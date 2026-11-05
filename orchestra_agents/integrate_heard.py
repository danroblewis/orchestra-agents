"""A relayed system's declared machine joined to the sources it is heard as (bootstrap run 7: Jira,
GitHub and Slack drawn "declared · never heard" beside some 120 of their events).

A kit's poller is a relay, never a machine (Orchestra's rule 2; the bridges' ``kits/poller.py``): it
never publishes under its own name, and every event it relays carries who acted as its ``source``:
``person.<system>.<account>``, ``bot.<system>.<account>`` for a bot account, ``bot.<system>`` for the
system's own act, or ``person.<key>`` for an account the project's identity map lists. So the
system declared as a machine (``design_integrate._declare``) is heard only as those actors, and the
core counts a declared machine heard only by its id or one of its aliases, exactly. The guessed
``bot.<system>`` alone is heard for Jira only when no one acts, and never for GitHub or Slack.

``join`` adds, as aliases, each actor the bus has heard for the system that the declaration does
not hold yet: those whose source names the system (``design_integrate.heard``), then those, such as
a mapped ``person.<key>``, that published one of the system's own event types (``<system>.…``). It
runs when the system is declared and approved, and once a minute in the host (``sweep``), so
"declared · never heard" stays only while nothing of the system is on the bus. Each join is one op
on the declared layer, the Supervisor's, with the sources it adds as its reason.

**Cheap every minute** (the owner, 2026-11-04: hundreds of Jira people). The sweep reads no machines
view: what each actor published is kept here (``Heard``), read over the newest ``SCAN`` events once and
then only over the events the bus added since, so a quiet minute costs a read of nothing new and a set
difference; the declared layer is read once a sweep. A join adds only the actors the declaration does
not hold, so the 600 people of a first join are one op, once, and never again; what it says counts
them past ``SHOWN`` (``people.named``)."""

from __future__ import annotations

from threading import Lock

from . import people
from .config import Project
from .design import MACHINE, apply
from .store import AgentStore

TAG = "integration"
SCAN = 20_000              # the newest events read the first time, as the machines view reads them
ACTORS = ("person.", "bot.")


class Heard:
    """The person and bot sources the bus has heard, each with the event types' first parts it
    published (``jira`` for ``jira.issue.updated``): read once over the newest ``SCAN`` events, then
    only over what the bus added since."""

    def __init__(self) -> None:
        self.offset: int | None = None
        self.actors: dict[str, set[str]] = {}
        self.lock = Lock()

    def update(self, store: AgentStore) -> dict[str, set[str]]:
        bus = store.events.bus
        with self.lock:
            end = bus.end()
            if self.offset is None or self.offset > end:          # first read, or a bus that started again
                self.offset, self.actors = max(0, end - SCAN), {}
            if end > self.offset:
                evs, n = bus.read(self.offset, SCAN)
                for e in evs:
                    src = str(e.get("source") or "").split("/")[0]
                    if src.startswith(ACTORS):
                        self.actors.setdefault(src, set()).add(str(e.get("type") or "").split(".")[0])
                self.offset = max(n, self.offset)
            return self.actors


_HEARD: dict[str, Heard] = {}


def heard_of(store: AgentStore) -> Heard:
    return _HEARD.setdefault(str(store.project.state_dir), Heard())


def heard_as(store: AgentStore, system: str) -> list[str]:
    """The actors heard for ``system``: those whose source names it (``bot.<system>``,
    ``bot.<system>.<account>``, ``person.<system>.<account>``), then the others that published one of
    its event types (a mapped ``person.<key>``)."""
    actors = heard_of(store).update(store)
    named = sorted(s for s in actors if s == f"bot.{system}" or s.startswith((f"bot.{system}.", f"person.{system}.")))
    typed = sorted(s for s, types in actors.items() if system in types)
    return list(dict.fromkeys([*named, *typed]))


SHOWN = 20             # actors named in a join's reason; the rest are counted (the declared layer's text is at most 4000)


def reason_for(system: str, new: list[str]) -> str:
    """A join's reason: the first actors by name, the rest counted, always within the layer's limit
    (the owner, 2026-11-04: hundreds of Jira people made the reason too long, and every sweep failed)."""
    shown = ", ".join(new[:SHOWN]) + (f", and {len(new) - SHOWN} more" if len(new) > SHOWN else "")
    text = f"{system}'s poller relays these {len(new)} actors, heard on the bus: {shown}"
    return text if len(text) <= 3900 else text[:3880] + "…"


def join(project: Project, store: AgentStore, system: str, layer: dict | None = None) -> list[str]:
    """Alias each actor heard for ``system`` to its declared machine; the sources added. ``layer``: the
    declared layer already read (a sweep reads it once)."""
    from . import design_machines
    lay = layer if layer is not None else design_machines.intended_api.current(project)
    m = (lay.get("machines") or {}).get(system)
    if not m or TAG not in (m.get("tags") or []):
        return []
    have = set(m.get("aliases") or [])
    new = [s for s in heard_as(store, system) if s not in have and s != system]
    if new:
        apply(project, [{"op": "alias", "machine": system, "observed": s} for s in new],
              {"author": MACHINE, "source": MACHINE, "reason": reason_for(system, new)})
    return new


def sweep(project: Project, store: AgentStore) -> list[str]:
    """``join`` for every declared relayed system; what was said, one line each, a system's people counted."""
    from . import design_machines
    lay = design_machines.intended_api.current(project)
    out = []
    for mid, m in (lay.get("machines") or {}).items():
        if TAG in (m.get("tags") or []) and m.get("managed") == "external" and (got := join(project, store, mid, lay)):
            out.append(f"machine {mid} heard as {people.named(got)}")
    return out
