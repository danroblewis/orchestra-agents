"""What an agent reads of the bus (``read_events``), and which event types count as known to the
design tools (``known_types``).

``read_events`` answers in a size a small model can read: a summary of every type in the window
(how many, from whom, when last), then the newest events first, at most ``limit`` (20 by default,
100 at most), each with its envelope and its short fields only (a long text is cut, a list or an
object is counted, never copied). Once, unbounded, it answered with 360,000 characters and the
model read none of it (the live design test, A3).

**A known type** is one a machine on the bus speaks: heard (an event of it in the events read),
or announced (a machine's ``orchestra.machine.started`` names it among what it listens for), or one a declared machine emits, or one declared with ``declare_event``. A
type the merge gate publishes only when a branch fails (``gate.rejected``) is known before the
first failure, because the machines that wait for it say so when they start.

With ``since`` it also answers the window's ``tally`` (``event_tally``): each class of event
counted, for "what changed since", and the ``model`` line (``modeling.model_line``: the model's
state with who changed it and when, from the version's own record, T2f s16), so the answer has the model from its first call (T2d s16), with the ``outbox``
("running; N sent, M waiting") and each prediction's state (``since_facts``, T2e s16), and what waits
on the owner now (``owner_waits``: disabled machines, Outbox drafts, the Supervisor's open
questions; T2f s16), and ``closing_line``: the line the reply ends on, verbatim (the model line).
And ``digest`` (``since_digest``): the whole answer as plain text, written from all of it, which
the Supervisor replies with as it is (T2g s16: given every fact, the model still misstated times
and counts)."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from .answers import first
from .event_tally import NOTE as TALLY_NOTE
from .event_tally import tally
from .store import AgentStore, StoreError

DEFAULT_LIMIT, MAX_LIMIT = 20, 100
SCAN = 20_000                           # the newest events read for the summary and the known types
TEXT = 160                              # a field's text past this is cut
HIDDEN = ("session.started", "session.settled", "task.query_requested")   # the host's own markers, and an agent's reads of the task system
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
    """What an announcement names: the types it listens for (Orchestra reads no other list in it)."""
    return [t for t in (e.get("listens_for") or []) if isinstance(t, str)]


def _matcher(types):
    """Whether a type is asked for: an exact type, or a prefix ending in ``.`` (``pagerduty.``, also
    written ``pagerduty.*``) for every type under it; no types asks for all."""
    exact, prefixes = set(), []
    for t in types or []:
        t = str(t).strip()
        if t.endswith(".*"):
            t = t[:-1]
        if t.endswith("."):
            prefixes.append(t)
        elif t:
            exact.add(t)
    if not exact and not prefixes:
        return lambda ty: True
    return lambda ty: isinstance(ty, str) and (ty in exact or any(ty.startswith(p) for p in prefixes))


RELATIVE = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def since_of(v, now: float | None = None) -> float:
    """``since`` as unix seconds: an ISO time (``2026-11-01T22:40:00Z``, a zone or UTC), unix
    seconds, or a time back from now (``30m``, ``2h``, ``1d``, ``90s``)."""
    now = time.time() if now is None else now
    if isinstance(v, int | float):
        return float(v)
    text = str(v).strip()
    if text and text[-1].lower() in RELATIVE and text[:-1].replace(".", "", 1).isdigit():
        return now - float(text[:-1]) * RELATIVE[text[-1].lower()]
    if text.replace(".", "", 1).isdigit():
        return float(text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise StoreError(f"since: an ISO time (2026-11-01T22:40:00Z), unix seconds, or a time back such as 30m, 2h or 1d; "
                         f"not {v!r}") from None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


def read_events(store: AgentStore, a: dict, project=None) -> dict:
    minutes = int(a.get("minutes") or 60)
    wanted = _matcher(a.get("types"))
    types = bool(a.get("types"))
    limit = max(1, min(int(a.get("limit") or DEFAULT_LIMIT), MAX_LIMIT))
    oldest = str(a.get("order") or "newest").strip().lower() == "oldest"
    if a.get("order") and str(a["order"]).strip().lower() not in ("oldest", "newest"):
        raise StoreError("order is newest (the default: the newest first) or oldest (from since on, in the order they happened)")
    start = since_of(a["since"]) if a.get("since") not in (None, "") else time.time() - minutes * 60
    picked, by_type, announced = [], {}, {}
    for e in recent(store):
        ty = e.get("type")
        if ty == ANNOUNCED:
            for t in _announced_types(e):
                announced.setdefault(t, set()).add(str(e.get("source") or ""))
        ts = _ts(e)
        if ts is None or ts < start or ty in HIDDEN or not wanted(ty):
            continue
        picked.append(e)
        row = by_type.setdefault(ty, {"type": ty, "count": 0, "sources": set(), "last": None})
        row["count"] += 1
        row["sources"].add(str(e.get("source") or ""))
        row["last"] = e.get("ts")
    summary = sorted(({**r, "sources": sorted(r["sources"])} for r in by_type.values()), key=lambda r: -r["count"])
    quiet = {t: sorted(s) for t, s in sorted(announced.items()) if t not in by_type and (not types or wanted(t))}
    shown = [brief(e) for e in picked[:limit]] if oldest else [brief(e) for e in reversed(picked[-limit:])]
    window = {"since": datetime.fromtimestamp(start, UTC).isoformat()} if a.get("since") not in (None, "") else {"minutes": minutes}
    which = (f"the oldest {len(shown)} of {len(picked)}, in the order they happened" if oldest
             else f"the newest {len(shown)} of {len(picked)}, newest first")
    more = (" (read on with since set to the last one's ts)" if oldest and len(picked) > len(shown) else "")
    counted = {"tally": tally(picked), "tally_note": TALLY_NOTE} if a.get("since") not in (None, "") else {}
    if counted and project is not None:
        from . import owner_waits, since_facts
        from .modeling import model_line
        counted["outbox"] = since_facts.outbox(project, start)
        counted["predictions"] = since_facts.predictions(project)
        counted["waiting_on_owner"] = owner_waits.waiting_on_owner(project, store)
        counted["model"] = model_line(project, start)
        counted["closing_line"] = counted["model"]       # the reply's last line, verbatim (closing_line.py)
        from . import clock, machine_ages, since_digest
        try:
            ages = machine_ages.machine_ages(project, store).get("line") or "Machines by age: not read."
        except Exception:  # noqa: BLE001 - the machines view unreadable: the digest still stands, saying so
            ages = "Machines by age: not read."
        try:
            from .subsystem_offer import line as subsystem_line
            boxes = subsystem_line(project, store)         # at most once a day
        except Exception:  # noqa: BLE001 - the declared layer or the model unreadable: no line, the digest stands
            boxes = None
        from . import supervisor_pr
        try:
            own_pr = supervisor_pr.digest_line(project, store)
        except Exception:  # noqa: BLE001 - git unreadable: the digest still stands, without the line
            own_pr = None
        counted["digest"] = since_digest.digest(picked, start, time.time(), clock.host_zone(), outbox_state=counted["outbox"],
                                                waiting=counted["waiting_on_owner"], preds=counted["predictions"],
                                                model=counted["model"], ages=ages, subsystems=boxes, own_pr=own_pr)
    return {"ok": True, "count": len(picked), **window, **counted, "types": len(summary),
            "by_type": first(({**r, "sources": first(r["sources"], 10)} for r in summary), hint="give types to narrow"),
            "announced_not_heard": first({"type": t, "listened_for_by": first(s, 10)} for t, s in quiet.items()),
            "events": shown, "shown": len(shown),
            "note": (f"{which} (limit {limit}, at most {MAX_LIMIT}){more}; by_type counts them all. A type in "
                     "announced_not_heard is known: a machine announced it, so it may be named")}


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
