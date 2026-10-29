"""Role prompts and per-turn prompts: what an agent is told when it starts, when it is given a
ticket, and when it is woken. A ticket here is what the task system said about it (an
assignment's brief, or a query's answer), read through ``TicketView``."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import ClassVar

from .config import Project
from .org import EVENT_TYPES, team_chain, tools_path
from .org_store import RepoOrgStore, all_teams
from .protocol import TERMINAL
from .roles import TOOL_GROUPS, RepoRoleStore, all_roles


class TicketView:
    """A ticket as the task system described it, with the fields the prompts read."""

    FIELDS: ClassVar[dict[str, object]] = {"id": "", "title": "", "priority": 5, "depends_on": [], "objective": "", "description": "", "acceptance": "",
              "verification": "", "feedback": None, "rejected_by": None, "review_rounds": 0, "branch": None}

    def __init__(self, d: dict):
        for k, v in self.FIELDS.items():
            val = d.get(k, v)
            setattr(self, k, v if val is None and v is not None else val)
        self.depends_on = list(self.depends_on or [])
        for k in ("objective", "description", "acceptance", "verification", "title"):
            setattr(self, k, getattr(self, k) or "")


PROMPTS_DIR = Path(__file__).parent / "prompts"


def prompt_path(project: Project, role: str) -> Path | None:
    """The file a built-in role's prompt is read from: the project's override, else the variant
    for the project's kind of work (``worker.tasks.md``: no git, no merge gate), else
    ``<role>.md``; None for a custom role, whose prompt is in ``.orchestra/roles/``."""
    override = project.config.prompt_overrides.get(role)
    if override:
        return project.repo_root / override
    for p in (PROMPTS_DIR / f"{role}.{project.config.work}.md", PROMPTS_DIR / f"{role}.md"):
        if p.exists():
            return p
    return None


def role_prompt(project: Project, role: str) -> str:
    """A role's own prompt, placeholders resolved: the job description. Built-in roles ship with
    this package (overridable per project); custom roles come from ``.orchestra/roles/``."""
    path = prompt_path(project, role)
    if path is not None and path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = RepoRoleStore(project).prompt(role)
        if not text.strip():
            text = f"You are good at being the {role}. Act on the wake-up you receive using your tools, then summarize."
    return _fill(project, text)


# Every session's last word (host._open_session): Claude Code once found no Orchestra tools and "created" a ticket in its own todo list.
NO_SUBSTITUTE = ("If Orchestra's tools (tickets, the project, the Supervisor's and your role's tools; tools of this session, "
                 "or the command this prompt gives to run them from your shell) are missing or do not run, say so in one line and stop: never do the work some other way, and never use another task, todo "
                 "or schedule list in their place; nothing outside Orchestra's tools is seen or done by anyone.")


MACHINES_DOC_URL = "https://github.com/danroblewis/orchestra/blob/main/docs/machines.md"


def machines_doc() -> str:
    """Where Orchestra's guide to writing a machine (its series, its declaration for the launcher)
    is: the file in Orchestra's checkout when it is installed from one, else the published copy."""
    import orchestra
    local = Path(orchestra.__file__).resolve().parents[1] / "docs" / "machines.md"
    return str(local) if local.exists() else MACHINES_DOC_URL


def _fill(project: Project, text: str) -> str:
    """Placeholders: ``{operator}`` (the Supervisor's part shared by both kinds of work: seeing the
    system and fixing its setup, prompts/operator.md), then the project's settings."""
    if "{operator}" in text:
        text = text.replace("{operator}", (PROMPTS_DIR / "operator.md").read_text(encoding="utf-8").strip())
    return (text.replace("{base_branch}", project.config.base_branch).replace("{ticket_prefix}", project.config.ticket_prefix)
            .replace("{machines_doc}", machines_doc()))


