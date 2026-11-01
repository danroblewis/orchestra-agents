"""Confirm and discard (machine-design.md §2.4, §2.5), and the owner's page requests.

The owner confirms; the Supervisor applies. A click on the Machines page publishes
``orchestra.intent.requested`` with source ``owner`` and changes nothing; it wakes the Supervisor
(``wake_reason``), which calls ``confirm``, ``discard`` or ``refine_machine`` (``set_kind``) with
the request's id, as it does for "confirm it" in the chat.

What confirming a machine does here: the op on the declared layer; for a Universal Machine, its
draft implementation moves from ``drafts/<name>/`` to ``machines/<name>/`` in the state directory
(the host loads it from there; nothing is committed), the id of the ``system.changed`` that will
announce its start is chosen now, and its expected effect is published as the Supervisor's
prediction of that change (``orchestra.prediction.made``, subject ``machine:<name>``). The
Supervisor then creates its role and its agent; the agent's first turn publishes that
``system.changed`` (``announce_start``, from the host), which begins the era the prediction is
judged in. Until confirm nothing runs and nothing is published. Discard leaves nothing but the
history."""

from __future__ import annotations

import shutil
import time
import uuid

from . import intended_api
from .config import Project
from .design import MACHINE, REQUESTED, answer, apply
from .design_machines import current, draft, drafts_dir, publishable
from .store import AgentStore, StoreError

PARTS = ("stock", "effect", "flow", "machine", "attachment")


def machines_dir(project: Project):
    return project.state_dir / "machines"


def _element(a: dict) -> tuple[str, str]:
    part, _, eid = (a.get("element") or "").strip().partition(":")
    if part not in PARTS or not eid:
        raise StoreError("element is part:id, as machine:feature_count_improver or stock:features")
    return part, eid


def _publish(project: Project, store: AgentStore, m: dict) -> tuple[str, dict | None, list]:
    """The change id the machine's start will carry, and the prediction published about it."""
    from orchestra.sd.predictions import PredictionError, Predictions
    change = uuid.uuid4().hex
    effect = m.get("expected_effect") or {}
    layer = intended_api.current(project)
    ready = [p for e in effect.get("edits") or [] if (p := publishable(layer, e)) is not None]
    waiting = [e for e in effect.get("edits") or [] if publishable(layer, e) is None]
    made = None
    if ready:
        try:
            made = Predictions(project.orchestra, MACHINE, events=store.events).record(
                ready, float(effect.get("horizon_s") or 0), change=change, subject=f"machine:{m['id']}")
        except PredictionError as e:
            raise StoreError(f"Orchestra refused the expected effect as a prediction: {e}; nothing was confirmed") from None
    starts = dict(store.agent_state(MACHINE).get("machine_starts") or {})
    starts[m["id"]] = {"change": change, "prediction": (made or {}).get("id"), "at": time.time(), "announced": None}
    store.set_agent_state(MACHINE, machine_starts=starts)
    return change, made, waiting


