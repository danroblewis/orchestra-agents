"""``list_machines``: each machine on the bus as Orchestra's Machines page has it (its view,
``orchestra.web.machines.machines_view``, the one ``/api/machines`` serves, read here over the
last events of the bus): its kind, how it is engaged, whether it is active, and the notes its
instances announced about themselves (the card's ``about``: ``disabled``, ``fake``, ``stand_in``,
``writes``, ...), passed on as they were said. ``disabled`` comes first, so a machine that stopped
itself is never missed when the owner asks what needs them.

What the page adds from elsewhere (the declared layer, counted traffic) is not here: read_design
has the declared machines. Nothing is written."""

from __future__ import annotations

from .store import AgentStore

SCAN = 20_000                    # the newest events the view is built from, as bus_reads reads them
FIRST = ("disabled", "fake", "stand_in", "writes")


def view(store: AgentStore) -> dict:
    """Orchestra's machines view over the newest events of the bus (tests replace this with an
    answer in /api/machines' shape)."""
    from orchestra.web.machines import machines_view
    bus = store.events.bus
    end = bus.end()
    return machines_view(bus.read(max(0, end - SCAN), SCAN)[0])


def notes(about: dict | None) -> dict:
    """The announced notes, ``disabled`` first, then the others the card shows, then the rest."""
    about = dict(about or {})
    out = {k: about.pop(k) for k in FIRST if about.get(k) not in (None, "", [], False)}
    out.update({k: v for k, v in sorted(about.items()) if v not in (None, "", [])})
    return out


def line(m: dict) -> str:
    said = notes(m.get("about"))
    how = ", ".join(x for x in (m.get("kind"), m.get("mode"), "active" if m.get("active") else "quiet") if x)
    return f"{m['id']} ({how})" + ("".join(f"; {k}: {v}" for k, v in said.items()) if said else "")


def list_machines(store: AgentStore, a: dict | None = None) -> dict:
    v = view(store)
    if v.get("ok") is False:
        return {"ok": False, "error": v.get("error") or "the machines view did not answer"}
    rows = [m for m in v.get("machines") or [] if not m.get("bootstrap")]
    machines = [{"id": m["id"], "kind": m.get("kind"), "mode": m.get("mode"), "active": bool(m.get("active")),
                 "package": m.get("package"), "name": m.get("name"), "notes": notes(m.get("about")),
                 "last_seen": m.get("last_seen")} for m in rows]
    disabled = [f"{m['id']}: {m['notes']['disabled']}" for m in machines if m["notes"].get("disabled")]
    fakes = [f"{m['id']}: {m['notes']['fake']}" for m in machines if m["notes"].get("fake")]
    summary = [line(m) for m in rows]
    if disabled:
        summary.insert(0, "disabled (needs the owner): " + "; ".join(disabled))
    return {"ok": True, "summary": summary, "machines": machines, "disabled": disabled, "fakes": fakes,
            "note": ("notes are what each machine announced about itself (orchestra.machine.started), passed on as said; "
                     "a disabled machine does no work until what it names is fixed")}
