"""One session's tool calls, however they reach Orchestra: the MCP server (``mcp.py``), the
command a session runs from its shell (``orchestra agents tool``, ``bash_tools.py``) and the
Pi extension (which runs that command) all call through here, so a call is the same call
whichever way it came.

A call is checked against the tool's schema (``tooling.check_args``), its caller is the session
the host issued the token in the environment to (``access.identify``, on every call, so a session
that has ended is refused), and it runs as that role (``tooling.handle``, which checks the role's
groups again). The project and the session come from the environment (``ORCHESTRA_REPO``,
``ORCHESTRA_SESSION_TOKEN``), never from the working directory, which may be a ticket's worktree
or another repository. ``ORCHESTRA_TOOLS`` only chooses which tools are offered."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

from .tools import BY_NAME, TOOLS, ToolDef, tools_for


def groups_of(env: Mapping[str, str]) -> list[str] | None:
    """The tool groups ``ORCHESTRA_TOOLS`` names; None when it is not set."""
    raw = env.get("ORCHESTRA_TOOLS")
    return None if raw is None else [g for g in raw.split(",") if g]


class Caller:
    def __init__(self, env: Mapping[str, str] | None = None, groups: list[str] | None = None):
        self.env = os.environ if env is None else env
        self.groups = groups
        self.tools: list[ToolDef] = tools_for(groups) if groups is not None else list(TOOLS)
        self._project = None
        self._store = None

    def tool(self, name: str) -> ToolDef:
        """The tool of that name this session is offered, or a LookupError saying which it has."""
        tool = BY_NAME.get(name)
        if tool is None or tool not in self.tools:
            raise LookupError(f"unknown tool {name}")
        return tool

    def _ctx(self):
        if self._project is None:
            from ..config import load_project
            from ..events import Events
            from ..store import AgentStore
            self._project = load_project(self.env.get("ORCHESTRA_REPO") or None)
            self._store = AgentStore(self._project, Events.for_project(self._project))
        return self._project, self._store

    def call(self, name: str, args: dict | None):
        """The tool's answer; raises with the reason when the call is refused or fails."""
        from .. import access, tooling
        tool = self.tool(name)
        args = dict(args or {})
        tooling.check_args(tool.name, args)
        project, store = self._ctx()
        role, ticket = access.identify(store, self.env)
        return tooling.handle(project, store, tool.name, args, role=role, ticket_env=ticket)


def text(result) -> str:
    """An answer as the model reads it: a string as it is, anything else as JSON."""
    return result if isinstance(result, str) else json.dumps(result, indent=1, default=str)


def error(e: BaseException) -> str:
    return json.dumps({"ok": False, "error": str(e)})
