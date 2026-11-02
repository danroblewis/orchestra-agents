"""``list_machines``: each machine on the bus as Orchestra's Machines page has it (its view,
``orchestra.web.machines.machines_view``, the one ``/api/machines`` serves, read here over the
last events of the bus): its kind, how it is engaged, whether it is active, and the notes its
instances announced about themselves (the card's ``about``: ``disabled``, ``fake``, ``stand_in``,
``writes``, ...), passed on as they were said. ``disabled`` comes first, so a machine that stopped
itself is never missed when the owner asks what needs them.

**How each is heard** (``state``), from what the core says and nothing else: a machine engaged
ad hoc (it acts when it is offered work) is "last heard <when>; acts when offered work", never
quiet, its silence being its nature (``orchestra/quiet.py``); a continuous or scheduled one is
"active", or "gone quiet" only when the core marked it so (``orchestra.machine.quiet`` from the
host, not followed by an event of the machine), else "last heard <when>". A machine the view shows
as not active is not called quiet here: the T2b walkthrough's Supervisor called a stand-in the
harness was holding "quiet" and told the owner to restart it.

What the page adds from elsewhere (counted traffic, the declared envelope) is not here: read_design
has the declared machines. A machine the declared layer holds is said ``declared`` (who proposed or
confirmed it) and ``running`` (``machines_running``): heard on the bus, or a workflow's program. Nothing is written."""

from __future__ import annotations

import time

from . import machines_running
from .bus_reads import _ts, recent
from .store import AgentStore

SCAN = 20_000                    # the newest events the view is built from, as bus_reads reads them
FIRST = ("disabled", "fake", "stand_in", "writes")
QUIET = "orchestra.machine.quiet"       # the core's mark of a machine gone quiet (orchestra/quiet.py)
WATCHED = ("continuous", "scheduled")   # the modes the core watches for silence


def view(store: AgentStore) -> dict:
    """Orchestra's machines view over the newest events of the bus (tests replace this with an
    answer in /api/machines' shape)."""
    from orchestra.web.machines import machines_view
    bus = store.events.bus
    end = bus.end()
    return machines_view(bus.read(max(0, end - SCAN), SCAN)[0])


def quiet_marks(store: AgentStore) -> dict[str, float]:
    """When the core last marked each machine gone quiet (``orchestra.machine.quiet``, its subject
    or ``data.machine`` the machine), over the newest events (tests replace this)."""
    out: dict[str, float] = {}
    for e in recent(store, SCAN):
        if e.get("type") != QUIET:
            continue
        m = (e.get("data") or {}).get("machine") or e.get("machine") or e.get("subject")
        t = _ts(e)
        if m and t is not None:
            out[m] = max(out.get(m, t), t)
    return out


def when(ts: float | None, now: float | None = None) -> str:
    """A time in the zone the pages show, named: ``18:04 PST``, with the day when it is not today."""
    if ts is None:
        return "never"
    t, today = time.localtime(ts), time.localtime(now if now is not None else time.time())
    fmt = "%H:%M %Z" if t[:3] == today[:3] else "%b %d %H:%M %Z"
    return time.strftime(fmt, t)


def state(m: dict, marked: float | None = None) -> tuple[str, bool]:
    """How the machine is heard, and whether the core marked it gone quiet: ad hoc is never quiet;
    a watched one is quiet only by the core's mark after its last event."""
    mode, last = (m.get("mode") or "").lower(), m.get("last_seen")
    heard = f"last heard {when(last)}" if last is not None else "not heard yet"
    if mode == "ad hoc":
        return f"{heard}; acts when offered work", False
    if m.get("active"):
        return "active", False
    if mode in WATCHED and marked is not None and last is not None and marked >= last:
        return f"gone quiet (Orchestra marked it at {when(marked)}; {heard})", True
    return heard, False


def notes(about: dict | None) -> dict:
    """The announced notes, ``disabled`` first, then the others the card shows, then the rest."""
    about = dict(about or {})
    out = {k: about.pop(k) for k in FIRST if about.get(k) not in (None, "", [], False)}
    out.update({k: v for k, v in sorted(about.items()) if v not in (None, "", [])})
    return out


def line(m: dict, heard: str) -> str:
    said = notes(m.get("about"))
    how = ", ".join(x for x in (m.get("kind"), m.get("mode"), heard) if x)
    return f"{m['id']} ({how})" + ("".join(f"; {k}: {v}" for k, v in said.items()) if said else "")


def list_machines(store: AgentStore, a: dict | None = None) -> dict:
    v = view(store)
    if v.get("ok") is False:
        return {"ok": False, "error": v.get("error") or "the machines view did not answer"}
    rows = [m for m in v.get("machines") or [] if not m.get("bootstrap")]
    marks = quiet_marks(store)
    states = [state(m, marks.get(m["id"])) for m in rows]
    programs = machines_running.workflow_programs(store.project)
    runs = {m["id"]: machines_running.running(m["id"], m.get("intent"), m.get("last_seen"), programs)
            for m in rows if m.get("intent") or m["id"] in programs}
    machines = [{"id": m["id"], "kind": m.get("kind"), "mode": m.get("mode"), "state": heard, "gone_quiet": gone,
                 **({"declared": machines_running.by_whom(m["intent"])} if m.get("intent") else {}),
                 **({"running": runs[m["id"]]} if m["id"] in runs else {}),
                 "package": m.get("package"), "name": m.get("name"), "notes": notes(m.get("about")),
                 "last_seen": m.get("last_seen")} for m, (heard, gone) in zip(rows, states, strict=True)]
    disabled = [f"{m['id']}: {m['notes']['disabled']}" for m in machines if m["notes"].get("disabled")]
    fakes = [f"{m['id']}: {m['notes']['fake']}" for m in machines if m["notes"].get("fake")]
    summary = [line(m, heard) + (f"; {runs[m['id']]}" if m["id"] in runs else "")
               for m, (heard, _) in zip(rows, states, strict=True)]
    if disabled:
        summary.insert(0, "disabled (needs the owner): " + "; ".join(disabled))
    return {"ok": True, "summary": summary, "machines": machines, "disabled": disabled, "fakes": fakes,
            "note": ("notes are what each machine announced about itself (orchestra.machine.started), passed on as said; "
                     "a disabled machine does no work until what it names is fixed; state is how it is heard: an ad hoc machine "
                     "acts when it is offered work, so its silence is no fault; only gone_quiet (the core's mark, on a "
                     "continuous or scheduled machine) means it stopped being heard, not that it stopped running (list_processes "
                     "says whether its process runs): say what the tools returned, and when a tool did not answer, say that; "
                     "running says whether a declared machine runs: one heard on the bus is running, and a program a "
                     "workflow's step names runs as that workflow's program, confirmed or not (unconfirmed is the declared "
                     "layer's state, never a switch that stops it)")}
