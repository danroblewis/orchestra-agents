"""Confirm and discard (machine-design.md §2.4, §2.5), and the owner's page requests.

The owner confirms; the Supervisor applies. A click on the Machines page publishes
``orchestra.intent.requested`` with source ``owner`` and changes nothing; it wakes the Supervisor
(``wake_reason``; the host wakes it only for one whose source is ``owner``), which calls
``confirm``, ``discard`` or ``refine_machine`` (``set_kind``) with the request's id, as it does for
"confirm it" in the chat. Every confirm and discard, of any part and of a pending change, is
backed by the owner: their quote, or their request for exactly that (``design.check_request``).

What confirming a Universal Machine does here, in this order: the op on the declared layer (with
the id of the ``system.changed`` that will announce its start, chosen first); its implementation
written to the state directory's ``machines/<name>/`` from the declaration, with its role and its
agent records (``state_machines``: the host runs it from there; nothing is committed); then its
expected effect published as the Supervisor's prediction of that change
(``orchestra.prediction.made``, subject ``machine:<name>``) and recorded on the declaration in a
second call. The agent's first turn publishes that ``system.changed`` (``announce_start``, from the
host), which begins the era the prediction is judged in. An edit that waits for a series is
published once its stock is linked (``publish_waiting``), and a prediction an era interrupts is
renewed for the new era (``renew``). Until confirm nothing runs and nothing is published. Discard
leaves nothing but the history."""

from __future__ import annotations

import shutil
import time
import uuid

from . import intended_api, state_machines
from .config import Project
from .design import MACHINE, REQUESTED, answer, apply, check_request, page_line
from .design_dynamics import resolve
from .design_machines import cannot_judge, current, draft, drafts_dir, publishable
from .store import AgentStore, StoreError

PARTS = ("stock", "effect", "flow", "machine", "attachment")
OWNER = "owner"


def machines_dir(project: Project):
    return state_machines.root(project)


def _element(project: Project, a: dict) -> tuple[str, str]:
    """``part:id``, or a bare declared id resolved as the other tools resolve one."""
    given = (a.get("element") or "").strip()
    part, sep, eid = given.partition(":")
    if sep:
        if part not in PARTS or not eid:
            raise StoreError("element is part:id, as machine:feature_count_improver or stock:features, or a declared id")
        return part, eid
    part, eid, declared = resolve(project, given)
    if not declared:
        raise StoreError(f"{given!r} is not declared: give part:id (machine:feature_count_improver), as read_design lists them")
    return part, eid


def _starts(store: AgentStore, fn) -> dict:
    def change_it(d):
        d = dict(d or {})
        fn(d)
        return d
    return store.update_agent_state(MACHINE, "machine_starts", change_it)


def _predict(project: Project, store: AgentStore, mid: str, edits: list[dict], horizon_s: float, change: str) -> dict:
    from orchestra.sd.predictions import PredictionError, Predictions
    try:
        return Predictions(project.orchestra, MACHINE, events=store.events).record(edits, horizon_s, change=change,
                                                                                   subject=f"machine:{mid}")
    except PredictionError as e:
        raise StoreError(f"Orchestra refused the expected effect as a prediction: {e}") from None


def _ready(layer: dict, m: dict, done: list | tuple = ()) -> tuple[list[dict], list[dict]]:
    """The expected edits publishable now (as published: linked ends read as their series) and
    those that wait, leaving out the ones already published (``done``, as published)."""
    ready, waiting = [], []
    for e in (m.get("expected_effect") or {}).get("edits") or []:
        p = publishable(layer, e)
        if p is not None and p in done:
            continue
        if p is None or cannot_judge(e):
            waiting.append(e)
        else:
            ready.append(p)
    return ready, waiting


