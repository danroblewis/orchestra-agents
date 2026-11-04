"""``retry_step <item> <step>``: the Supervisor asks the Workflows floor to offer a blocked or stalled
step again now, once the cause is fixed (T48: the standing demo's SCRUM-56 sat at ``write`` blocked
because every worktree's git fetch and push were refused over ssh; the Supervisor said so four
times and had nothing to do about it, and the floor's back-off, W16, waits up to an hour between
tries).

It goes through the plugin's contract, never its files: ``item.retry_requested`` (orchestra-workflows
W17, its ``docs/machine-contract.md``; the name copied in ``floor.py``) about the item, from the
Supervisor as the item's planner, naming the step and the reason. The floor enters the step again
(its tries counted from nothing) and offers it on the tick that reads the request. That event is
the Supervisor's act on the bus, with its reason, and the ledger's line.

Checked first, through the plugin's record API (``Record.items``: the item's positions, as
``explain_item`` reads them): the item is open, and at that step its turn finished ``blocked`` or
``error`` (the position's ``retry``, or its ``last_try``), or a guard moved it from that step to the
one it is at (``stalled``: the position's ``came_from``); and the workflow's planner is the
Supervisor, since the floor counts the request from the planner alone. Anything else is refused,
nothing published, in words that say where the item is."""

from __future__ import annotations

from .config import Project
from .floor import RETRIED, RETRY_REQUESTED
from .store import AgentStore, StoreError

ROLE = "supervisor"


def _failed(pos: dict) -> bool:
    last = pos.get("last_try") if isinstance(pos.get("last_try"), dict) else {}
    return bool(pos.get("retry")) or last.get("outcome") in RETRIED


def _where(pos: dict) -> str:
    last = pos.get("last_try") if isinstance(pos.get("last_try"), dict) else {}
    held = str(pos.get("pile") or "").endswith(".held")
    return (f"at {pos.get('step')}" + (" (held: its turn runs)" if held else "")
            + (f", its last turn {last.get('outcome')}" if last.get("outcome") else ""))


def retry_step(project: Project, store: AgentStore, a: dict, role: str) -> dict:
    if role != ROLE:
        raise StoreError("only the supervisor asks the floor to try a step again")
    from .workflow_tools import _plugin
    p = _plugin()
    item, step = str(a.get("item") or "").strip(), str(a.get("step") or "").strip()
    reason = str(a.get("reason") or "").strip()
    if not item or not step:
        raise StoreError("item is the item's id and step the step to offer again (explain_item shows both)")
    if len(reason) < 10:
        raise StoreError("give the reason in a sentence: what caused the step to fail and what you fixed. Retry only "
                         "after the cause is fixed: it goes on the bus as your act")
    rec = p.Record.for_project(project.orchestra)
    row = next(iter(rec.items(lambda s: s.get("item") == item)), None)
    if row is None:
        raise StoreError(f"no open item {item!r} in the Workflows record (list_items shows the open ones): nothing "
                         "to retry")
    positions = {w: pos for w, pos in (row.get("at") or {}).items() if isinstance(pos, dict)}
    held = {w for w, pos in positions.items() if str(pos.get("pile") or "").endswith(".held")}
    fits = [w for w, pos in positions.items() if w not in held and not pos.get("offer")
            and ((pos.get("step") == step and _failed(pos)) or pos.get("came_from") == step)]
    if not fits:
        where = "; ".join(f"{w}: {_where(pos)}" for w, pos in positions.items()) or "no workflow holds it"
        raise StoreError(f"refused, nothing published: {item} is neither blocked nor stalled at {step} ({where}). "
                         "retry_step tries again only a step whose turn finished blocked or error, or the step an item "
                         "was moved to stalled from; explain_item says where it is and why")
    workflow = fits[0]
    project.reload_config()
    w = next((x for x in p.define.load(project.orchestra).workflows if x.name == workflow), None)
    planner = getattr(w, "planner", None)
    if planner not in (None, ROLE):
        raise StoreError(f"refused, nothing published: the floor counts a retry only from {workflow}'s planner, "
                         f"{planner}, not the supervisor; hand it to the {planner} (message_agent) with the fix")
    pos = positions[workflow]
    ev = store.events.publish(RETRY_REQUESTED, {"step": step, "workflow": workflow, "reason": reason},
                              source=ROLE, subject=item)
    from .request_work import floor_down
    down = floor_down(project)
    stalled = pos.get("step") != step
    return {"ok": True, "item": item, "step": step, "workflow": workflow, "requested": ev.get("id"),
            "was": f"at {pos.get('step')} (moved there from {step})" if stalled else f"at {step}, {_where(pos)}",
            "said": (f"Asked the floor to offer {item}'s {step} again now: it enters the step again, its tries counted "
                     "from nothing, and offers it on its next tick, a second or so from now"
                     + (f"; but {down}" if down else "") + ". Tell the owner what you fixed and that the step is "
                     "tried again; explain_item shows the new offer, and if it fails again its summary says why.")}
