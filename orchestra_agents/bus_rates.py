"""How busy an event type is on the bus: its count in the last hour, read from the newest events
(``bus_reads.recent``), for the warning the duty and binding tools give on a wake event that is
activity (duties.py, bindings.py; guide.md's pitfall: wake on what signals trouble, not on
activity), for ``list_bindings`` and for the Agents page's list of what the Supervisor wakes on,
each event with its rate.

Since 2026-11-04 (T67, the owner: "I really do want agents to be able to remove their own event
bindings and add more event bindings") a busy event is allowed, with that warning: the Supervisor's
wake-ups are gathered into one turn at most every ``[agents] attention_every`` and capped by the
day's spend (notices.py, budget.py). Only a heartbeat unfiltered (``offer.alive``, a beat of every
lease every few seconds) is refused (``refusal``)."""

from __future__ import annotations

import time

from .bus_reads import _ts, recent
from .store import AgentStore

WINDOW_S = 3600.0
SCAN = 5000
BUSY_PER_HOUR = 30          # a type seen more often than this in the last hour is busy
HEARTBEATS = frozenset({"offer.alive"})     # refused whatever filter narrows them: each is a beat of a lease
ACTIVITY = {                # the Workflows floor's and the Outbox's activity, and the trouble signal to wake on instead
    "offer.alive": "offer.withdrawn (a lease that lapsed) or attention.needed",
    "offer.claimed": "offer.withdrawn or attention.needed",
    "offer.made": "attention.needed (the floor asks when an offer waits unclaimed)",
    "offer.finished": "attention.needed (a blocked finish is said there)",
    "item.closed": "attention.needed, or item.closed with a filter naming the item",
    "outbox.sent": "outbox.draft.rejected or outbox.send_failed",
    "session.settled": "session.error or agent.crash_loop",
    "gate.started": "gate.rejected",
}
TROUBLE = "a trouble signal: offer.withdrawn, attention.needed, outbox.draft.rejected, gate.rejected, orchestra.model.stalled"


def per_hour(events: list[dict], now: float | None = None, window: float = WINDOW_S) -> dict[str, int]:
    now = time.time() if now is None else now
    out: dict[str, int] = {}
    for e in events:
        ts = _ts(e)
        if ts is not None and now - ts <= window:
            out[str(e.get("type"))] = out.get(str(e.get("type")), 0) + 1
    return out


def rates(store: AgentStore, now: float | None = None) -> dict[str, int]:
    """Each type's count in the last hour; empty when the bus cannot be read."""
    try:
        return per_hour(recent(store, SCAN), now)
    except Exception:  # noqa: BLE001 - no rate known is no refusal and no mark
        return {}


def refusal(listen: str) -> str | None:
    """Why ``listen`` is refused as a wake event: only a heartbeat with no filter."""
    t, _, narrowed = listen.partition(":")
    if t in HEARTBEATS and not narrowed:
        return (f"{t} is a heartbeat, sent every few seconds for each lease: refused unfiltered. Wake on {ACTIVITY[t]}, "
                f"or narrow it to one lease ({t}:<item>)")
    return None


def busy(listen: str, rate: dict[str, int]) -> str | None:
    """Why ``listen`` (``type`` or ``type:filter``) would wake the Supervisor on activity, or None."""
    t, _, narrowed = listen.partition(":")
    if t in HEARTBEATS:
        return f"{t} is a heartbeat, sent every few seconds for each lease; wake on {ACTIVITY[t]}"
    if narrowed:
        return None
    if t in ACTIVITY:
        return f"{t} is activity, one for every piece of work; wake on {ACTIVITY[t]}"
    if rate.get(t, 0) > BUSY_PER_HOUR:
        return (f"{t} came {rate[t]} times in the last hour (over {BUSY_PER_HOUR}): each would be a turn; wake on {TROUBLE}, "
                f"or narrow it with a filter ({t}:<subject>)")
    return None
