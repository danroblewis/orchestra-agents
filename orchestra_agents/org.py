"""Agents and teams as data (SPEC.md: the organisation).

Three things, kept apart on purpose:

- a **role** (``roles.py``) describes a capability, like a job description. It
  has no runtime: it cannot be scheduled, hold a session, or receive a message;
- an **agent** is an instance of one or more roles placed in a team. It is what runs: it
  has a kind and a session policy, and it is what gets paused, restarted, messaged, and
  shown on the Dashboard. Three kinds: ``continuous`` is a resident process that is always
  up and is woken by events (like the Coordinator); ``scheduled`` runs on an interval or at
  a daily time, with a resident or an ephemeral session; ``ad_hoc`` gets a fresh session
  per invocation, and an invocation is a brief from its lead or a subscribed event;
- a **team** has exactly one lead agent, members, standing responsibilities, access
  grants, a space in the repository, and optionally a parent team.

Built-in agents and teams are declared here from the built-in roles. Custom ones live in
the repository under ``.orchestra/agents/`` and ``.orchestra/teams/`` (``org_store.py``, which
also merges the two into the project's organisation); a team's working space is
``.orchestra/spaces/<team>/``."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .builtins import ROLES as BUILTIN_ROLES
from .config import Project
from .roles import EVENT_TYPES, TOOL_GROUPS, RoleError, parse_interval
from .store import now

AGENT_KINDS = ("continuous", "scheduled", "ad_hoc")
ACCESS_MODES = ("read", "write")
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


class OrgError(ValueError):
    pass


_CUSTOM_TYPE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def event_matches(wake_on: list[str], ev: dict) -> bool:
    """`type` matches the event type; `type:x` also needs x to be the transition target, the
    status, the probe name, the role an attention event is addressed to, or the subject of an
    agent-emitted event."""
    for w in wake_on:
        t, _, suffix = w.partition(":")
        if ev.get("type") != t:
            continue
        if not suffix or suffix in (ev.get("to"), ev.get("status"), ev.get("probe"), ev.get("subject")):
            return True
    return False


def custom_event_types(teams: dict) -> set[str]:
    out: set[str] = set()
    for t in teams.values():
        out.update(getattr(t, "emits", []) or [])
    return out


@dataclass
class AgentDef:
    name: str
    roles: list[str] = field(default_factory=list)
    team: str | None = None
    kind: str = "continuous"              # continuous | scheduled | ad_hoc
    session: str = "resident"             # resident | ephemeral
    wake_on: list[str] = field(default_factory=list)
    every: str | None = None
    at: str | None = None
    description: str = ""
    created_by: str = "supervisor"
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    builtin: bool = False
    source: str = "agent"                 # agent | role (implicit, from a role record with wake rules)
    triggers: list[str] = field(default_factory=list)   # built-ins: what else wakes them, which is not an event on the bus
    emits: list[str] = field(default_factory=list)      # built-ins: event types their actions cause

    @property
    def session_id(self) -> str:
        return self.name.replace("_", "-")

    @property
    def resident(self) -> bool:
        """A long-lived process is kept for this agent between wake-ups."""
        return self.session == "resident" and self.kind != "ad_hoc"

    @property
    def ephemeral(self) -> bool:
        return not self.resident

    def validate(self, custom_types: set[str] | frozenset[str] = frozenset()) -> None:
        if not _NAME.match(self.name):
            raise OrgError("agent name must be lowercase letters, digits, and underscores, 2 to 41 chars")
        if not self.roles:
            raise OrgError("an agent needs at least one role")
        if self.kind not in AGENT_KINDS:
            raise OrgError(f"kind must be one of {list(AGENT_KINDS)}")
        if self.session not in ("resident", "ephemeral"):
            raise OrgError("session must be 'resident' or 'ephemeral'")
        for w in self.wake_on:
            t = w.split(":", 1)[0]
            if t not in EVENT_TYPES and t not in custom_types:
                raise OrgError(f"unknown event type {w!r}; known: {list(EVENT_TYPES)}"
                               + (f"; declared by teams: {sorted(custom_types)}" if custom_types else ""))
        if self.every is not None:
            try:
                parse_interval(self.every)
            except RoleError as e:
                raise OrgError(str(e))
        if self.at is not None and not re.fullmatch(r"\d{2}:\d{2}", self.at):
            raise OrgError("at must be HH:MM")
        if self.kind == "ad_hoc" and (self.every or self.at):
            raise OrgError("an ad_hoc agent runs when briefed or on a wake_on event; it has no `every` or `at`")
        if self.kind == "ad_hoc" and self.session != "ephemeral":
            raise OrgError("an ad_hoc agent gets a fresh session per invocation: session must be 'ephemeral'")
        if self.kind == "continuous" and self.session != "resident":
            raise OrgError("a continuous agent is a process that is always up: session must be 'resident'. "
                           "For a fresh session on each event, use kind 'ad_hoc' with wake_on.")
        if self.kind == "scheduled" and not (self.every or self.at):
            raise OrgError("a scheduled agent needs `every` or `at`")

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("builtin", "source", "triggers", "emits"):
            d.pop(k, None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> AgentDef:
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class TeamDef:
    name: str                             # "product" or, nested, "product/qa"
    description: str = ""
    lead: str = ""                        # agent name
    members: list[str] = field(default_factory=list)   # agent names, lead excluded
    tracked: bool = False                 # is the space committed with the code?
    access: list[dict] = field(default_factory=list)   # {"path", "access"} or {"tools": [...]}
    capacity: int = 1                     # member sessions that may run at once
    emits: list[str] = field(default_factory=list)     # event types this team's agents may raise with emit_event
    probes: list[dict] = field(default_factory=list)   # {"name", "command", "every", "timeout"?}, run in the space
    created_by: str = "supervisor"
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    builtin: bool = False

    @property
    def parent(self) -> str | None:
        return self.name.rpartition("/")[0] or None

    @property
    def agents(self) -> list[str]:
        return ([self.lead] if self.lead else []) + [m for m in self.members if m != self.lead]

    @property
    def space(self) -> str:
        return f".orchestra/spaces/{self.name}"

    def space_dir(self, project: Project) -> Path:
        return project.repo_root / self.space

    @property
    def tool_groups(self) -> list[str]:
        out: list[str] = []
        for g in self.access:
            for t in g.get("tools") or []:
                if t not in out:
                    out.append(t)
        return out

    @property
    def paths(self) -> list[dict]:
        return [g for g in self.access if g.get("path")]

    def validate(self) -> None:
        for seg in self.name.split("/"):
            if not _NAME.match(seg):
                raise OrgError("team name segments must be lowercase letters, digits, and underscores; nest with '/'")
        if not self.lead:
            raise OrgError("a team needs a lead agent")
        if self.lead in self.members:
            raise OrgError("the lead is not also a member")
        if self.capacity < 0:
            raise OrgError("capacity must be 0 or more")
        for e in self.emits:
            if not _CUSTOM_TYPE.match(e) or e in EVENT_TYPES:
                raise OrgError(f"emits entry {e!r} must be a new lowercase_name, not one of Orchestra's own event types")
        for pr in self.probes:
            if not isinstance(pr, dict) or not _NAME.match(str(pr.get("name", ""))) or not pr.get("command"):
                raise OrgError("each probe is {name, command, every} with a lowercase_name")
            try:
                parse_interval(str(pr.get("every", "5m")))
            except RoleError as e:
                raise OrgError(f"probe {pr.get('name')}: {e}")
        for g in self.access:
            if "tools" in g:
                bad = [t for t in g["tools"] if t not in TOOL_GROUPS]
                if bad:
                    raise OrgError(f"unknown tool groups {bad}; known: {list(TOOL_GROUPS)}")
            elif "path" in g:
                if g.get("access") not in ACCESS_MODES:
                    raise OrgError("a path grant needs access 'read' or 'write'")
                if str(g["path"]).startswith("/") or ".." in str(g["path"]).split("/"):
                    raise OrgError("path grants are relative to the repository and may not climb out of it")
            else:
                raise OrgError("each access entry is {path, access} or {tools}")

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("builtin", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> TeamDef:
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


def built_in_agents() -> dict[str, AgentDef]:
    out: dict[str, AgentDef] = {}
    for b in BUILTIN_ROLES.values():
        team = {"supervisor": None, "coordinator": "engineering", "worker": "engineering",
                "reviewer": "engineering"}.get(b.name)
        out[b.name] = AgentDef(name=b.name, roles=[b.name], team=team, kind="continuous" if b.resident else "ad_hoc",
                               session="resident" if b.resident else "ephemeral", wake_on=list(b.wake_on),
                               description=b.description, created_by="orchestra", builtin=True,
                               triggers=list(b.triggers), emits=list(b.emits))
    return out


BUILTIN_TEAMS: dict[str, TeamDef] = {
    "engineering": TeamDef(name="engineering", lead="coordinator", members=["worker", "reviewer"], builtin=True,
                           created_by="orchestra",
                           description="Turns tickets into finished work: the Coordinator keeps the plan executable, "
                                       "workers do the tickets, reviewers review them."),
}


def members_of(team: TeamDef, agents: dict[str, AgentDef]) -> list[str]:
    """The team's agents: those its record names, then any whose own record names the team
    (an agent is allowed to be created before the team lists it)."""
    names = [n for n in team.agents if n in agents]
    names += [a.name for a in agents.values() if a.team == team.name and a.name not in names]
    return names


def team_chain(teams: dict[str, TeamDef], name: str | None) -> list[TeamDef]:
    """The team and its ancestors, nearest first."""
    out: list[TeamDef] = []
    while name and name in teams:
        out.append(teams[name])
        name = teams[name].parent
    return out


def teams_led_by(teams: dict[str, TeamDef], agent: str) -> list[TeamDef]:
    return [t for t in teams.values() if t.lead == agent]


def effective_tools(agent: AgentDef, roles: dict, teams: dict[str, TeamDef]) -> tuple[str, ...]:
    """Tool groups an agent runs with: its roles' groups, its team chain's granted groups,
    and `team` for every agent in a team, so a member can reach its lead with
    message_agent and read the organisation. Who may message whom is enforced in the
    API by the tree, not by the grant. A worker or reviewer on a ticket gets its role's
    exit tool and nothing else."""
    out: list[str] = []
    for r in agent.roles:
        for g in (roles[r].tools if r in roles else ()):
            if g not in out:
                out.append(g)
    if agent.builtin and not agent.resident:
        return tuple(out)
    for t in team_chain(teams, agent.team):
        for g in t.tool_groups:
            if g not in out:
                out.append(g)
    if (agent.team in teams or teams_led_by(teams, agent.name)) and "team" not in out:
        out.append("team")
    return tuple(out)


def tools_path(project: Project, teams: dict[str, TeamDef], agent: AgentDef) -> list[Path]:
    """The team chain's tools/ directories, nearest first, for the agent's PATH."""
    return [t.space_dir(project) / "tools" for t in team_chain(teams, agent.team)]


def may_message(teams: dict[str, TeamDef], agents: dict[str, AgentDef], sender: str, target: str) -> str | None:
    """The tree: a lead may message its team's agents and its sub-teams' leads, an agent
    may message its own lead, the Supervisor may message anyone. Returns why not, or None."""
    if sender == "supervisor":
        return None
    if target not in agents:
        return f"no agent {target!r}"
    t_team = agents[target].team
    s_team = agents.get(sender).team if sender in agents else None
    led = {t.name for t in teams_led_by(teams, sender)}
    if t_team in led:
        return None                                   # my member
    if t_team and teams[t_team].parent in led and teams[t_team].lead == target:
        return None                                   # the lead of my sub-team
    if s_team and teams.get(s_team) and teams[s_team].lead == target:
        return None                                   # my own lead
    for t in teams_led_by(teams, sender):
        if t.parent and t.parent in teams and teams[t.parent].lead == target:
            return None                               # the lead of the team above mine
    return (f"{sender} may not message {target}: agents talk only to their own lead, leads to their team and "
            f"to the leads of their sub-teams. Escalate to the Supervisor instead.")
