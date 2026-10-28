"""The tools an agent gets, defined once as data.

Every harness exposes these to its model in its own way: the Pi extension registers them
as Pi tools, the MCP server (``orchestra_agents.harness.mcp``) serves them to Claude Code and any
other MCP client. Each tool maps to an ``orchestra_agents.tooling`` command. Which tools an agent
gets is decided by tool *groups* (``RoleDef.tools``), never per tool."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..protocol import STATES as _STATES

STATES = ", ".join(_STATES)


@dataclass(frozen=True)
class ToolDef:
    name: str                                         # also the ``orchestra_agents.tooling`` command
    group: str
    description: str
    properties: dict = field(default_factory=dict)   # JSON schema properties
    required: tuple[str, ...] = ()

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": dict(self.properties), "required": list(self.required),
                "additionalProperties": False}

    def to_dict(self) -> dict:
        return {"name": self.name, "group": self.group, "description": self.description, "inputSchema": self.input_schema}


def S(d: str) -> dict:
    return {"type": "string", "description": d}


def I(d: str = "") -> dict:
    return {"type": "integer", "description": d} if d else {"type": "integer"}


TARGET = {"type": "string", "enum": ["repo", "overlay"],
          "description": "repo (default): orchestra.toml, the project's settings, committed; overlay: the owner's "
                         "private file, for their own or their company's settings (identities, channels, templates), "
                         "never committed"}


def A(d: str = "") -> dict:
    s = {"type": "array", "items": {"type": "string"}}
    return {**s, "description": d} if d else s


TOOLS: list[ToolDef] = [
    ToolDef("report_ticket", "report_ticket",
            "Report the outcome of your work on this ticket. Call exactly once when you are finished. "
            "READY_FOR_REVIEW means your result is ready for a reviewer (committed on your branch, or in RESULT.md). "
            "BLOCKED means you cannot proceed; blocked_reason is required and must say what you need.",
            {"state": {"type": "string", "enum": ["READY_FOR_REVIEW", "BLOCKED"]},
             "summary": S("What you changed and why, in a few sentences"),
             "blocked_reason": S("Required when state is BLOCKED")}, ("state", "summary")),
    ToolDef("report_review", "report_review",
            "Report your review verdict. Call exactly once when you are finished reading the result. "
            "APPROVED means it is correct, complete against the acceptance criteria, and consistent with "
            "the repository. REJECTED must include specific, actionable feedback the worker can act on.",
            {"verdict": {"type": "string", "enum": ["APPROVED", "REJECTED"]},
             "summary": S("Short summary of the review"),
             "feedback": S("Required when REJECTED: concrete problems, with file and line where possible")},
            ("verdict", "summary")),
    # tickets
    ToolDef("project_status", "tickets", "Counts of tickets by state, milestones, capacity, pipeline state."),
    ToolDef("list_milestones", "tickets", "List all milestones with id, name, status, and ticket counts."),
    ToolDef("create_milestone", "tickets", "Create a milestone to group tickets. Returns its id (M1, M2, ...).",
            {"name": S("Short name"), "description": S("What this milestone is for")}, ("name",)),
    ToolDef("update_milestone", "tickets", "Rename or re-describe a milestone.",
            {"id": S("Milestone id"), "name": S("New name"), "description": S("New description"), "order": I()}, ("id",)),
    ToolDef("close_milestone", "tickets",
            "Close a milestone. Only allowed when every ticket in it is DONE or CANCELLED. Irreversible.",
            {"id": S("Milestone id")}, ("id",)),
    ToolDef("list_tickets", "tickets", "List tickets, optionally filtered by state or milestone.",
            {"state": S(f"One of {STATES}"), "milestone_id": S("Milestone id")}),
    ToolDef("get_ticket", "tickets", "Full detail of one ticket including its notes.", {"id": S("Ticket id like ORC-12")}, ("id",)),
    ToolDef("create_ticket", "tickets",
            "Create a ticket in an open milestone. objective says why (which project objective it serves); "
            "description is a self-contained brief a worker can do without asking; acceptance is what a "
            "reviewer checks in the result; verification is how it is proved: the merge gate's tests for code "
            "(including any test that must be added), what the reviewer checks for a task.",
            {"title": S("Short imperative title"),
             "objective": S("Why this ticket exists and which objective it serves"),
             "description": S("Self-contained implementation brief"),
             "acceptance": S("Acceptance criteria, one per line"),
             "verification": S("How it is proved: existing acceptance test(s) or the test to add; for a task, what the reviewer checks"),
             "milestone_id": S("Milestone id, e.g. M1"),
             "priority": I("1 (first) .. 9 (last); default 5"),
             "depends_on": A("Ticket ids that must be DONE first")},
            ("title", "objective", "description", "acceptance", "verification", "milestone_id")),
    ToolDef("update_ticket", "tickets",
            "Edit a ticket's title, description, acceptance, verification, objective, priority, milestone, or dependencies.",
            {"id": S("Ticket id"), "title": S("New title"), "description": S("New description"),
             "acceptance": S("New acceptance criteria"), "verification": S("New verification"),
             "objective": S("New objective (supervisor only)"), "priority": I(),
             "milestone_id": S("Move to this open milestone"), "depends_on": A()}, ("id",)),
    ToolDef("add_note", "tickets", "Append a note to a ticket.", {"id": S("Ticket id"), "text": S("Note text")}, ("id", "text")),
    ToolDef("cancel_ticket", "tickets", "Cancel a ticket that should not be done.", {"id": S("Ticket id"), "reason": S("Why")}, ("id", "reason")),
    ToolDef("unblock_ticket", "tickets",
            "Move a BLOCKED ticket back to OPEN after resolving its blocker. Explain the resolution in note.",
            {"id": S("Ticket id"), "note": S("How the blocker was resolved")}, ("id", "note")),
    # context
    ToolDef("get_project_context", "context",
            "Read the project context document (objectives, value propositions, non-goals, verification)."),
    ToolDef("update_project_context", "context", "Replace the project context document with the full new markdown text.",
            {"text": S("The complete document in markdown")}, ("text",)),
    # pipeline
    ToolDef("get_gate_run", "pipeline", "Detail and logs of one merge gate run.", {"id": S("Gate run id")}, ("id",)),
    ToolDef("list_gate_runs", "pipeline", "Recent merge gate runs."),
    ToolDef("get_staging_status", "pipeline", "Status and recent log of the staging environment."),
    ToolDef("resume_pipeline", "pipeline", "Resume the merge pipeline after a base-branch failure has been fixed.",
            {"note": S("What was fixed")}, ("note",)),
    # capacity
    ToolDef("get_capacity", "capacity", "Current worker/reviewer capacity: the runtime dial and the configured maximum."),
    ToolDef("set_capacity", "capacity",
            "Set how many workers and reviewers may run at once, between 0 and the configured maximum. "
            "Use 0 to pause pickup while re-planning; raise it again afterwards.",
            {"workers": I(), "reviewers": I(), "note": S("Why")}, ("note",)),
    # events
    ToolDef("emit_event", "events",
            "Raise an event that other agents may be subscribed to: a fact with no addressee, like flaky_test_suspected "
            "or lesson_proposed. The type must be one your team declares in `emits`. Use message_agent for something "
            "aimed at one agent, escalate_to_supervisor for a decision. Rate-limited; refused when the cause chain is deep.",
            {"type": S("Declared event type, lowercase_name"), "subject": S("What it is about, e.g. a ticket id or test name"),
             "note": S("One or two sentences")}, ("type",)),
    ToolDef("read_events", "events",
            "Recent events on the bus (tickets, assignments, gate runs, staging, agents). Use to look back over what happened.",
            {"minutes": I("How far back, default 60"), "types": A("Event types to include; empty for all")}),
    # team: reading the organisation, and talking down or up the tree (leads and the supervisor)
    ToolDef("list_teams", "team", "All teams: lead, agents, sub-teams, access, paused state."),
    ToolDef("get_team", "team", "One team in full: responsibilities (own and inherited), agents, access, space.",
            {"name": S("Team name, e.g. product or product/qa")}, ("name",)),
    ToolDef("list_agents", "team", "All agents: roles, team, kind, schedule or wake events, paused state."),
    ToolDef("get_agent", "team", "One agent in full: its roles, team chain, effective tools, and the prompt it is started with.",
            {"name": S("Agent name")}, ("name",)),
    ToolDef("list_roles", "team", "All roles (job descriptions), built-in and custom, and which agents use each."),
    ToolDef("get_role", "team", "One role's definition and prompt.", {"name": S("Role name")}, ("name",)),
    ToolDef("run_agent", "team", "Invoke one of your team's ad_hoc agents once with a brief. It wakes at the next tick, "
            "does the brief, and closes. Only a lead (or the supervisor) may do this, and only for its own team.",
            {"name": S("Agent name"), "brief": S("What to do, self-contained")}, ("name", "brief")),
    ToolDef("message_agent", "team", "Leave a note for an agent's next wake-up: a lead to its team or its sub-teams' leads, "
            "an agent to its own lead. Anything else is refused; escalate to the supervisor instead.",
            {"name": S("Agent name"), "note": S("The message")}, ("name", "note")),
    # roles: designing the organisation (supervisor only)
    ToolDef("org_guide", "roles", "The method for designing roles, agents, and teams. Call this first whenever the human "
            "asks for a new team, a change to a team, a new role, or a new scheduled agent; then follow it."),
    ToolDef("create_role", "roles",
            "Create a role: a job description in the second person (what this kind of agent is good at, how it works, "
            "what it never does, how it reports). No schedule, no team: those belong to agents and teams.",
            {"name": S("lowercase_with_underscores"), "description": S("One line: what this kind of agent is good at"),
             "prompt": S("The job description in markdown, under sixty lines"),
             "tools": A("Tool groups the role needs: tickets, context, pipeline, capacity, escalate, events")},
            ("name", "description", "prompt")),
    ToolDef("update_role", "roles", "Change a custom role's description, tools, or prompt.",
            {"name": S("Role name"), "description": S(""), "prompt": S("New full prompt"), "tools": A()}, ("name",)),
    ToolDef("delete_role", "roles", "Delete a custom role no agent uses.", {"name": S("Role name")}, ("name",)),
    ToolDef("create_agent", "roles",
            "Create an agent: an instance of one or more roles in a team. kind is continuous (a process that is always "
            "up, session resident, woken by wake_on events and messages), scheduled (every '6h' or at 'HH:MM'; session "
            "resident or ephemeral), or ad_hoc (a fresh session each time it is invoked, by its lead with run_agent or by "
            "a wake_on event). The team may be one you create next.",
            {"name": S("lowercase_with_underscores"), "roles": A("Role names, at least one"), "team": S("Team name, or empty for none"),
             "kind": S("continuous | scheduled | ad_hoc"), "session": S("resident | ephemeral"),
             "wake_on": A("Event types, optionally 'ticket.transitioned:DONE'"), "every": S("e.g. 30m, 6h, 1d"), "at": S("HH:MM"),
             "description": S("One line: what this agent is for")},
            ("name", "roles", "kind")),
    ToolDef("update_agent", "roles", "Change a custom agent's roles, team, kind, schedule, or session policy.",
            {"name": S("Agent name"), "roles": A(), "team": S(""), "kind": S(""), "session": S(""), "wake_on": A(),
             "every": S(""), "at": S(""), "description": S("")}, ("name",)),
    ToolDef("delete_agent", "roles", "Delete a custom agent; its sessions are archived.", {"name": S("Agent name")}, ("name",)),
    ToolDef("pause_agent", "roles", "Pause an agent: wake-ups queue until resumed.", {"name": S("Agent name")}, ("name",)),
    ToolDef("resume_agent", "roles", "Resume a paused agent.", {"name": S("Agent name")}, ("name",)),
    ToolDef("restart_agent", "roles", "Restart an agent's session (fresh process; transcript kept).", {"name": S("Agent name")}, ("name",)),
    ToolDef("reset_agent", "roles", "Start a resident agent over with an empty transcript (the old one is archived as a run). Use when "
            "a session has grown so long that every turn is slow; the agent loses its memory of the conversation but "
            "not the project context or the team's space.", {"name": S("Agent name")}, ("name",)),
    ToolDef("create_team", "roles",
            "Create a team: one lead agent, members, responsibilities (standing conditions as a numbered markdown list), "
            "access (tool groups, which decide the tools its agents get, and repository paths, which are written into "
            "their prompts as instructions and are not enforced). Nest with 'parent/child'. Prefer update_team on an "
            "existing team; a project rarely needs more than four or five.",
            {"name": S("Team name, e.g. product or product/qa"), "description": S("One line"), "lead": S("Lead agent name"),
             "members": A("Member agent names"), "responsibilities": S("Markdown: what this team keeps true"),
             "access": {"type": "array", "items": {"type": "object"},
                        "description": "Entries like {\"tools\": [\"tickets\", \"events\"]} or {\"path\": \"src\", \"access\": \"read\"}; "
                                       "a path entry is an instruction in the agents' prompts, not a permission check"},
             "tracked": {"type": "boolean", "description": "Commit the team's space with the code (default false: ignored by git)"},
             "emits": A("Event types this team's agents may raise with emit_event, e.g. lesson_proposed"),
             "probes": {"type": "array", "items": {"type": "object"},
                        "description": "Conditions Orchestra checks on an interval, run in the team's space: "
                                       "[{\"name\": \"docs_budget\", \"command\": \"tools/doc-budget\", \"every\": \"30m\"}]. "
                                       "Exit 0 = holds; probe.failed:<name> / probe.recovered:<name> fire on the transition."}},
            ("name", "lead", "responsibilities")),
    ToolDef("update_team", "roles", "Change a team: lead, members, responsibilities, access, tracking.",
            {"name": S("Team name"), "description": S(""), "lead": S(""), "members": A(), "responsibilities": S("Full new markdown"),
             "access": {"type": "array", "items": {"type": "object"}}, "tracked": {"type": "boolean"},
             "emits": A(), "probes": {"type": "array", "items": {"type": "object"}}}, ("name",)),
    ToolDef("delete_team", "roles", "Delete a custom team that has no agents and no sub-teams.", {"name": S("Team name")}, ("name",)),
    # observe: the whole system, read only (the Supervisor looks before it concludes)
    ToolDef("check_setup", "observe",
            "Whether the system can do what is asked: which machines fill the worker and reviewer roles (AI agents, programs), "
            "the kind of work (tasks or code), the gate's tests, the capacity limit, what Orchestra runs and how each "
            "collector's last round went. Call it first when a ticket blocks or keeps being rejected, or a request is not met."),
    ToolDef("read_config", "observe", "The project's orchestra.toml as it is, whether it can be changed now, and its recent commits; "
            "whether the owner has a private overlay, and each key it sets (secret values left out)."),
    ToolDef("list_processes", "observe",
            "The programs Orchestra's launcher keeps running (its runtime record: state, pid, restarts, last exit code, "
            "log) and the collectors orchestra.toml declares, with the last line of each one's log."),
    ToolDef("read_machine_log", "observe",
            "The end of a machine's log: a program's or a collector's (by its name in orchestra.toml or the process "
            "list, e.g. shell_worker, tasks.scheduler), orchestra-host for Orchestra's own, or the log a machine announced.",
            {"name": S("Machine or process name"), "lines": I("How many lines from the end, default 80")}, ("name",)),
    ToolDef("list_metrics", "observe",
            "The metrics catalogue: every series name in the project's own store, its labels, when it was last written "
            "and how many points in the last hour. A measurement that is not here is not being stored.",
            {"prefix": S("Only names starting with this")}),
    ToolDef("get_model", "observe",
            "The system dynamics model Orchestra discovered from the metrics: stocks, flows with their equations and the "
            "machines attributed to them, and the series the last discovery left out, with why."),
    ToolDef("snapshot", "observe",
            "The task system's whole picture in one answer: tickets with why each waits, milestones, capacity, the "
            "pipeline, and which machine instance holds each assignment."),
    # setup: the configuration, changed by the Supervisor only, through Orchestra: in orchestra.toml
    # (target repo, each change a commit with its reason) or the owner's private overlay (target overlay)
    ToolDef("set_setting", "setup",
            "Set one key of the configuration (section.name, as desk.channels or slack.me), or remove it with value "
            "null. Say where: target overlay for the owner's own or their company's settings (identities, channels, "
            "templates, anything personal; never committed), repo for the project's settings (committed).",
            {"key": S("section.name"),
             "value": S('The value as JSON: "#ops", 3, true, ["#a", "@me"], {"k": "v"}; null removes the key'),
             "target": TARGET, "reason": S("Why, in a sentence")}, ("key", "target", "reason")),
    ToolDef("set_agent_roles", "setup",
            "Set which of the task system's roles the AI agents fill ([agents] serves). A role left out is left to other "
            "machines (a program worker): leave a role out only when one fills it.",
            {"serves": A("worker, reviewer, both, or none"), "target": TARGET, "reason": S("Why, in a sentence: it goes in the commit")},
            ("serves", "reason")),
    ToolDef("set_work_mode", "setup",
            "Set the kind of work ([project] work): code (a branch per ticket, reviewed, landed on the base branch by the "
            "merge gate; needed for anything that must end up in the repository, like a collector) or tasks (a scratch "
            "folder per ticket, nothing merged).",
            {"work": {"type": "string", "enum": ["tasks", "code"]}, "target": TARGET, "reason": S("Why, in a sentence")}, ("work", "reason")),
    ToolDef("set_gate_unit", "setup",
            "Add or change a test the merge gate runs on every branch before it lands ([[gate.unit]]): a shell command, "
            "run in the branch's checkout, that must exit 0.",
            {"name": S("The test's name, default unit"), "command": S("The shell command"),
             "timeout": S("How long it may take, e.g. 300s"), "target": TARGET, "reason": S("Why, in a sentence")}, ("command", "reason")),
    ToolDef("set_capacity_limit", "setup",
            "Set the most tickets offered at once to workers and to reviewers ([tasks] workers, reviewers): the ceiling "
            "under which the Coordinator's set_capacity dial moves.",
            {"workers": I(), "reviewers": I(), "target": TARGET, "reason": S("Why, in a sentence")}, ("reason",)),
    ToolDef("set_machine", "setup",
            "Add or change a program Orchestra keeps running ([[machines]]): a command in the repository. Orchestra starts "
            "a new one within a second and restarts one whose command changed. The program itself is a worker's to write.",
            {"name": S("letters, digits, . _ -"), "command": S("The command, e.g. python machines/feed.py"),
             "restart": S("always (default), on-failure or never"), "stop_grace": I("Seconds from SIGTERM to SIGKILL"),
             "target": TARGET, "reason": S("Why, in a sentence")}, ("name", "command", "reason")),
    ToolDef("set_collector", "setup",
            "Add or change a collector: a command under machines/ that prints Prometheus text ([[collect]]), or a url "
            "that serves it ([[scrape]]). Orchestra runs it on its interval and stores what it prints. The script itself "
            "is a worker's to write.",
            {"name": S("The machine's id"), "command": S("The script, e.g. machines/disk.sh"), "url": S("Or a URL"),
             "every": S("How often, e.g. 60s"), "timeout": S("For a round that may take more than 10 s"),
             "target": TARGET, "reason": S("Why, in a sentence")}, ("name", "reason")),
    ToolDef("remove_entry", "setup",
            "Remove a [[machines]], [[collect]], [[scrape]] or [[gate.unit]] entry by name. A removed machine is stopped.",
            {"section": {"type": "string", "enum": ["machines", "collect", "scrape", "gate.unit"]}, "name": S("Entry name"),
             "target": TARGET, "reason": S("Why, in a sentence")}, ("section", "name", "reason")),
    ToolDef("restart_machine", "setup",
            "Restart one of the project's programs ([[machines]]), including one Orchestra gave up on: its entry is taken "
            "out until Orchestra has stopped it, then put back as it was, which starts it again. Nothing is committed.",
            {"name": S("The [[machines]] entry's name"), "reason": S("Why, in a sentence")}, ("name", "reason")),
    # escalate
    ToolDef("escalate_to_supervisor", "escalate",
            "Wake the Supervisor with a note when a decision about intent, scope, or priorities is needed.",
            {"note": S("What needs deciding and why")}, ("note",)),
]

BY_NAME = {t.name: t for t in TOOLS}


def tools_for(groups: list[str] | tuple[str, ...] | set[str]) -> list[ToolDef]:
    g = set(groups)
    return [t for t in TOOLS if t.group in g]
