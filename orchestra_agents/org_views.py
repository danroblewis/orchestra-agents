"""What a role, an agent, a team and the running agents look like to a reader: the dicts the
organisation commands (api_org.py), the web plugin and the CLI's ``status`` return."""

from __future__ import annotations

import time

from orchestra.runtime import alive

from . import prompting
from .clock import parse_interval
from .config import Project
from .harness.tools import tools_for
from .org import AgentDef, TeamDef, effective_tools, members_of, needs_task_system, team_chain
from .org_store import RepoOrgStore, all_agents, all_teams
from .roles import RoleDef, all_roles
from .store import AgentStore

NOT_USED = "needs the task system"     # the Coordinator without it: never started, never "down" (T54)


def role_definition(project: Project, role: RoleDef) -> dict:
    """What makes a role that role: its prompt, where it comes from, the tools behind its groups."""
    from . import state_machines
    repo = project.repo_root / ".orchestra" / "roles" / f"{role.name}.md"
    designed = state_machines.root(project) / role.name / "role.md"
    source = str(prompting.prompt_path(project, role.name) if role.builtin else designed if designed.exists() and not repo.exists() else repo)
    # the tool names first: a long prompt never pushes them onto a later page (answers.cut)
    return {"tool_names": {g: [t.name for t in tools_for([g])] for g in role.tools},
            "prompt": prompting.role_prompt(project, role.name), "prompt_source": source}


def agent_runs(store: AgentStore, ag: AgentDef) -> list[dict]:
    """Every session this agent has run under, newest first. A resident agent has one; an
    ephemeral agent has one per run, under a timestamped id, and they are all kept."""
    runs = [s for s in [*store.sessions(), *store.runtime().get("archived_sessions", {}).values()]
            if s.get("kind") == ag.name or s.get("id") == ag.session_id]
    runs.sort(key=lambda s: s.get("started_at") or 0, reverse=True)
    return [{k: s.get(k) for k in ("id", "status", "started_at", "settled_at", "last_text", "session_file", "harness")} for s in runs]


def agent_view(project: Project, store: AgentStore, ag: AgentDef, teams: dict | None = None) -> dict:
    st = store.agent_state(ag.name)
    teams = teams if teams is not None else all_teams(project)
    runs = agent_runs(store, ag)
    return {**ag.to_dict(), "builtin": ag.builtin, "session_id": ag.session_id,
            "triggers": list(ag.triggers), "emits": list(ag.emits),
            "last_run_id": runs[0]["id"] if runs else ag.session_id, "run_count": len(runs),
            "team_exists": ag.team is None or ag.team in teams,   # an agent may be created before its team (the guide's order)
            "resident": ag.resident, "paused": bool(st.get("paused")), "deleted": bool(st.get("deleted")),
            "crash_loop": st.get("crash_loop") if st.get("paused") else None,
            "paused_why": st.get("paused_why") if st.get("paused") else None,
            "last_woken": st.get("last_woken"), "last_turn": st.get("last_turn"), "next_scheduled_at": st.get("next_scheduled_at"),
            "next_review_at": st.get("next_review_at"),
            "every_seconds": parse_interval(ag.every) if ag.every else None,
            **({"not_used": NOT_USED} if needs_task_system(ag) else {})}


def agent_definition(project: Project, store: AgentStore, ag: AgentDef) -> dict:
    roles, teams = all_roles(project), all_teams(project)
    tools = effective_tools(ag, roles, teams)
    # the tool names first: a long prompt or state never pushes them onto a later page (answers.cut)
    return {"tool_groups": list(tools), "tool_names": {g: [t.name for t in tools_for([g])] for g in tools},
            "agent": agent_view(project, store, ag), "state": store.agent_state(ag.name), "runs": agent_runs(store, ag)[:30],
            "roles": [{**roles[r].to_dict(), "builtin": roles[r].builtin} for r in ag.roles if r in roles],
            "team_chain": [t.name for t in team_chain(teams, ag.team)],
            "leads": [t.name for t in teams.values() if t.lead == ag.name],
            "prompt": prompting.agent_prompt(project, ag, roles, teams), "machine": machine_view(project, store, ag.name)}