def agent_prompt(project: Project, agent, roles: dict | None = None, teams: dict | None = None) -> str:
    """The system prompt an agent is started with: its roles' prompts, then its team's
    responsibilities and those of the teams above it, then where it works (its space, its tools
    and its teams' path grants, which are instructions here and enforced nowhere). Built-in
    agents outside a custom team get exactly their role prompt."""
    return _core(agent) + _agent_prompt(project, agent, roles, teams) + _spec(project, agent)


def _core(agent) -> str:
    """The Supervisor's fixed core (prompts/supervisor_core.md), always first: it ships with the
    plugin, so nothing the Supervisor writes in its spec can remove its guardrails."""
    if agent.name != "supervisor":
        return ""
    return (PROMPTS_DIR / "supervisor_core.md").read_text(encoding="utf-8").strip() + "\n\n---\n\n"


def _spec(project: Project, agent) -> str:
    """What the agent's machine spec adds (``.orchestra/machines/<name>/``): its loop and duties."""
    from .machine_spec import load, prompt_part
    try:
        spec = load(project, agent.name)
    except Exception:  # noqa: BLE001 - a spec that cannot be read leaves the prompt as the record says
        return ""
    return "\n" + _fill(project, prompt_part(spec)) if spec is not None else ""


def _agent_prompt(project: Project, agent, roles: dict | None = None, teams: dict | None = None) -> str:
    roles = roles if roles is not None else all_roles(project)
    teams = teams if teams is not None else all_teams(project)
    chain = team_chain(teams, agent.team)
    if agent.builtin and all(t.builtin for t in chain):
        return role_prompt(project, agent.roles[0])
    team = chain[0] if chain else None
    lead_of = [t.name for t in teams.values() if t.lead == agent.name]
    if team is None and agent.team:
        team_line = (f"You are assigned to the team `{agent.team}`, which has not been created yet; until it is, the "
                     "Supervisor is your lead and you have no team responsibilities.")
        reach_line = "The human, and decisions above you, are reached only through `escalate_to_supervisor`."
    elif team is None:
        team_line = "You belong to no team; you report to the Supervisor."
        reach_line = "The human, and decisions above you, are reached only through `escalate_to_supervisor`."
    elif team.name in lead_of:
        members = [a for a in team.agents if a != agent.name]
        parent_lead = teams[team.parent].lead if team.parent and team.parent in teams else None
        team_line = f"You belong to the team `{team.name}`, which you lead."
        reach_line = ("Your members" + (f" ({', '.join(f'`{m}`' for m in members)})" if members else "") +
                      " are reached with `message_agent`, and ad hoc members are briefed with `run_agent`. " +
                      (f"You report to `{parent_lead}`, the lead of the team above yours, with `message_agent`; "
                       if parent_lead else "You have no lead above you except the Supervisor; ") +
                      "the human, and decisions you cannot make, are reached only through `escalate_to_supervisor`.")
    else:
        team_line = f"You belong to the team `{team.name}`, led by `{team.lead}`."
        reach_line = (f"Your lead `{team.lead}` is reached with `message_agent`; the human, and decisions above your lead, "
                      "only through `escalate_to_supervisor`.")
    parts = [AGENT_PREAMBLE.format(agent=agent.name, roles=", ".join(f"`{r}`" for r in agent.roles), team_line=team_line,
                                   reach_line=reach_line)]
    for r in agent.roles:
        parts.append(f"## Your role: {r}\n\n{role_prompt(project, r).strip()}\n")
    for i, t in enumerate(chain):
        resp = "" if t.builtin else RepoOrgStore(project).responsibilities(t.name).strip()
        rel = "your team" if i == 0 else f"the team above `{chain[i - 1].name}`"
        who = f"Lead: `{t.lead}`. Agents: {', '.join(f'`{a}`' for a in t.agents) or 'none'}."
        subs = [x.name for x in teams.values() if x.parent == t.name]
        if subs:
            who += f" Sub-teams: {', '.join(f'`{x}`' for x in subs)}."
        body = _fill(project, resp) if resp else "(none written yet)"
        parts.append(f"## Team `{t.name}` ({rel})\n\n{t.description}\n{who}\n\n### Responsibilities of `{t.name}`\n\n{body}\n")
    if team:
        tools_dirs = ", ".join(f"`{p.relative_to(project.repo_root)}`" for p in tools_path(project, teams, agent))
        lines = [f"- Working directory and space: `{team.space}` (yours to organise; keep it accurate and small)",
                 f"- Tools on your PATH: {tools_dirs}"]
        for t in chain:
            for g in t.paths:
                lines.append(f"- {g['access']}: `{g['path']}`" + (f" (granted to team `{t.name}`)" if t.name != team.name else ""))
        lines.append("- Treat everything else in the repository as read-only. Changes to the product happen only through tickets.")
        parts.append("## Where you work\n\n" + "\n".join(lines) + "\n")
    return "\n".join(parts)


