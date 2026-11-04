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
on the declared layer, the Supervisor's, with the sources it adds as its reason."""

from __future__ import annotations

from .config import Project
from .design import MACHINE, apply
from .store import AgentStore

TAG = "integration"


def _typed(store: AgentStore, system: str) -> list[str]:
    """The actors that published one of ``system``'s event types, as the Machines page has them."""
    from .machines_read import view
    try:
        rows = view(store).get("machines") or []
    except Exception:  # noqa: BLE001 - without the view nothing more is joined
        return []
    pre = f"{system}."
    return [m["id"] for m in rows if str(m.get("id") or "").startswith(("person.", "bot."))
            and any(str(t).startswith(pre) for t in m.get("publishes") or [])]


def heard_as(store: AgentStore, system: str) -> list[str]:
    from .design_integrate import heard
    return list(dict.fromkeys([*heard(store, system), *_typed(store, system)]))


def join(project: Project, store: AgentStore, system: str) -> list[str]:
    """Alias each actor heard for ``system`` to its declared machine; the sources added."""
    from . import design_machines
    m = (design_machines.intended_api.current(project).get("machines") or {}).get(system)
    if not m or TAG not in (m.get("tags") or []):
        return []
    have = set(m.get("aliases") or [])
    new = [s for s in heard_as(store, system) if s not in have and s != system]
    if new:
        apply(project, [{"op": "alias", "machine": system, "observed": s} for s in new],
              {"author": MACHINE, "source": MACHINE, "reason": f"{system}'s poller relays these actors, heard on the bus: "
                                                               + ", ".join(new)})
    return new


def sweep(project: Project, store: AgentStore) -> list[str]:
    """``join`` for every declared relayed system; what was said, one line each."""
    from . import design_machines
    out = []
    for mid, m in (design_machines.intended_api.current(project).get("machines") or {}).items():
        if TAG in (m.get("tags") or []) and m.get("managed") == "external" and (got := join(project, store, mid)):
            out.append(f"machine {mid} heard as {', '.join(got)}")
    return out
