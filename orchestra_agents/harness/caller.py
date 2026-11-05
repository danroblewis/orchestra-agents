"""One session's tool calls, however they reach Orchestra: the MCP server (``mcp.py``), the
command a session runs from its shell (``orchestra agents tool``, ``bash_tools.py``) and the
Pi extension (which runs that command) all call through here, so a call is the same call
whichever way it came.

A call is checked against the tool's schema (``tooling.check_args``), its caller is the session
the host issued the token in the environment to (``access.identify``, on every call, so a session
that has ended is refused), and it runs as that role (``tooling.handle``, which checks the role's
groups again). The project and the session come from the environment (``ORCHESTRA_REPO``,
``ORCHESTRA_SESSION_TOKEN``), never from the working directory, which may be a ticket's worktree
or another repository. ``ORCHESTRA_TOOLS`` only chooses which tools are offered. Every answer is
bounded here (``answer``, ``answers.cut``): no harness gets one over ``answers.BOUND`` characters, and a
session that holds ``read_answer`` none over ``answer_store.TRIM``: the rest is kept, to page."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

from .tools import PAGE, TOOLS, ToolDef, tools_for

READ_ANSWER = "read_answer"
WHOLE = (READ_ANSWER, "read_skill")      # never trimmed: a page already, and a skill is read whole (skills.PIECE_CHARS)


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
        """The tool of that name this session is offered (as offered: its text gated), or a LookupError.
        Matched by name: ``tools_for`` returns copies whose text is gated, which never equal ``BY_NAME``'s."""
        tool = next((t for t in self.tools if t.name == name), None)
        if tool is None:
            raise LookupError(f"unknown tool {name}")
        return tool

    def _ctx(self):
        if self._project is None:
            from ..config import load_project
            from ..events import Events
            from ..store import AgentStore
            self._project = load_project(self.env.get("ORCHESTRA_REPO") or None)
            # as the session: its own environment's source (a file-mode session's file is acted on in the host)
            source = self.env.get("ORCHESTRA_SOURCE") or self.env.get("ORCHESTRA_ROLE") or None
            self._store = AgentStore(self._project, Events.for_project(self._project, source=source))
        return self._project, self._store

    def answer(self, name: str, args: dict | None) -> str:
        """The tool's answer as the model reads it: its text, at most ``answers.BOUND`` characters
        (cut with a line saying how to read the rest; a read tool's ``page`` is the page asked for,
        never passed to the tool). Raises with the reason when the call is refused or fails."""
        from ..answer_store import TRIM
        from ..answers import cut
        tool = self.tool(name)
        args = dict(args or {})
        pageable = PAGE in tool.properties
        page = args.pop(PAGE, None) if pageable else None
        try:
            page = int(page) if page not in (None, "") else 1
        except (TypeError, ValueError):
            raise ValueError(f"page is a number, 1 for the first page; not {page!r}") from None
        full = text(self.call(name, args))
        if self.trims and tool.name not in WHOLE and (len(full) > TRIM or page > 1):
            return self.trimmed(full, tool.name, page)
        return cut(full, tool.name, page, pageable)

    @property
    def trims(self) -> bool:
        """Whether this session's large answers are trimmed (``answer_store``): it holds ``read_answer``."""
        return any(t.name == READ_ANSWER for t in self.tools)

    def trimmed(self, full: str, tool: str, page: int) -> str:
        """The answer's start (or the page asked for), its shape and a pointer to the whole of it, kept."""
        from ..answer_store import keep, piece
        project, _ = self._ctx()
        return piece(full, keep(project, full), tool, page)

    def call(self, name: str, args: dict | None):
        """The tool's answer; raises with the reason when the call is refused or fails."""
        from .. import access, tooling
        tool = self.tool(name)
        args = dict(args or {})
        tooling.check_args(tool.name, args)
        project, store = self._ctx()
        role, ticket = access.identify(store, self.env)
        if tool.name == "delegate":       # the host runs it, from its environment and sign-in (delegate_host.py)
            from ..delegate_host import ask
            access.authorize(project, tool.name, role)
            return ask(project, store, args, role, self.env)
        return tooling.handle(project, store, tool.name, args, role=role, ticket_env=ticket)


def text(result) -> str:
    """An answer as the model reads it: a string as it is, anything else as JSON."""
    return result if isinstance(result, str) else json.dumps(result, indent=1, default=str)


def error(e: BaseException) -> str:
    """A refusal or failure as the model reads it, bounded like any answer."""
    from ..answers import cut
    return cut(json.dumps({"ok": False, "error": str(e)}), "the tool", 1, pageable=False)