def org_guide() -> str:
    """The Supervisor's guide to designing roles, agents, and teams; loaded on demand."""
    return (PROMPTS_DIR / "org_guide.md").read_text(encoding="utf-8")


def modeling_guide() -> str:
    """The modeling skill (prompts/modeling.md): what the model's words mean, how to test a change
    on it and commit a prediction, and the equation language as Orchestra defines it now; loaded
    on demand, with the ``modeling_guide`` tool."""
    return (PROMPTS_DIR / "modeling.md").read_text(encoding="utf-8").replace("{language}", language_text())


def language_text() -> str:
    """Orchestra's equation language (``GET /language``), as text."""
    from orchestra.sd.experiments.language import reference

    r = reference()
    arity = lambda lo, hi: f"{lo} or more" if hi is None else str(lo) if lo == hi else f"{lo} to {hi}"
    lines = [f"- Syntax: {r['syntax']}.", f"- Names: {r['names']}.", f"- Rates: {r['rate']}.", f"- Units: {r['units']}.",
             "- Limits: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in (r.get("limits") or {}).items()) + ".",
             "- Functions (how many arguments): " + "; ".join(f"`{name}` ({arity(*f['arguments'])}) {f['meaning']}"
                                                              for name, f in (r.get("functions") or {}).items()) + "."]
    return "\n".join(lines)


VERDICT_TURN = """[A verdict on your prediction]
{reason}

Your ticket's work is over: this turn is only about the verdict. Read it with `read_predictions`
(the prediction's id above) and, if it helps, the experiment with `read_experiment`. Do not change
any file. Reply with what the verdict means for the change you made, in two or three sentences:
whether it did what you predicted, and what the model got wrong if it did not.
"""


def verdict_task(reason: str) -> str:
    return VERDICT_TURN.format(reason=reason)


CONTEXT_TEMPLATE = """# Project context

_Maintained by the Supervisor. Every Supervisor turn starts with this document._

## Objectives
(What this project is trying to achieve, in priority order.)

## Value propositions
(Who benefits and how.)

## Non-goals
(What we deliberately will not do.)

## Definition of done
A ticket is DONE when its review is approved and, for code, its branch has landed on the base
branch through the merge gate.

## Verification
(Which acceptance tests exist, what they cover, and what a new feature must add.)
"""


def context_path(project: Project) -> Path:
    return project.state_dir / "PROJECT.md"


def read_context(project: Project) -> str:
    p = context_path(project)
    if not p.exists():
        seed = project.repo_root / ".orchestra" / "PROJECT.md"
        p.write_text(seed.read_text(encoding="utf-8") if seed.exists() else CONTEXT_TEMPLATE, encoding="utf-8")
    return p.read_text(encoding="utf-8")


def write_context(project: Project, text: str) -> None:
    context_path(project).write_text(text, encoding="utf-8")


