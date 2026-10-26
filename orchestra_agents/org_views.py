"""What a role, an agent and a team look like to a reader: the dicts the organisation commands
(api_org.py) and the web plugin return."""

from __future__ import annotations

from . import prompting as agents
from .config import Project
from .harness.tools import tools_for
from .org import AgentDef, TeamDef, effective_tools, members_of, team_chain
from .org_store import RepoOrgStore, all_agents, all_teams
from .roles import RoleDef, all_roles, parse_interval
from .store import AgentStore


def role_definition(project: Project, role: RoleDef) -> dict:
    """What makes a role that role: its prompt, where it comes from, the tools behind its groups."""
    source = str(agents.prompt_path(project, role.name) if role.builtin else project.repo_root / ".orchestra" / "roles" / f"{role.name}.md")
    return {"prompt": agents.role_prompt(project, role.name), "prompt_source": source,
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
    return {**ag.to_dict(), "builtin": ag.builtin, "source": ag.source, "session_id": ag.session_id,
            "triggers": list(ag.triggers), "emits": list(ag.emits),
            "last_run_id": runs[0]["id"] if runs else ag.session_id, "run_count": len(runs),
            "team_exists": ag.team is None or ag.team in teams,   # an agent may be created before its team (the guide's order)
            "resident": ag.resident, "paused": bool(st.get("paused")), "deleted": bool(st.get("deleted")),
            "crash_loop": st.get("crash_loop") if st.get("paused") else None,
            "last_woken": st.get("last_woken"), "next_scheduled_at": st.get("next_scheduled_at"),
            "every_seconds": parse_interval(ag.every) if ag.every else None}


def agent_definition(project: Project, store: AgentStore, ag: AgentDef) -> dict:
    roles, teams = all_roles(project), all_teams(project)
    tools = effective_tools(ag, roles, teams)
    return {"agent": agent_view(project, store, ag), "state": store.agent_state(ag.name), "runs": agent_runs(store, ag)[:30],
            "roles": [{**roles[r].to_dict(), "builtin": roles[r].builtin} for r in ag.roles if r in roles],
            "team_chain": [t.name for t in team_chain(teams, ag.team)],
            "leads": [t.name for t in teams.values() if t.lead == ag.name],
            "tool_groups": list(tools), "tool_names": {g: [t.name for t in tools_for([g])] for g in tools},
            "prompt": agents.agent_prompt(project, ag, roles, teams)}


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