def confirm(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    part, eid = _element(project, a)
    pending = (a.get("change") or "").strip() or None
    check_request(project, store, prov, "confirm", part, eid, pending)
    if part != "machine" or pending:
        done = apply(project, store, [{"op": "confirm", "part": part, "id": eid, **({"change": pending} if pending else {})}], prov)
        lines = [f"{part} {eid}: {'change ' + pending if pending else 'it'} confirmed"]
        if part == "machine":
            m = current(project, eid)
            draft(project, m)
            if m.get("state") == "declared" and m.get("kind") == "universal":
                _restart(project, store, eid)
                lines.append(f"its spec, role and agent records in {machines_dir(project) / eid} are written again from the "
                             "declaration (no update_role or update_agent is needed); a running session starts over with them")
        lines += _refinement_after(project, store, prov)
        return answer(project, done, lines)
    m = current(project, eid)
    if m.get("state") == "declared":
        raise StoreError(f"machine {eid} is confirmed already")
    lines, extra = [f"machine {eid} confirmed", page_line(project, "the Machines page draws it solid, \"declared · not heard yet\"")], {}
    if m.get("kind") == "universal":
        change = uuid.uuid4().hex                        # the id of the system.changed its first turn will publish
        state_machines.files(m)                          # its records check before anything is written
        done = apply(project, store, [{"op": "confirm", "part": "machine", "id": eid}], prov)
        m = current(project, eid)
        dst = state_machines.write(project, m)
        shutil.rmtree(drafts_dir(project) / eid, ignore_errors=True)
        _starts(store, lambda d: d.__setitem__(eid, {"change": change, "prediction": None, "published": [], "at": time.time(),
                                                     "announced": None}))
        ready, waiting = _ready(intended_api.current(project), m)
        try:
            made = _publish(project, store, eid, ready, m, change) if ready else None
        except StoreError as e:                          # the confirm stands; the prediction is said to be missing
            made = None
            lines.append(f"its expected effect was not published: {e}; it is confirmed, with no prediction yet")
        e = m.get("engagement") or {}
        when = f"at {e.get('at')} each day" if e.get("mode") == "scheduled" else f"woken by {', '.join(m.get('receives') or [])}"
        lines += [(f"its spec, role and agent records are at {dst} (on {state_machines.DEFAULT_MODEL}, {when}); nothing was "
                   "committed, and the host runs it from there"),
                  (f"prediction {made} published as the Supervisor, about change {change}: judged "
                   f"{float(m['expected_effect']['horizon_s']) / 86_400:.3g} days after its first turn" if made else
                   "no prediction published yet: " + ("its expected edits wait for a series" if waiting else "it has no expected effect"))]
        if made and waiting:
            lines.append(f"{len(waiting)} edit(s) wait for a series and are published once linked")
        extra = {"change": change, "prediction": made, "spec": str(dst)}
    else:
        done = apply(project, store, [{"op": "confirm", "part": "machine", "id": eid}], prov)
        if m.get("managed") == "orchestra":
            lines.append("next: the program is a worker's to write (hand_to_coordinator) unless it exists; when it does, "
                         "set_machine adds its [[machines]] entry, in the overlay")
        else:
            lines.append("nothing runs here: the declaration waits to be heard on the bus or measured")
    lines += _refinement_after(project, store, prov)
    return answer(project, done, lines, machine=eid, **extra)


def _publish(project: Project, store: AgentStore, mid: str, edits: list[dict], m: dict, change: str) -> str | None:
    """Publish ``edits`` as the Supervisor's prediction of ``change`` and record it on the declaration."""
    made = _predict(project, store, mid, edits, float((m.get("expected_effect") or {}).get("horizon_s") or 0), change)
    pid = made.get("id")
    apply(project, store, [{"op": "record_prediction", "machine": mid, "prediction": pid}],
          {"author": MACHINE, "source": MACHINE, "reason": f"the prediction published for {mid}'s change {change}"})
    _starts(store, lambda d: d.setdefault(mid, {}).update(prediction=pid, published=[*d[mid].get("published", []),
                                                                                    {"prediction": pid, "change": change,
                                                                                     "edits": edits}]))
    return pid


def publish_waiting(project: Project, store: AgentStore) -> list[str]:
    """Publish the expected edits of confirmed machines that waited for a series and now have one
    (after a ``link``, or on the host's tick); what was said."""
    starts = store.agent_state(MACHINE).get("machine_starts") or {}
    layer = intended_api.current(project)
    said = []
    for mid, rec in starts.items():
        m = (layer.get("machines") or {}).get(mid)
        if m is None or m.get("state") != "declared":
            continue
        ready, _ = _ready(layer, m, done=[x for p in rec.get("published") or [] for x in p.get("edits") or []])
        if not ready:
            continue
        change, fresh = rec["change"], bool(rec.get("announced"))
        if fresh:                       # its start was announced already: a change of its own, which its next turn announces
            change = uuid.uuid4().hex
            _starts(store, lambda d, c=change, k=mid: d[k].update(next=c))
        pid = _publish(project, store, mid, ready, m, change)
        said.append(f"prediction {pid} published for {mid}'s waiting edit(s), about change {change}"
                    + (" (a fresh change, announced at its next turn: its start was announced before the series was linked)"
                       if fresh else ""))
    return said


def renew(project: Project, store: AgentStore, ev: dict) -> str | None:
    """A designed machine's prediction judged ``interrupted``: the same edits recorded again as the
    Supervisor's prediction of the change that began the new era; what to tell the Supervisor."""
    if ev.get("type") != "orchestra.prediction.judged" or ev.get("verdict") != "interrupted":
        return None
    starts = store.agent_state(MACHINE).get("machine_starts") or {}
    for mid, rec in starts.items():
        hit = next((p for p in rec.get("published") or [] if p.get("change") == ev.get("correlationid")), None)
        if hit is None:
            continue
        new = _era_cause(project, ev.get("interrupted_by"))
        if new is None:
            return (f"event orchestra.prediction.judged: {mid}'s prediction {hit['prediction']} was interrupted (era "
                    f"{ev.get('interrupted_by')}), and the change that began that era cannot be read, so it was not renewed: "
                    "say so to the owner")
        pid = _publish(project, store, mid, hit["edits"], current(project, mid), new)
        return (f"event orchestra.prediction.judged: {mid}'s prediction {hit['prediction']} was interrupted by change {new}; "
                f"its edits were recorded again as prediction {pid}, about that change. Tell the owner in a line if they ask.")
    return None


def _era_cause(project: Project, era: str | None) -> str | None:
    """The id of the event that began era ``era`` (a prediction is about that change)."""
    if not era:
        return None
    from orchestra.sd.eras import recorded
    from orchestra.sd.store import ModelStore
    return next((e.cause for e in recorded(ModelStore.for_state(project.orchestra.state_dir)) if e.id == era), None)


def _restart(project: Project, store: AgentStore, name: str) -> None:
    from .org_store import all_agents
    ag = all_agents(project).get(name)
    if ag is not None and ag.resident:
        store.set_agent_state(name, restart_requested=True)


def _refinement_after(project: Project, store: AgentStore, prov: dict) -> list[str]:
    """After the owner's confirm or discard, the refinement pass over the map as it is now (§3)."""
    from .design_passes import DUTY, has_duty, run
    if prov.get("author") != OWNER and not prov.get("request"):
        return []
    if not has_duty(project):
        return [f"no refinement pass runs: your spec has no {DUTY} duty"]
    try:
        got = run(project, store, store.events.read(max(0, store.events.end() - 5000), limit=5000)[0], force=True)
    except Exception as e:  # noqa: BLE001 - the confirm stands without its pass
        return [f"the refinement pass did not run: {e}"]
    if not got or not got["findings"]:
        return ["the refinement pass over the map found nothing new"]
    return [f"the refinement pass found {len(got['findings'])} thing(s) to put to the owner (read_design, refinement): "
            + "; ".join(f["text"] for f in got["findings"][:3])]


def discard(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    from .design_instruments import remove_for
    part, eid = _element(project, a)
    pending = (a.get("change") or "").strip() or None
    check_request(project, store, prov, "discard", part, eid, pending)
    done = apply(project, store, [{"op": "discard", "part": part, "id": eid, **({"change": pending} if pending else {})}], prov)
    said = [f"change {pending} on {part} {eid} discarded" if pending else f"{part} {eid} discarded: kept only in the history"]
    if part == "machine" and not pending:
        shutil.rmtree(drafts_dir(project) / eid, ignore_errors=True)
    if part in ("stock", "effect") and not pending and (gone := remove_for(project, store, f"{part}:{eid}",
                                                                             f"{part} {eid} was discarded")):
        said.append(f"its instruments removed, entries and scripts: {', '.join(gone)}")
    said += _refinement_after(project, store, prov)
    return answer(project, done, said)


def wake_reason(ev: dict, project: Project | None = None) -> str | None:
    """What the Supervisor is told when the owner clicks a request on a page; None for any other
    event, and for a request whose source is not the owner (nothing else may ask). ``ev`` is flat
    (its data's fields beside the envelope's, ``events.flat``): the data's ``id`` gives way to the
    event's, so the element is read from the subject (``machine:<id>``)."""
    if ev.get("type") != REQUESTED or str(ev.get("source") or "").split("/")[0] != OWNER:
        return None
    control, change = ev.get("control"), ev.get("change")
    part, _, eid = str(ev.get("subject") or "").partition(":")
    element, rid = f"{part}:{eid}", ev.get("id")
    on = f" (change {change})" if change else ""
    if control == "switch_kind":
        kind = None
        if project is not None:
            try:
                kind = (intended_api.current(project).get("machines") or {}).get(eid, {}).get("kind")
            except Exception:  # noqa: BLE001 - the layer cannot be read now: the Supervisor reads it
                kind = None
        to = {"universal": "programmatic", "programmatic": "universal"}.get(kind)
        call = (f"refine_machine with name {eid}, set_kind {to}" if to else
                f"refine_machine with name {eid} and set_kind the other kind (read_design gives its kind)")
        return (f"event {REQUESTED} request={rid}: the owner clicked the Universal | Programmatic switch on {element} on the "
                f"{ev.get('from') or 'page'}. Do it now: {call}, request {rid}, and a reason; then tell the owner in a line.")
    return (f"event {REQUESTED} request={rid}: the owner clicked {control} on {element}{on} on the {ev.get('from') or 'page'}. "
            f"Do it now: {control} with element {element}" + (f", change {change}" if change else "")
            + f", request {rid}, and a reason; then tell the owner in a line.")


def announce_start(store: AgentStore, name: str) -> str | None:
    """At a confirmed machine's first turn, publish the ``system.changed`` its prediction is about
    (as the machine, with the id chosen at confirm), and at a later turn the fresh change a
    prediction published after that is about (``publish_waiting``); the change id, or None."""
    rec = (store.agent_state(MACHINE).get("machine_starts") or {}).get(name)
    if not rec:
        return None
    if rec.get("announced"):
        if not (nxt := rec.get("next")):
            return None
        store.events.publish("system.changed", {"machine": name, "note": f"{name}: its waiting expected edits are judged from now",
                                                "prediction_change": nxt}, source=name, id=nxt, subject=f"machine:{name}")
        _starts(store, lambda d: d[name].pop("next", None))
        return nxt
    store.events.publish("system.changed", {"machine": name, "note": f"{name} starts: its first turn",
                                            "prediction_change": rec["change"]},
                         source=name, id=rec["change"], subject=f"machine:{name}")
    _starts(store, lambda d: d[name].update(announced=time.time()))
    return rec["change"]


COMMANDS = {"confirm": confirm, "discard": discard}
