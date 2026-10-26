"""The organisation as stored: custom agents and teams in the project repository, and the merged
view of built-ins and custom records that the rest of the package reads (``all_agents``,
``all_teams``). Roles have the same arrangement in ``roles.py``."""

from __future__ import annotations

import json
from pathlib import Path

from .builtins import ROLES as BUILTIN_ROLES
from .config import Project
from .org import BUILTIN_TEAMS, AgentDef, OrgError, TeamDef, built_in_agents
from .roles import all_roles, commit_orchestra_files
from .store import now


class RepoOrgStore:
    """Agents and teams in the target repository: ``.orchestra/agents/<name>.json``,
    ``.orchestra/teams/<team>.json`` with ``<team>.md`` for the responsibilities, and
    ``.orchestra/spaces/<team>/`` for the team's working space. Writes are committed to
    the base branch when the checkout allows it, like roles."""

    def __init__(self, project: Project):
        self.project = project
        self.root = project.repo_root / ".orchestra"
        self.agents_dir = self.root / "agents"
        self.teams_dir = self.root / "teams"
        self.spaces_dir = self.root / "spaces"
        self.last_commit_note = ""

    # agents
    def list_agents(self) -> list[AgentDef]:
        out = []
        for p in sorted(self.agents_dir.glob("*.json")):
            try:
                out.append(AgentDef.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def get_agent(self, name: str) -> AgentDef:
        p = self.agents_dir / f"{name}.json"
        if not p.exists():
            raise OrgError(f"no agent {name!r}")
        return AgentDef.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save_agent(self, agent: AgentDef, custom_types: set[str] | frozenset[str] = frozenset()) -> AgentDef:
        agent.validate(custom_types)
        if agent.name in BUILTIN_ROLES:
            raise OrgError(f"{agent.name!r} is a built-in agent; choose another name")
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        agent.updated_at = now()
        (self.agents_dir / f"{agent.name}.json").write_text(json.dumps(agent.to_dict(), indent=2) + "\n", encoding="utf-8")
        self.last_commit_note = commit_orchestra_files(self.project, f"agents: agent {agent.name}")
        return agent

    def delete_agent(self, name: str) -> None:
        p = self.agents_dir / f"{name}.json"
        if not p.exists():
            raise OrgError(f"no agent {name!r}")
        p.unlink()
        self.last_commit_note = commit_orchestra_files(self.project, f"agents: remove agent {name}")

    # teams
    def _team_paths(self, name: str) -> tuple[Path, Path]:
        return self.teams_dir / f"{name}.json", self.teams_dir / f"{name}.md"

    def list_teams(self) -> list[TeamDef]:
        out = []
        for p in sorted(self.teams_dir.rglob("*.json")):
            try:
                out.append(TeamDef.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def get_team(self, name: str) -> TeamDef:
        p, _ = self._team_paths(name)
        if not p.exists():
            raise OrgError(f"no team {name!r}")
        return TeamDef.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def responsibilities(self, name: str) -> str:
        _, p = self._team_paths(name)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def save_team(self, team: TeamDef, responsibilities: str) -> TeamDef:
        team.validate()
        if team.name in BUILTIN_TEAMS:
            raise OrgError(f"{team.name!r} is a built-in team; choose another name")
        if team.parent and not (self._team_paths(team.parent)[0].exists() or team.parent in BUILTIN_TEAMS):
            raise OrgError(f"parent team {team.parent!r} does not exist")
        pj, pm = self._team_paths(team.name)
        pj.parent.mkdir(parents=True, exist_ok=True)
        team.updated_at = now()
        pj.write_text(json.dumps(team.to_dict(), indent=2) + "\n", encoding="utf-8")
        pm.write_text(responsibilities.rstrip() + "\n", encoding="utf-8")
        self.ensure_space(team)
        self.last_commit_note = commit_orchestra_files(self.project, f"agents: team {team.name}")
        return team

    def delete_team(self, name: str) -> None:
        pj, pm = self._team_paths(name)
        if not pj.exists():
            raise OrgError(f"no team {name!r}")
        if any(t.parent == name for t in self.list_teams()):
            raise OrgError(f"team {name!r} has sub-teams; delete or move them first")
        for p in (pj, pm):
            if p.exists():
                p.unlink()
        self.last_commit_note = commit_orchestra_files(self.project, f"agents: remove team {name}")

    def ensure_space(self, team: TeamDef) -> Path:
        """The team's working space: created with a tools/ directory, and listed in
        .orchestra/.gitignore unless the team asked for it to be tracked. The space is
        never deleted by Orchestra: it is the team's, and it may hold work."""
        space = team.space_dir(self.project)
        (space / "tools").mkdir(parents=True, exist_ok=True)
        gi = self.root / ".gitignore"
        line = f"spaces/{team.name}/"
        lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
        if team.tracked:
            lines = [ln for ln in lines if ln.strip() != line]
        elif line not in lines:
            lines.append(line)
        gi.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return space


def all_teams(project: Project) -> dict[str, TeamDef]:
    out = dict(BUILTIN_TEAMS)
    for t in RepoOrgStore(project).list_teams():
        out[t.name] = t
    return out


def all_agents(project: Project) -> dict[str, AgentDef]:
    """Built-in agents (the per-ticket ones only for the roles these agents serve: a role another
    machine fills is not an agent here; the Coordinator on the project's schedule, if it sets
    one), implicit agents for role records that carry wake rules and have no explicit agent, and
    the project's own agents, by name."""
    out = {n: a for n, a in built_in_agents().items() if a.resident or n in project.config.serves}
    if project.config.coordinator_every:
        out["coordinator"].every, out["coordinator"].kind = project.config.coordinator_every, "scheduled"
    explicit = {a.name: a for a in RepoOrgStore(project).list_agents()}
    for r in all_roles(project).values():
        if r.builtin or r.name in explicit or not (r.wake_on or r.every or r.at):
            continue   # a role without wake rules runs only through an agent
        kind = "scheduled" if (r.every or r.at) else "continuous"
        out[r.name] = AgentDef(name=r.name, roles=[r.name], kind=kind, session=r.session, wake_on=list(r.wake_on),
                               every=r.every, at=r.at, description=r.description, created_by=r.created_by,
                               created_at=r.created_at, updated_at=r.updated_at, source="role")
    out.update(explicit)
    # A team's record is authoritative for who is in it: an agent it names as lead or member
    # belongs to it even when the agent's own record (or a role record, which has no team
    # field) says nothing, or says something older.
    for t in all_teams(project).values():
        for name in t.agents:
            if name in out and not out[name].builtin and out[name].team != t.name:
                out[name].team = t.name
    return out
