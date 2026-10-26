"""The agents' events, published on Orchestra's bus with the CloudEvents envelope.

The code here calls ``emit(type, **fields)`` and reads events as flat dicts (``ev["ticket"]``).
``emit`` publishes a CloudEvent: the fields are its ``data``, the ticket (or milestone, or
session) it is about is its ``subject``, and its ``source`` is the machine that emitted it.
Subscribers in this process, and ``read``, get the flat shape back: the data's fields beside
``type``, ``ts`` and ``source``. The bus is opened as Orchestra opens it (``[events] url`` or
ORCHESTRA_EVENTS_URL), so one place decides where the events go.

The source is the machine that emitted the event:

- the agents' host (``orchestra-agents run``) is ``agents``, a program: it starts the agents;
- the web plugin's operator controls (web.py) are ``agents_web``;
- an event about an agent's session (its turn starting, settling, timing out) is the agent's own,
  sent as the instance the host bound to that session (``bind``): ``supervisor``, ``coordinator``,
  or a slot of a per-ticket role, ``worker/1``;
- an agent's own tool call (``orchestra-agents api`` or the MCP server, in the agent's
  environment) is ``ORCHESTRA_SOURCE``, which the host sets to the same instance."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from orchestra.events import Events as OrchestraEvents

if TYPE_CHECKING:
    from .config import Project

HOST = "agents"


def default_source() -> str:
    return os.environ.get("ORCHESTRA_SOURCE") or os.environ.get("ORCHESTRA_ROLE") or HOST


def flat(ev: dict) -> dict:
    """An event as this package reads it: its data's fields, with ``type``, ``ts``, ``source``."""
    data = ev.get("data")
    out = dict(data) if isinstance(data, dict) else {"data": data}
    out.update(type=ev.get("type"), ts=ev.get("time"), source=ev.get("source"), id=ev.get("id"))
    return out


class Events(OrchestraEvents):
    def __init__(self, *args, source: str | None = None, **kwargs):
        super().__init__(*args, source=source or default_source(), **kwargs)
        self._bound: dict[str, str] = {}             # session id -> the instance its events come from

    @classmethod
    def for_project(cls, project: Project, source: str | None = None) -> Events:
        return super().for_project(project.orchestra, source=source or default_source())

    def bind(self, session: str, source: str) -> None:
        self._bound[session] = source

    def unbind(self, session: str) -> None:
        self._bound.pop(session, None)

    def emit(self, type: str, **data: Any) -> dict:
        subject = data.get("ticket") or data.get("milestone") or data.get("session")
        source = self._bound.get(data.get("session") or "")
        return flat(self.publish(type, data or None, source=source, **({"subject": str(subject)} if subject else {})))

    def read(self, since: int = 0, limit: int = 500) -> tuple[list[dict], int]:
        evs, nxt = super().read(since, limit)
        return [flat(e) for e in evs], nxt


__all__ = ["HOST", "Events", "flat"]