def machine_view(project: Project, store: AgentStore, name: str) -> dict | None:
    """The agent's machine spec (``.orchestra/machines/<name>/``), when it has one: its model, its
    duties, its spec's commits, its last review round and its overload signals."""
    from . import spec_git
    from .machine_spec import load
    spec = load(project, name)
    if spec is None:
        return None
    st = store.agent_state(name)
    return {"dir": spec.dir, "model": spec.model, **runs_on(project, name), "review_every": spec.review_every, "next_review_at": st.get("next_review_at"),
            "duties": [{"name": d.name, "about": d.about, "builtin": d.builtin, "listens": d.listens, "tools": d.tools,
                        "reason": d.reason} for d in spec.duties],
            "history": spec_git.history(project, 10), "review": {k: v for k, v in (st.get("review") or {}).items() if k != "seen"},
            "load": st.get("load"), "split_proposal": st.get("split_proposal")}


def team_view(project: Project, store: AgentStore, t: TeamDef) -> dict:
    ags = all_agents(project)
    return {**t.to_dict(), "builtin": t.builtin, "parent": t.parent, "space": t.space,
            "agents": [agent_view(project, store, ags[n]) for n in members_of(t, ags)],
            "sub_teams": [x.name for x in all_teams(project).values() if x.parent == t.name]}


def team_definition(project: Project, store: AgentStore, t: TeamDef) -> dict:
    v = team_view(project, store, t)
    resp = "" if t.builtin else RepoOrgStore(project).responsibilities(t.name)
    chain = team_chain(all_teams(project), t.parent)
    return {"team": v, "responsibilities": resp,
            "inherited": [{"team": x.name, "responsibilities": RepoOrgStore(project).responsibilities(x.name) if not x.builtin else ""}
                          for x in chain],
            "space_dir": str(t.space_dir(project)), "tools_dir": str(t.space_dir(project) / "tools")}


def session_alive(s: dict, host_alive: bool, residents: set[str]) -> bool:
    """A session is alive while its turn's process is; a resident agent between turns (on a
    harness that starts a process per turn there is none) is alive while its host is up."""
    return alive(s.get("pid")) or (s["id"] in residents and s.get("status") != "running" and host_alive)


def residents_of(project: Project) -> set[str]:
    return {a.session_id for a in all_agents(project).values() if a.resident}


def agents_status(project: Project, store: AgentStore) -> dict:
    """The running agents in one read: the host (alive or not, its pid and control port), what
    it runs the agents on and which of the task system's roles it fills, every agent with its
    state, and every session as the machine instance it runs (or last ran) as, with the ticket
    it is on (as its assignment named it). The machine cards, the Agents page and
    ``orchestra agents status`` read this."""
    rt = store.runtime()
    ctl = rt.get("control") or {}
    host_alive = alive(ctl.get("pid"))
    agents = []
    for a in all_agents(project).values():
        st = rt["agent_state"].get(a.name) or {}
        last = None if a.resident or a.builtin else next(iter(agent_runs(store, a)), None)
        agents.append({**({"not_used": NOT_USED} if needs_task_system(a) else {}),
                       "id": a.name, "name": a.name, "kind": "agent", "description": a.description, "team": a.team,
                       "session_id": a.session_id, "resident": a.resident, "paused": bool(st.get("paused")),
                       "paused_why": st.get("paused_why") if st.get("paused") else None,
                       "last_woken": st.get("last_woken"), "last_turn": st.get("last_turn"),
                       "next_scheduled_at": st.get("next_scheduled_at"),
                       "engaged": a.kind, "every": a.every, "at": a.at, "tz": a.tz, "roles": list(a.roles), "builtin": a.builtin,
                       "last_run": {"at": last.get("settled_at") or last.get("started_at"), "status": last.get("status"),
                                    "said": (last.get("last_text") or "")[-160:]} if last else None})
    residents = {a["session_id"] for a in agents if a["resident"]}
    sessions = []
    for x in rt["sessions"].values():
        sessions.append({**{k: v for k, v in x.items() if k not in ("partial_text", "last_text")},
                         "session": x["id"], "instance": x.get("instance") or x.get("kind"), "role": x.get("kind"),
                         "alive": session_alive(x, host_alive, residents),
                         "tool": x.get("tool_name"), "last_text": (x.get("partial_text") or x.get("last_text") or "")[-300:],
                         "ticket": ({"id": x["ticket_id"], "title": x.get("ticket_title") or "", "state": x.get("ticket_state") or "IN_PROGRESS",
                                     "worker_turns": x.get("worker_turns") or 0} if x.get("ticket_id") else None)})
    cfg = project.config
    for a in agents:
        if a["name"] == "supervisor":                     # its card says what it runs on (the D5 finding: it was not seen)
            a.update(runs_on(project))
    return {"ok": True, "host": {**ctl, "alive": host_alive}, "agents": agents, "sessions": sessions, "recent": recent_sessions(rt),
            "now": time.time(), "harness": cfg.harness, "model": cfg.model, "harness_note": cfg.harness_note, "serves": cfg.serves}


