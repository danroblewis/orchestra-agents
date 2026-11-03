"""The digest's one line on subsystems (``declare_subsystem``'s proposing side; Orchestra's
``docs/proposals/subsystems.md`` B5, B8), at most once a day: the Supervisor's own proposals still
waiting for the owner (no box until they confirm), else the model's default partition when it is
worth a name: two groups at least, more flows kept inside a group than crossing between groups,
and none of its values named by a subsystem. The crossing count is said, never judged. Read only
but for when the line was last said (the Supervisor's agent state, ``subsystem_line_at``)."""

from __future__ import annotations

import time

from . import intended_api
from .design import MACHINE, plural
from .store import AgentStore

DAY_S = 86_400.0


def _proposals(layer: dict) -> list[dict]:
    return [x for x in (layer.get("subsystems") or {}).values()
            if x.get("state") == "proposed" and (x.get("provenance") or {}).get("author") == MACHINE]


def _crossing(p: dict) -> str:
    pairs = [f"{c['between'][0]}–{c['between'][1]}" for c in p.get("crossing") or [] if c.get("count")]
    return plural(p.get("crossed") or 0, "flow") + " crossing" + (f" ({', '.join(pairs[:3])})" if pairs else "")


def line_now(layer: dict, model) -> str | None:
    """The line, whether or not it was said today."""
    if mine := _proposals(layer):
        said = "; ".join(f"'{x['name']}' over {x.get('label')}={x.get('value')}" for x in mine[:3])
        return (f"Subsystems: proposed by me, no box until you confirm: {said}"
                + (f" and {len(mine) - 3} more" if len(mine) > 3 else "") + ".")
    subs = intended_api.partitions(model)
    p = next((x for x in subs.get("partitions") or [] if x["label"] == subs.get("default")), None)
    if p is None or len(p["groups"]) < 2 or (p.get("joined") or 0) <= (p.get("crossed") or 0):
        return None
    named = {(x.get("label"), x.get("value")) for x in (layer.get("subsystems") or {}).values()}
    if any(x.get("members") for x in (layer.get("subsystems") or {}).values()) or \
            any((p["label"], g["value"]) in named for g in p["groups"]):
        return None
    values = ", ".join(g["value"] for g in p["groups"][:5])
    return (f"Subsystems: the model separates best by {p['label']} ({plural(len(p['groups']), 'box', 'boxes')}: {values}), "
            f"{plural(p.get('joined') or 0, 'flow')} inside a box, {_crossing(p)}; none is named: a name in your words "
            "titles its box on both maps.")


def line(project, store: AgentStore, now: float | None = None) -> str | None:
    """The line for the digest, at most once a day; None otherwise, or when there is nothing to say."""
    now = time.time() if now is None else now
    last = store.agent_state(MACHINE).get("subsystem_line_at")
    if last is not None and now - float(last) < DAY_S:
        return None
    said = line_now(intended_api.current(project), intended_api.current_model(project))
    if said:
        store.set_agent_state(MACHINE, subsystem_line_at=now)
    return said
