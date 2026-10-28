"""What a role, an agent, a team and the running agents look like to a reader: the dicts the
organisation commands (api_org.py), the web plugin and the CLI's ``status`` return."""

from __future__ import annotations

import time

from orchestra.runtime import alive

from . import prompting
from .clock import parse_interval
from .config import Project
from .harness.tools import tools_for
from .org import AgentDef, TeamDef, effective_tools, members_of, team_chain
from .org_store import RepoOrgStore, all_agents, all_teams
from .roles import RoleDef, all_roles
from .store import AgentStore


def role_definition(project: Project, role: RoleDef) -> dict:
    """What makes a role that role: its prompt, where it comes from, the tools behind its groups."""
    source = str(prompting.prompt_path(project, role.name) if role.builtin else project.repo_root / ".orchestra" / "roles" / f"{role.name}.md")
    return {"prompt": prompting.role_prompt(project, role.name), "prompt_source": source,
            "tool_names": {g: [t.name for t in tools_for([g])] for g in role.tools}}


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
            "last_woken": st.get("last_woken"), "next_scheduled_at": st.get("next_scheduled_at"), "next_review_at": st.get("next_review_at"),
            "every_seconds": parse_interval(ag.every) if ag.every else None}


def agent_definition(project: Project, store: AgentStore, ag: AgentDef) -> dict:
    roles, teams = all_roles(project), all_teams(project)
    tools = effective_tools(ag, roles, teams)
    return {"agent": agent_view(project, store, ag), "state": store.agent_state(ag.name), "runs": agent_runs(store, ag)[:30],
            "roles": [{**roles[r].to_dict(), "builtin": roles[r].builtin} for r in ag.roles if r in roles],
            "team_chain": [t.name for t in team_chain(teams, ag.team)],
            "leads": [t.name for t in teams.values() if t.lead == ag.name],
            "tool_groups": list(tools), "tool_names": {g: [t.name for t in tools_for([g])] for g in tools},
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
    return {"dir": spec.dir, "model": spec.model, "review_every": spec.review_every, "next_review_at": st.get("next_review_at"),
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
        agents.append({"id": a.name, "name": a.name, "kind": "agent", "description": a.description, "team": a.team,
                       "session_id": a.session_id, "resident": a.resident, "paused": bool(st.get("paused")),
                       "last_woken": st.get("last_woken"), "next_scheduled_at": st.get("next_scheduled_at")})
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
    return {"ok": True, "host": {**ctl, "alive": host_alive}, "agents": agents, "sessions": sessions, "recent": recent_sessions(rt),
            "now": time.time(), "harness": cfg.harness, "model": cfg.model, "harness_note": cfg.harness_note, "serves": cfg.serves}


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
    """The agents' measurements as Prometheus text, for the instrument and the plugin's /metrics."""
    return store.metrics_text(measured_roles(project))
