"""Who is calling a tool, and whether that caller may call it (AGENTS.md rules 7 and 10).

**Who.** The host issues every session it starts a token (``ORCHESTRA_SESSION_TOKEN`` in the
session's environment, beside ``ORCHESTRA_ROLE``) and keeps only its SHA-256, with the role and
ticket it was issued for, in ``runtime.json`` (``AgentStore.set_grant``). A tool call that comes
in from outside the host's own process (the MCP server, ``orchestra agents tool``) is identified by
that token (``identify``): the role and the ticket are the grant's, never the environment's. A
shell inside a worker's session that sets ``ORCHESTRA_ROLE=supervisor`` still carries the
worker's token and is refused; one with no token, or a token the host did not issue or has
revoked (its session closed, or a new session of that id opened), is refused.

**What.** ``authorize`` checks every call that reaches ``tooling.handle`` against the caller's
tool groups as the organisation grants them now (``org.effective_tools``; a built-in role's own
groups for a built-in name): an unknown or missing role is refused, and so is a tool whose group
the role does not have. The tools that are the Supervisor's alone (``api_org.MUTATIONS``, the
``setup`` group, ``update_project_context``) are checked again by their handlers.

The limit, stated plainly: the agents run as the same OS user as the host, with their
harnesses' permission checks off. Such a process can read another process's environment or
command line, the Claude Code MCP config the host writes for a turn, or Python's memory, and it
can import this package and write ``runtime.json`` itself. The token stops a role being claimed
by setting a variable; it is not a sandbox. Isolation from a hostile agent needs another OS user
or a container, which this package does not set up."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping

from .builtins import ROLES as BUILTIN_ROLES
from .config import Project
from .org import built_in_agents, effective_tools
from .org_store import all_agents, all_teams
from .roles import all_roles
from .store import AgentStore, StoreError

TOKEN_ENV = "ORCHESTRA_SESSION_TOKEN"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(store: AgentStore, session_id: str, role: str, ticket: str = "") -> str:
    """A new token for a session the host is starting; any earlier token of that session id stops working."""
    token = secrets.token_urlsafe(32)
    store.set_grant(session_id, {"digest": _digest(token), "role": role, "ticket": ticket, "issued_at": time.time()})
    return token


def revoke(store: AgentStore, session_id: str) -> None:
    store.drop_grant(session_id)


def identify(store: AgentStore, env: Mapping[str, str]) -> tuple[str, str]:
    """The (role, ticket) the host issued the calling session's token for. Raises StoreError,
    saying why, when there is no token, the host did not issue it (or no longer honours it), or
    ``ORCHESTRA_ROLE`` claims another role than the token's."""
    token = env.get(TOKEN_ENV) or ""
    if not token:
        raise StoreError(f"refused: no {TOKEN_ENV}. The agents' tools are called only from a session the host started, "
                         "which carries ORCHESTRA_ROLE and the session's token")
    digest = _digest(token)
    grant = next((g for g in store.grants().values() if hmac.compare_digest(str(g.get("digest", "")), digest)), None)
    if grant is None:
        raise StoreError(f"refused: {TOKEN_ENV} was not issued by the host, or its session has ended")
    claimed = env.get("ORCHESTRA_ROLE") or ""
    if claimed and claimed != grant["role"]:
        raise StoreError(f"refused: ORCHESTRA_ROLE is {claimed!r}, but this session was started as {grant['role']!r}; "
                         "the role comes from the session, not from the environment")
    return grant["role"], grant.get("ticket") or ""


def granted_groups(project: Project, role: str) -> tuple[str, ...] | None:
    """The tool groups ``role`` (an agent's name, or a built-in role's) holds now; None when no
    agent or built-in role has that name. A built-in name always means the built-in agent."""
    ag = all_agents(project).get(role)
    if role in BUILTIN_ROLES and (ag is None or not ag.builtin):
        ag = built_in_agents()[role]
    if ag is None:
        return None
    return effective_tools(ag, all_roles(project), all_teams(project))


def authorize(project: Project, command: str, role: str) -> None:
    """Raise StoreError, naming the role and the group needed, unless ``role`` may call ``command``."""
    from .harness.tools import BY_NAME
    tool = BY_NAME.get(command)
    if tool is None:
        raise StoreError(f"unknown command {command!r}")
    if not role:
        raise StoreError(f"refused {command}: no role. A tool call comes from an agent's session, which the host starts with its role")
    groups = granted_groups(project, role)
    if groups is None:
        raise StoreError(f"refused {command}: unknown role {role!r} (no built-in or custom agent of that name)")
    if tool.group not in groups:
        raise StoreError(f"refused {command}: it needs the {tool.group!r} tool group, and role {role!r} has "
                         f"{', '.join(groups) or 'none'}")
