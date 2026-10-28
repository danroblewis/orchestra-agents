"""The agents' events, published on Orchestra's bus with the CloudEvents envelope.

The code here calls ``emit(type, **fields)`` and reads events as flat dicts (``ev["ticket"]``).
``emit`` publishes a CloudEvent: the fields are its ``data``, the ticket (or milestone, or
session) it is about is its ``subject``, and its ``source`` is the machine that emitted it.
Subscribers in this process, and ``read``, get the flat shape back: the data's fields beside
``type``, ``ts`` and ``source``. The bus is opened as Orchestra opens it (``[events] url`` or
ORCHESTRA_EVENTS_URL), so one place decides where the events go.

The source is the machine that emitted the event, and the agents' host is never one (the owner,
2026-10-27: the Agents plugin provides an implementation of a machine, the AI agent, and the host
is the shared runtime that runs the configured agents; it is no machine itself). So the host never
announces itself and never publishes under its own name, ``agents``: a publish as ``agents``, or
with no source at all, is refused here. Every event goes out as the agent it is about or acts for:

- an event about an agent's session (``session.started``, ``session.settled``, and a turn's bad
  ends: ``session.timed_out``, ``session.error``, ``session.interrupted``) is the agent's own,
  sent as the instance the host bound to that session (``bind``): ``supervisor``, ``coordinator``,
  or a slot of a per-ticket role, ``worker/1``; once the slot is given back, the instance the
  session's record names (``src``); so is ``agent.*``, about the agent as a whole (``agent.said``,
  ``agent.compacted``, ``agent.crashed``, ``agent.crash_loop``, ``agent.reset``);
- a team's probe (probes.py) is published as the agent responsible for the team: its lead, or the
  Supervisor when it has none;
- an agent's own tool call (``orchestra agents api`` or the MCP server, in the agent's
  environment) is ``ORCHESTRA_SOURCE``, which the host sets to the same instance."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from orchestra.events import Events as OrchestraEvents
from orchestra.events import machine_and_instance

if TYPE_CHECKING:
    from .config import Project

HOST = "agents"     # the host's name in its log and the plugin's name: never an event's source
GAVE_UP = "orchestra.process.gave_up"   # Orchestra's launcher stopped restarting a process (data: name, code, failures)


def default_source() -> str:
    """The agent this process acts as (set by the host in each session's environment), or none:
    the host itself has no source of its own."""
    return os.environ.get("ORCHESTRA_SOURCE") or os.environ.get("ORCHESTRA_ROLE") or ""


def flat(ev: dict) -> dict:
    """An event as this package reads it: its data's fields, with ``type``, ``ts``, ``source``, and
    the envelope's ``correlationid`` and ``causationid`` when it has them (a verdict names its
    change by ``correlationid``: verdicts.py)."""
    data = ev.get("data")
    out = dict(data) if isinstance(data, dict) else {"data": data}
    out.update(type=ev.get("type"), ts=ev.get("time"), source=ev.get("source"), id=ev.get("id"))
    for k in ("correlationid", "causationid"):
        if ev.get(k) is not None:
            out[k] = ev[k]
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

    def bound(self, session: str) -> str | None:
        return self._bound.get(session)

    @contextmanager
    def acting_as(self, source: str) -> Iterator[None]:
        """Within: a publish with no source of its own is ``source``'s, when this process has none
        (an agent's tool call made where ORCHESTRA_SOURCE is not set, as in the tests)."""
        before = self.source
        self.source = before or source
        try:
            yield
        finally:
            self.source = before

    def publish(self, type: str, data=None, *, source: str | None = None, **ext):
        src = source or self.source
        if not src or machine_and_instance(src)[0] == HOST:
            raise ValueError(f"{type}: the agents' host is no machine; publish as the agent this event is about or acts for, "
                             f"never as {HOST!r} or with no source")
        return super().publish(type, data, source=src, **ext)

    def emit(self, type: str, *, src: str | None = None, **data: Any) -> dict:
        """Publish ``type`` with ``data`` as ``src``; without one, as the instance bound to the
        data's ``session``, or this process's own source (an agent's tool call)."""
        subject = data.get("ticket") or data.get("milestone") or data.get("session")
        source = src or self._bound.get(data.get("session") or "")
        return flat(self.publish(type, data or None, source=source, **({"subject": str(subject)} if subject else {})))

    def read(self, since: int = 0, limit: int = 500) -> tuple[list[dict], int]:
        evs, nxt = super().read(since, limit)
        return [flat(e) for e in evs], nxt


__all__ = ["HOST", "Events", "flat"]