AGENT_PREAMBLE = """# You are the `{agent}` agent of Orchestra

You are an agent in a software project run by Orchestra, with the role {roles}. {team_line}
Orchestra wakes you with a message that says why (an event, a schedule, or a note or brief from
another agent) and gives you a snapshot of the project. Act using your tools, then reply with a
short summary of what you did and why. You never write code on the base branch and never start
agents yourself; code changes happen by creating or editing tickets, which the Coordinator
orders and workers implement. {reach_line}
"""


def _ticket_block(t: TicketView) -> str:
    deps = ", ".join(t.depends_on) or "none"
    return (f"Ticket: {t.id}\nTitle: {t.title}\nPriority: {t.priority}\nDepends on: {deps}\n\n"
            f"## Objective (why)\n{t.objective.strip() or '(not stated)'}\n\n"
            f"## Description\n{t.description.strip() or '(none)'}\n\n"
            f"## Acceptance criteria\n{t.acceptance.strip() or '(none given; use the description)'}\n\n"
            f"## Verification (how it is proved)\n{t.verification.strip() or '(none stated; existing tests must keep passing)'}\n")


def worker_task(project: Project, t: TicketView, unit_command: str | None) -> str:
    test_line = f"Run the unit tests with: `{unit_command}`" if unit_command else "There is no configured unit test command."
    head = f"# Your assignment\n\n{_ticket_block(t)}\n"
    env = (f"## Environment\nYou are in a git worktree checked out on branch `{t.branch}`. "
           f"The base branch is `{project.config.base_branch}`. Only this branch is yours. "
           f"{test_line}\n\n")
    if t.feedback and t.rejected_by:
        return (head + env + f"## This ticket was REJECTED by the {t.rejected_by} (round {t.review_rounds})\n"
                f"Address every point below, commit, re-run the tests, then call `report_ticket`.\n\n"
                f"{t.feedback.strip()}\n")
    if t.feedback:
        return head + env + f"## Note from the planner\n{t.feedback.strip()}\n\nImplement the ticket, then call `report_ticket`.\n"
    return (head + env + "Implement the ticket. If the verification section names a test to add or extend, "
            "that test is part of the work. Verify, commit on your branch, then call `report_ticket`.\n")


def worker_task_tasks(project: Project, t: TicketView) -> str:
    """A task (not a code change): the worker works in its own scratch folder and reports a result."""
    head = f"# Your assignment\n\n{_ticket_block(t)}\n"
    env = ("## Environment\nYour working directory is this ticket's scratch folder. Put your result in "
           "`RESULT.md` there, with any supporting files beside it. The repository, when the task is about it, "
           f"is at $ORCHESTRA_REPO ({project.repo_root}): read it there; do not change it.\n\n")
    if t.feedback and t.rejected_by:
        return (head + env + f"## This ticket was REJECTED by the {t.rejected_by} (round {t.review_rounds})\n"
                f"Address every point below, update `RESULT.md`, then call `report_ticket`.\n\n{t.feedback.strip()}\n")
    if t.feedback:
        return head + env + f"## Note from the planner\n{t.feedback.strip()}\n\nDo the task, then call `report_ticket`.\n"
    return head + env + "Do the task, write `RESULT.md`, then call `report_ticket`.\n"


def reviewer_task_tasks(t: TicketView, summary: str, files: list[str], result: str) -> str:
    """A finished task to review: the ticket, the worker's summary, its RESULT.md and its files."""
    result = result if len(result) < 60000 else result[:60000] + "\n... (truncated; open RESULT.md for the rest)\n"
    return (f"# Review request\n\n{_ticket_block(t)}\n## The worker's summary\n{summary.strip() or '(none)'}\n\n"
            f"## Files in the folder\n```\n" + ("\n".join(files) or "(none)") + "\n```\n\n"
            f"## RESULT.md\n{result.strip() or '(missing)'}\n\n"
            "Judge the result against the ticket, then call `report_review` exactly once.\n")