def runs_on(project: Project, name: str = "supervisor") -> dict:
    """What an agent's sessions start on: the harness, the provider (Pi and Codex take one) and the
    model as the host chooses it (``machine_spec.model_of``), with where the model came from."""
    from .harness import get_harness
    from .machine_spec import load, model_of
    cfg = project.config
    try:
        harness = get_harness(cfg.harness)
    except ValueError:                                    # an unknown harness: the host refuses it too
        harness = None
    model = model_of(project, harness, name) if harness is not None else cfg.model
    try:
        spec = load(project, name)
    except Exception:  # noqa: BLE001 - a spec that cannot be read: the model is the configured one
        spec = None
    if not model:
        origin = "the harness's default"
    elif cfg.model_set and model == cfg.model:
        origin = "[agents] model"
    elif spec is not None and spec.model and harness is not None and harness.model_for(spec.model) == model:
        origin = "its spec"
    else:
        origin = "[agents] model"
    return {"runs_on": {"harness": cfg.harness, "provider": cfg.provider, "model": model, "model_from": origin,
                        "tools_via": cfg.tools_via}}


def recent_sessions(rt: dict, per_role: int = 8) -> list[dict]:
    """The per-ticket sessions that are over (their ticket finished, or their turn ended), newest
    first, a few per role: the machine cards' "finished" list, so a worker's or reviewer's
    transcript is a click away after its instance has moved on."""
    done = [x for x in [*rt["sessions"].values(), *rt.get("archived_sessions", {}).values()]
            if x.get("ticket_id") and x.get("status") != "running"]
    done.sort(key=lambda x: x.get("settled_at") or x.get("started_at") or 0, reverse=True)
    out, per = [], {}
    for x in done:
        role = x.get("kind") or ""
        per[role] = per.get(role, 0) + 1
        if per[role] <= per_role:
            out.append({"session": x["id"], "role": role, "instance": x.get("instance") or role, "ticket": x["ticket_id"],
                        "title": x.get("ticket_title") or "", "settled_at": x.get("settled_at"), "last_text": (x.get("last_text") or "")[-200:]})
    return out


def measured_roles(project: Project) -> list[str]:
    """The roles whose sessions are measured (``agents_working{role}``, measures.py): the resident
    agents and the task system's roles the agents serve."""
    return sorted({name for name, a in all_agents(project).items() if a.resident} | set(project.config.serves))


def metrics_text(project: Project, store: AgentStore) -> str:
    """The agents' measurements as Prometheus text, for the instrument (``orchestra agents metrics``)."""
    return store.metrics_text(measured_roles(project))