def confirm(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    part, eid = _element(a)
    pending = (a.get("change") or "").strip() or None
    if part != "machine" or pending:
        done = apply(project, store, [{"op": "confirm", "part": part, "id": eid, **({"change": pending} if pending else {})}], prov)
        if part == "machine":
            draft(project, current(project, eid))
        return answer(project, done, [f"{part} {eid}: {'change ' + pending if pending else 'it'} confirmed"])
    m = current(project, eid)
    if m.get("state") == "declared":
        raise StoreError(f"machine {eid} is confirmed already")
    if prov["author"] != "owner" and not prov.get("request"):
        raise StoreError("confirming a machine is the owner's: quote their \"confirm it\" (quote) or pass the request id of their "
                         "click (request)")
    ops = [{"op": "confirm", "part": "machine", "id": eid}]
    lines, extra = [f"machine {eid} confirmed: the Machines page draws it solid, \"declared · not heard yet\""], {}
    if m.get("kind") == "universal":
        src, dst = drafts_dir(project) / eid, machines_dir(project) / eid
        if not src.exists():
            draft(project, m)
        change, made, waiting = _publish(project, store, m)
        if made:
            ops.append({"op": "record_prediction", "machine": eid, "prediction": made.get("id")})
        done = apply(project, store, ops, prov)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.move(str(src), str(dst))
        e = m.get("engagement") or {}
        when = f"at {e.get('at')}" if e.get("mode") == "scheduled" and e.get("at") else f"woken by {', '.join(m.get('receives') or [])}"
        lines += [f"its spec is at {dst}; nothing was committed",
                  (f"prediction {made.get('id')} published as the Supervisor, about change {change}: judged "
                   f"{float(m['expected_effect']['horizon_s']) / 86_400:g} days after its first turn" if made else
                   "no prediction published: " + ("its expected edits wait for a series" if waiting else "it has no expected effect")),
                  f"next: create_role {eid} (its role.md as the prompt), then create_agent {eid} ({when}), on haiku"]
        extra = {"change": change, "prediction": (made or {}).get("id"), "spec": str(dst)}
    else:
        done = apply(project, store, ops, prov)
        if m.get("managed") == "orchestra":
            lines.append("next: the program is a worker's to write (hand_to_coordinator) unless it exists; when it does, "
                         "set_machine adds its [[machines]] entry, in the overlay")
        else:
            lines.append("nothing runs here: the declaration waits to be heard on the bus or measured")
    return answer(project, done, lines, machine=eid, **extra)


def discard(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    part, eid = _element(a)
    pending = (a.get("change") or "").strip() or None
    done = apply(project, store, [{"op": "discard", "part": part, "id": eid, **({"change": pending} if pending else {})}], prov)
    if part == "machine" and not pending:
        shutil.rmtree(drafts_dir(project) / eid, ignore_errors=True)
    said = f"change {pending} on {part} {eid} discarded" if pending else f"{part} {eid} discarded: gone from the page, kept in history"
    return answer(project, done, [said])


def wake_reason(ev: dict) -> str | None:
    """What the Supervisor is told when the owner clicks a request on a page. ``ev`` is flat (its
    data's fields beside the envelope's, ``events.flat``): the data's ``id`` gives way to the
    event's, so the element is read from the subject (``machine:<id>``) when the data's is gone."""
    if ev.get("type") != REQUESTED:
        return None
    control, part, change = ev.get("control"), ev.get("part"), ev.get("change")
    s_part, _, s_id = str(ev.get("subject") or "").partition(":")
    part, eid = part or s_part, s_id or ev.get("element")
    call = {"confirm": "confirm", "discard": "discard", "switch_kind": "refine_machine with set_kind"}.get(control, control)
    element = f"{part}:{eid}"
    return (f"event {REQUESTED} request={ev.get('id')}: the owner clicked {control} on {element}"
            + (f" (change {change})" if change else "") + f" on the {ev.get('from') or 'page'}. "
            f"Do it now: {call} with element {element}" + (f", change {change}" if change else "")
            + f" and request {ev.get('id')}; then tell the owner in a line.")


def announce_start(store: AgentStore, name: str) -> str | None:
    """At a confirmed machine's first turn, publish the ``system.changed`` its prediction is about
    (as the machine, with the id chosen at confirm); the change id, or None."""
    starts = dict(store.agent_state(MACHINE).get("machine_starts") or {})
    rec = starts.get(name)
    if not rec or rec.get("announced"):
        return None
    store.events.publish("system.changed", {"machine": name, "note": f"{name} starts: its first turn",
                                            "prediction_change": rec["change"]},
                         source=name, id=rec["change"], subject=f"machine:{name}")
    rec["announced"] = time.time()
    store.set_agent_state(MACHINE, machine_starts=starts)
    return rec["change"]


COMMANDS = {"confirm": confirm, "discard": discard}