def reviewer_task(project: Project, t: TicketView, commit: str, diff: str, files: list[str],
                  log: str, test_output: str | None) -> str:
    diff = diff if len(diff) < 60000 else diff[:60000] + "\n... (diff truncated; open files in the worktree for the rest)\n"
    tests = f"## Unit test output (the worker's run on this commit; the merge gate runs every test again before landing)\n```\n{test_output.strip()[-4000:]}\n```\n" if test_output else \
        "## Unit tests\nNo unit test command is configured.\n"
    return (f"# Review request\n\n{_ticket_block(t)}\n"
            f"## Commit under review\n{commit}\n\n## Commits on the branch\n```\n{log.strip()}\n```\n\n"
            f"## Changed files\n```\n" + "\n".join(files) + "\n```\n\n"
            f"{tests}\n## Diff against `{project.config.base_branch}`\n```diff\n{diff}\n```\n\n"
            "Review it like a pull request. Check that the verification named in the ticket was actually "
            "added or extended. Then call `report_review` exactly once.\n")


def org_reference() -> str:
    """What an agent can watch and do, appended to the design guide."""
    return ("Tool groups: " + ", ".join(TOOL_GROUPS) + ".\nEvent types that can wake an agent: " + ", ".join(EVENT_TYPES)
            + ".\nA wake_on entry may filter: 'task.reported:BLOCKED' (a worker's turn ended blocked), "
              "'task.attention_needed:coordinator' (a planner is asked to decide), 'request.done:CANCELLED' (a ticket was cancelled), "
              "'probe.failed:<probe>'. A ticket is done when 'gate.landed' (code) or, in tasks mode, 'review.approved'. "
              "The task system publishes no ticket state changes: ask it (get_ticket, list_tickets) for a ticket's state.")


WAKE_TICKETS = 40          # open tickets listed in a wake-up; the rest are a count, and `list_tickets` has them


def wake_task(target: str, reasons: list[str], snapshot: dict, roles: list[str] | None = None) -> str:
    counts = ", ".join(f"{k}={v}" for k, v in snapshot["counts"].items() if v)
    lines = [f"# Wake-up for the {target.replace('_', ' ')}", "", "## Why you were woken"]
    shown = reasons[:12]
    lines += [f"- {r}" for r in shown]
    if len(reasons) > len(shown):
        lines.append(f"- ...and {len(reasons) - len(shown)} more of the same kinds; the snapshot below is current")
    if snapshot.get("unavailable"):
        lines += ["", (f"The task system did not answer: {snapshot['unavailable']}. The snapshot below is empty for that "
                       "reason, not because the project is empty; do not act on ticket counts this turn.")]
    else:
        lines += ["", "## Execution health"] + execution_health(snapshot)
    if roles:
        lines += ["", "## Who fills the roles"] + roles
    lines += ["", "## Project snapshot", f"Ticket counts: {counts or 'none'}",
              f"Pipeline: {'PAUSED: ' + str(snapshot['pipeline'].get('reason')) if snapshot['pipeline'].get('paused') else 'running'}",
              "", "Milestones:"]
    for m in snapshot["milestones"]:
        lines.append(f"- {m['id']} [{m['status']}] {m['name']}")
    lines += ["", "Tickets (id, state, priority, milestone, title):"]
    live = [t for t in snapshot["tickets"] if t["state"] not in TERMINAL]
    for t in live[:WAKE_TICKETS]:
        extra = f" BLOCKED: {t['blocked_reason']}" if t["state"] == "BLOCKED" else ""
        extra += f" (review rounds: {t['review_rounds']})" if t["review_rounds"] else ""
        obj = f" | objective: {t['objective'][:80]}" if t.get("objective") else ""
        lines.append(f"- {t['id']} {t['state']} p{t['priority']} {t['milestone_id']} {t['title']}{extra}{obj}")
    if len(live) > WAKE_TICKETS:
        lines.append(f"- ...and {len(live) - WAKE_TICKETS} more; `list_tickets` has them all")
    lines += ["", ("Use your tools to act, then report the way your role says to; if it does not say, reply with a few "
                   "lines on what you did and why.")]
    return "\n".join(lines)


