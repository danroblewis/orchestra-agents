"""A hand-off the Coordinator never plans (``handoff.py``: a ``work.requested`` with no
``work.planned`` after it) would otherwise be silent: the Supervisor told the human it is handed
on and waits for a plan that never comes. Past a deadline (``DEADLINE_S``, or the Coordinator's
``[agents] coordinator_every`` when that is longer), each such hand-off wakes the Supervisor once
(``overdue_at`` on its record), and every Supervisor wake-up's facts list the ones still waiting,
with their request ids."""

from __future__ import annotations

import time

from .clock import parse_interval
from .protocol import SUPERVISOR
from .store import AgentStore

DEADLINE_S = 1800.0


def deadline(cfg) -> float:
    every = getattr(cfg, "coordinator_every", None)
    return max(DEADLINE_S, parse_interval(every)) if every else DEADLINE_S


def overdue(store: AgentStore, now: float, limit: float) -> dict[str, dict]:
    asked = store.agent_state(SUPERVISOR).get("handoffs") or {}
    return {rid: h for rid, h in asked.items() if not h.get("planned") and now - float(h.get("at") or now) >= limit}


def _mins(s: float) -> str:
    return f"{max(1, round(s / 60))} min"


def facts_line(store: AgentStore, now: float, limit: float) -> str:
    late = overdue(store, now, limit)
    if not late:
        return ""
    return ("[Orchestra: hand-offs the Coordinator has not planned (no work.planned yet): "
            + "; ".join(f"{rid}, handed on {_mins(now - float(h['at']))} ago ({(h.get('requirements') or '')[:80]})"
                        for rid, h in late.items()) + "]\n")


def wake(store: AgentStore, now: float | None = None, limit: float = DEADLINE_S) -> list[str]:
    """The wake-up reasons for hand-offs that passed the deadline since the last call, each once."""
    now = time.time() if now is None else now
    late = [rid for rid, h in overdue(store, now, limit).items() if not h.get("overdue_at")]
    if not late:
        return []
    store.update_agent_state(SUPERVISOR, "handoffs", lambda d: {
        rid: ({**h, "overdue_at": now} if rid in late else h) for rid, h in (d or {}).items()})
    return [f"hand-off {rid} to the Coordinator has no plan after {_mins(limit)} (no work.planned): tell the human it is "
            "not planned yet, and find out why (the Coordinator's session, its turns)" for rid in late]
