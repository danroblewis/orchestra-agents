"""What an agent reads of the bus (``read_events``), and which event types count as known to the
design tools (``known_types``).

``read_events`` answers in a size a small model can read: a summary of every type in the window
(how many, from whom, when last), then the newest events first, at most ``limit`` (20 by default,
100 at most), each with its envelope and its short fields only (a long text is cut, a list or an
object is counted, never copied). Once, unbounded, it answered with 360,000 characters and the
model read none of it (the live design test, A3).

**A known type** is one a machine on the bus speaks: heard (an event of it in the events read),
or announced (a machine's ``orchestra.machine.started`` names it among what it listens for, or
what it publishes), or one a declared machine emits, or one declared with ``declare_event``. A
type the merge gate publishes only when a branch fails (``gate.rejected``) is known before the
first failure, because the machines that wait for it say so when they start."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from .store import AgentStore

DEFAULT_LIMIT, MAX_LIMIT = 20, 100
SCAN = 20_000                           # the newest events read for the summary and the known types
TEXT = 160                              # a field's text past this is cut
HIDDEN = ("session.started", "session.settled")
ENVELOPE = ("type", "ts", "source", "subject", "id")
ANNOUNCED = "orchestra.machine.started"


def _ts(e: dict) -> float | None:
    try:
        dt = datetime.fromisoformat(e["ts"])
    except (KeyError, TypeError, ValueError):
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


def _short(v):
    if isinstance(v, str):
        return v if len(v) <= TEXT else v[:TEXT] + f"… ({len(v)} chars)"
    if isinstance(v, list):
        return v if len(v) <= 4 and all(isinstance(x, str | int | float | bool) and len(str(x)) <= 60 for x in v) \
            else f"({len(v)} items)"
    if isinstance(v, dict):
        return f"({len(v)} fields)"
    return v


def brief(e: dict) -> dict:
    """An event as read_events shows it: its envelope, then its other fields shortened."""
    out = {k: e[k] for k in ENVELOPE if e.get(k) is not None}
    out.update({k: _short(v) for k, v in e.items() if k not in out and k not in ENVELOPE})
    return out


def recent(store: AgentStore, n: int = SCAN) -> list[dict]:
    end = store.events.end()
    return store.events.read(max(0, end - n), limit=n)[0]


def _announced_types(e: dict) -> list[str]:
    return [t for k in ("listens_for", "types", "emits", "publishes") for t in (e.get(k) or []) if isinstance(t, str)]


def read_events(store: AgentStore, a: dict) -> dict:
    minutes = int(a.get("minutes") or 60)
    types = set(a.get("types") or [])
    limit = max(1, min(int(a.get("limit") or DEFAULT_LIMIT), MAX_LIMIT))
    since = time.time() - minutes * 60
    picked, by_type, announced = [], {}, {}
    for e in recent(store):
        ty = e.get("type")
        if ty == ANNOUNCED:
            for t in _announced_types(e):
                announced.setdefault(t, set()).add(str(e.get("source") or ""))
        ts = _ts(e)
        if ts is None or ts < since or ty in HIDDEN or (types and ty not in types):
            continue
        picked.append(e)
        row = by_type.setdefault(ty, {"type": ty, "count": 0, "sources": set(), "last": None})
        row["count"] += 1
        row["sources"].add(str(e.get("source") or ""))
        row["last"] = e.get("ts")
    summary = sorted(({**r, "sources": sorted(r["sources"])} for r in by_type.values()), key=lambda r: -r["count"])
    quiet = {t: sorted(s) for t, s in sorted(announced.items()) if t not in by_type and (not types or t in types)}
    newest = [brief(e) for e in reversed(picked[-limit:])]
    return {"ok": True, "count": len(picked), "minutes": minutes, "by_type": summary,
            "announced_not_heard": [{"type": t, "listened_for_by": s} for t, s in quiet.items()],
            "events": newest, "shown": len(newest),
            "note": (f"the newest {len(newest)} of {len(picked)}, newest first (limit {limit}, at most {MAX_LIMIT}); by_type counts "
                     "them all. A type in announced_not_heard is known: a machine announced it, so it may be named")}


def known_types(project, store: AgentStore, but: str | None = None) -> dict[str, str]:
    """Every known event type, with how it is known (the first way found): heard from a source,
    announced by a machine, emitted by a declared machine (other than ``but``), or declared."""
    from . import intended_api
    out: dict[str, str] = {}
    for e in recent(store):
        ty, src = e.get("type"), str(e.get("source") or "?")
        if ty == ANNOUNCED:
            for t in _announced_types(e):
                out.setdefault(t, f"announced by {src}")
        if isinstance(ty, str) and ty not in HIDDEN:
            out[ty] = f"heard from {src}" if not out.get(ty, "").startswith("heard") else out[ty]
    lay = intended_api.current(project)
    for mid, m in (lay.get("machines") or {}).items():
        for t in (m.get("emits") or []) if mid != but else []:
            out.setdefault(t, f"emitted by the declared machine {mid}")
    for t, d in (lay.get("events") or {}).items():
        if not (d or {}).get("implicit"):                # one a machine's emits made is no declaration of it
            out.setdefault(t, "declared (declare_event)")
    return out


def unknown_line(project, store: AgentStore, types: list[str], field: str, but: str | None = None) -> str | None:
    """A line for the answer when ``types`` names a type nothing speaks (``but``: the machine
    being changed, whose own emits do not make a type known); None when all are known."""
    if not types:
        return None
    known = known_types(project, store, but)
    if not (unknown := [t for t in types if t not in known]):
        return None
    return (f"{field}: {', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} not heard on the bus, announced by a machine, "
            "emitted by a declared machine or declared: name a type read_events lists (its by_type or announced_not_heard), "
            "or declare it first (declare_event) if this machine is the one that publishes it")