def execution_health(snapshot: dict) -> list[str]:
    """Plain statements about whether work is flowing, with the likely cause when not. Who is
    working is read from the task system's assignments: whatever machine holds them."""
    cap = snapshot.get("capacity", {})
    held: dict[str, list[str]] = {}
    for a in snapshot.get("assignments", []):
        if a.get("instance"):
            held.setdefault(a.get("role") or "?", []).append(a["instance"])
    workers, reviewers = held.get("worker", []), held.get("reviewer", [])
    ready = [f"{t['id']}(p{(t.get('priority_info') or {}).get('effective', t['priority'])})"
             for t in snapshot["tickets"] if t["state"] == "OPEN" and t.get("ready")]
    waiting = [t for t in snapshot["tickets"] if t["state"] == "OPEN" and not t.get("ready")]
    blocked = [t["id"] for t in snapshot["tickets"] if t["state"] == "BLOCKED"]
    out = [f"Worker capacity: {cap.get('workers')} of a configured maximum {cap.get('max_workers')}"
           + (f" (dial note: {cap['note']})" if cap.get("note") else ""),
           f"Reviewer capacity: {cap.get('reviewers')} of {cap.get('max_reviewers')}",
           f"Workers running: {len(workers)} {workers or ''}".rstrip(), f"Reviewers running: {len(reviewers)} {reviewers or ''}".rstrip(),
           f"Ready tickets (OPEN, deps DONE, milestone open): {len(ready)} {ready[:8]}",
           f"OPEN but not ready: {len(waiting)}" + (f" e.g. {waiting[0]['id']}: {waiting[0].get('why_waiting')}" if waiting else ""),
           f"BLOCKED: {len(blocked)} {blocked[:8]}",
           f"Pipeline: {'PAUSED: ' + str(snapshot['pipeline'].get('reason')) if snapshot['pipeline'].get('paused') else 'running'}"]
    if ready and not workers:
        if cap.get("workers", 0) == 0 and cap.get("max_workers", 0) == 0:
            out.append("DIAGNOSIS: work is stalled because [tasks] workers = 0 in orchestra.toml. Only the human "
                       "can change that file: escalate_to_supervisor asking for it to be raised.")
        elif cap.get("workers", 0) == 0:
            out.append("DIAGNOSIS: work is stalled because the runtime worker capacity dial is 0. "
                       "Raise it with set_capacity if there is no reason to keep it paused.")
        else:
            out.append("DIAGNOSIS: tickets are ready and capacity exists but no worker is running; the task system "
                       "offers them within a tick. If this persists across check-ins, escalate_to_supervisor.")
    elif not ready and not workers and waiting:
        out.append("DIAGNOSIS: nothing is ready. Check whether the waiting tickets' dependencies or milestones are "
                   "wrong and fix them; unblock BLOCKED tickets you can resolve.")
    return out


# What in the event stream concerns the Supervisor enough to be named one by one in its digest:
# what it is asked to decide or handle (attention addressed to it, a blocked ticket, the gate, the
# staging deploy, a process Orchestra gave up on, an agent that keeps crashing). Everything else is
# only counted; a ticket of the human's that is done wakes it on its own (landings.py).
CONCERNS = ("gate.rejected", "gate.conflict", "gate.error", "gate.base_broken", "staging.failed",
            "pipeline.paused", "orchestra.process.gave_up", "agent.crash_loop")
DIGEST_EVENTS = 8          # at most this many named events
DIGEST_TYPES = 8           # at most this many event types counted by name


# In front of a Supervisor wake-up: the human is not there, and the reply is what they will read.
SUPERVISOR_WAKE = ("[Orchestra: a wake-up, not a message from the human, who is not present for this turn. Act with your "
                   "tools if you can. Your reply is left for the human as a message: write it to them, short and in your own "
                   "words, about what they need to know or decide; never quote this notice or the reasons below.]\n\n")

