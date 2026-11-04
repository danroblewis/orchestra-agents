"""What has died, the very first thing the "since" digest says (``since_digest``), before any item
that looks stuck (T2k: the Outbox sender crashed five times after a restart and the launcher stopped
trying it at 01:17; the digest led with three day-old attentions and said only "Now: not running"
in the Outbox line, though SCRUM-47 waited at ``close_ticket`` on that sender).

Two kinds, from Orchestra's runtime record (``orchestra.runtime.read``: what the launcher and the
collectors record for the Core page; nothing else is read) and the window's events:

- **a process the launcher stopped retrying**: its record says so (``state`` ``gave_up``; or, once
  the launcher backs off and keeps trying, a line of its record saying it ``stopped after N
  crashes`` with its next try), or it has an exit code, no pid and is not waiting to start again.
  When it died is the window's ``orchestra.process.gave_up`` for it, else its last
  ``orchestra.process.exited``;
- **an instrument that cannot fetch its target**: its last round failed after its last write (or it
  never wrote), with the error and when it last wrote.

Each process is named with the open items it blocks: an item waiting at a step that process serves
("SCRUM-47 (#51) waits at close_ticket on the stopped outbox.sender since 01:16"). Which steps a
process serves is read from the step's actor (``item_state``'s ``actors``) and the process's
owner and name, never configured: an outbox step by the outbox plugin's processes, a program's step
by the process of that name or the Workflows plugin's program host, an agent's step by the agents'
host, an outside system's by a process named for it (``github_live`` for ``github``), and every step
by the Workflows plugin's floor, which moves them all. A guess, said as "waits on", never "because
of"."""

from __future__ import annotations

import re

from .event_tally import field

STOPPED = re.compile(r"stopped after (\d+) crash", re.IGNORECASE)
NEXT = ("next_try_at", "next_at", "start_at")


def record(project) -> dict:
    """The launcher's processes and the instruments, as Orchestra's runtime record has them."""
    from orchestra import runtime
    rec = runtime.read(project.orchestra)
    return {"processes": rec.get("processes") or {}, "instruments": rec.get("instruments") or {}}


def _said(r: dict) -> str | None:
    """The record's own words that it stopped retrying (the launcher's, once it says them)."""
    for v in r.values():
        if isinstance(v, str) and STOPPED.search(v):
            return v
    return None


def stopped(r: dict) -> bool:
    if not isinstance(r, dict):
        return False
    if r.get("state") == "gave_up" or _said(r):
        return True
    return r.get("pid") is None and r.get("code") not in (None, 0) and r.get("state") not in ("waiting", "running")


def failing(r: dict) -> bool:
    if not isinstance(r, dict) or not isinstance(r.get("last_error_at"), int | float):
        return False
    return not isinstance(r.get("last_write"), int | float) or r["last_error_at"] > r["last_write"]


def _died(d, name: str) -> tuple[float | None, dict | None]:
    """When the window's events say the process died: its give-up, else its last exit."""
    gave = exited = None
    for t, e in d.all:
        if field(e, "name") != name:
            continue
        if e.get("type") == "orchestra.process.gave_up":
            gave = (t, e)
        elif e.get("type") == "orchestra.process.exited":
            exited = (t, e)
    return gave or exited or (None, None)


def serves(name: str, r: dict, actor: tuple[str, str] | None) -> bool:
    """Whether the process ``name`` (its record ``r``) serves a step whose actor is ``actor``."""
    owner = str(r.get("owner") or "project")
    if name == "workflows.floor":
        return True
    if not actor:
        return False
    kind, who = actor
    if kind == "outbox":
        return owner == "outbox"
    if kind == "program":
        return name in (who, "workflows.programs") or name.endswith("." + str(who))
    if kind == "role":
        return name == "agents.host"
    if kind == "outside":
        return bool(who) and (name == who or re.match(rf"{re.escape(str(who))}[._-]", name) is not None)
    return False


def _actor(row: dict, actors: dict) -> tuple[str, str] | None:
    got = actors.get((row.get("workflow"), row.get("step")))
    if got:
        return tuple(got)
    acts = row.get("acts") if isinstance(row.get("acts"), dict) else {}
    wait = row.get("wait") if isinstance(row.get("wait"), dict) else {}
    if acts.get("kind") in ("outbox", "program", "role", "outside"):
        return acts["kind"], str(acts.get("who") or "")
    if "outbox" in " ".join(str(wait.get(k) or "") for k in ("why", "text", "kind")).lower():
        return "outbox", ""
    return None


def dead(d, rec: dict | None, state: dict | None) -> tuple[list[tuple[str, list[str]]], set[str]]:
    """``([(what, [line])], {items named})``: the stopped processes, newest death first, each with
    the items it blocks; then the instruments that cannot fetch. Nothing without the record."""
    if not rec:
        return [], set()
    rows = (state or {}).get("rows") or []
    actors = (state or {}).get("actors") or {}
    now = d.clock.now
    procs, named = [], set()
    for name, r in sorted((rec.get("processes") or {}).items()):
        if not stopped(r):
            continue
        t, e = _died(d, name)
        crashes = field(e, "failures") if e is not None else None
        said = _said(r)
        how = [f"exit code {r['code']}" if r.get("code") not in (None, 0) else None,
               f"{crashes} crashes in a row" if isinstance(crashes, int) and not said else None]
        nxt = next((r[k] for k in NEXT if isinstance(r.get(k), int | float) and r[k] > now), None)
        line = (f"{name}" + (f" ({r['owner']} plugin's)" if r.get("owner") not in (None, "project") else "") +
                (f" stopped at {d.clock.at(t)}" if t else " stopped") +
                (f" ({', '.join(x for x in how if x)})" if any(how) else "") +
                (f": \"{said}\"" if said else "") +
                (f", next try at {d.clock.at(nxt)}" if nxt and not said else "" if said else ", not tried again"))
        blocked = []
        for row in sorted(rows, key=lambda x: x.get("age_s") or 0):              # the newest first
            if serves(name, r, _actor(row, actors)):
                item = str(row.get("item") or "?")
                age = row.get("age_s")
                since = f" since {d.clock.at(now - age)}" if isinstance(age, int | float) else ""
                blocked.append(f"{d.item(item)} waits at {row.get('step')} on the stopped {name}{since}")
                named.add(item)
        procs.append((t or 0.0, line + ("; " + "; ".join(blocked) if blocked else "")))
    out = []
    if procs:
        out.append(("a process the launcher stopped retrying", [x for _, x in sorted(procs, key=lambda x: -x[0])]))
    fails = []
    for name, r in (rec.get("instruments") or {}).items():
        if not failing(r):
            continue
        wrote = r.get("last_write")
        err = " ".join(str(r.get("last_error") or "its round failed").split())
        fails.append((r["last_error_at"], f"{name} ({r.get('target') or r.get('kind') or '?'}): {err[:120]}; " +
                      (f"nothing written since {d.clock.at(wrote)}" if isinstance(wrote, int | float) else "never written")))
    if fails:
        out.append(("an instrument that cannot fetch its target", [x for _, x in sorted(fails, key=lambda x: -x[0])]))
    return out, named