# Between what Orchestra tells the Supervisor before a chat turn and the human's message.
CHAT_SEAM = ("[Orchestra: the notices above are for you; the human has not seen them. Answer the human's message below. "
             "Mention a notice only when it bears on what they asked, as news in your own words, never as something they saw.]\n\n"
             "[The human's message]\n")


def chat_turn(context: str, message: str) -> str:
    """A chat turn's prompt: Orchestra's notices (supervisor_context), then the human's message,
    marked as theirs, so the reply answers the human and keeps the notices out of it. With no
    notices the message arrives as itself."""
    return context + CHAT_SEAM + message if context else message


def concerns_supervisor(e: dict) -> bool:
    t = e.get("type")
    report = e.get("report") if isinstance(e.get("report"), dict) else {}
    return (t in CONCERNS or (t == "task.attention_needed" and e.get("to") == "supervisor")
            or (t == "task.reported" and report.get("state") == "BLOCKED"))     # a worker's turn ended blocked


def supervisor_context(snapshot: dict, events: list[dict], project: Project | None = None, store=None,
                       roles: list[str] | None = None) -> str:
    """What Orchestra puts in front of a Supervisor turn: the project context document only
    when it has changed since the Supervisor last saw it (the session remembers earlier
    copies, and `get_project_context` fetches it any time), then a short digest of the events
    since its last turn: how many of each type, and the few that concern it (CONCERNS), at
    most DIGEST_EVENTS. Who fills the task system's roles (``roles``, fillers.py) comes first
    when it has changed since the Supervisor last saw it. The digest stays a few lines however busy the bus was, so a turn does
    not fill the session's context; the snapshot and `list_tickets` have the rest. A human's
    "yes, go ahead" arrives as itself."""
    out = ""
    if project is not None:
        text = read_context(project).strip()
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        seen = store.agent_state("supervisor").get("context_hash") if store is not None else None
        if digest != seen:
            out += "[Orchestra: project context" + (", updated" if seen else "") + "]\n" + text + "\n\n"
            if store is not None:
                store.set_agent_state("supervisor", context_hash=digest)
    if roles:
        said = hashlib.sha256("\n".join(roles).encode("utf-8")).hexdigest()[:16]
        if store is None or store.agent_state("supervisor").get("roles_hash") != said:
            out += "[Orchestra: who fills the roles]\n" + "\n".join(roles) + "\n\n"
            if store is not None:
                store.set_agent_state("supervisor", roles_hash=said)
    if not events:
        return out
    by_type: dict[str, int] = {}
    for e in events:
        by_type[str(e.get("type"))] = by_type.get(str(e.get("type")), 0) + 1
    common = sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))
    counted = ", ".join(f"{t}={n}" for t, n in common[:DIGEST_TYPES])
    rest = sum(n for _, n in common[DIGEST_TYPES:])
    counted += f", {rest} of {len(common) - DIGEST_TYPES} other types" if rest else ""
    lines = [f"[Orchestra: {len(events)} events since your last turn: {counted}]"]
    named = [e for e in events if concerns_supervisor(e)]
    if named:
        lines.append("Of these, for you" + (f" (the last {DIGEST_EVENTS} of {len(named)})" if len(named) > DIGEST_EVENTS else "") + ":")
    for e in named[-DIGEST_EVENTS:]:
        parts = [e["type"]]
        for k in ("ticket", "name", "from", "to", "reason", "by", "error"):
            if e.get(k):
                parts.append(f"{k}={e[k]}")
        lines.append("- " + " ".join(str(p) for p in parts)[:240])
    counts = ", ".join(f"{k}={v}" for k, v in snapshot["counts"].items() if v)
    if snapshot.get("unavailable"):
        lines.append(f"[Orchestra: the task system did not answer: {snapshot['unavailable']}; ticket counts unknown]")
    else:
        lines.append(f"[Orchestra: ticket counts now: {counts or 'none'}]")
    return out + "\n".join(lines) + "\n\n"
