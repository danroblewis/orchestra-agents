"""Confirm and discard (machine-design.md §2.4, §2.5), and the owner's page requests.

The owner confirms; the Supervisor applies. A click on the Machines page publishes
``orchestra.intent.requested`` with source ``owner`` and changes nothing; it wakes the Supervisor
(``wake_reason``; the host wakes it only for one whose source is ``owner``), which calls
``confirm``, ``discard`` or ``switch_kind`` with the request's id, as it does for "confirm it" in
the chat. Every confirm and discard, of any part and of a pending change, is backed by the owner:
their quote, or their noted request for exactly that (``design.check_request``).

What confirming a machine does here depends first on who runs it. **Managed elsewhere**
(``managed: external``), of either kind: nothing runs here, nothing is written to the state
directory and nothing is published (no ``system.changed``: nothing changed, and a change would
interrupt every open prediction); the declaration waits to be heard or measured, and its expected
effect is kept on it as an expectation. **A Programmatic one managed here**: its prediction is
published about a change its program announces when it runs (``announce_change`` with the id the
answer names). **A Universal one managed here**, in this order: the op on the declared layer (with the
id of the ``system.changed`` that will announce its start, chosen first); its implementation
written to the state directory's ``machines/<name>/`` from the declaration, with its role and its
agent records (``state_machines``: the host runs it from there; nothing is committed; a daily
machine's last run is set to now unless a missed run is ``run once``, so it first runs at its next
time); then its expected effect published as the Supervisor's prediction of that change
(``orchestra.prediction.made``, subject ``machine:<name>``, its ends the current model's ids),
recorded on the declaration in a second call and in the Supervisor's own predictions, so the
verdict wakes it (``verdicts.py``). The agent's first turn publishes that ``system.changed``
(``announce_start``, from the host), which begins the era the prediction is judged in. An edit
that waits for a series is published once it has one (``publish_waiting``), and a prediction an
era interrupts is renewed for what is left of its horizon (``renew``). Until confirm nothing runs
and nothing is published. A confirmed pending change is made so here too: one that changes
``kind`` or ``managed`` stops or starts the agent (``design_machines.apply_kind``), one that changes
the engagement counts the schedule from now, any other rewrites the records.

**Discard** of a proposal leaves nothing but the history. Discard of a declared element retracts
it (the owner's request or words back it); a declared machine is then retired: its records in the
state directory are removed, and the host stops its session."""

from __future__ import annotations

import shutil
import time
import uuid
from datetime import datetime

from . import intended_api, state_machines
from .clock import next_at, zone
from .config import Project
from .design import MACHINE, OWNER, REQUESTED, answer, apply, check_request, page_line, plural
from .design_dynamics import resolve
from .design_machines import (
    apply_kind,
    cannot_judge,
    current,
    draft,
    drafts_dir,
    min_horizon,
    publishable,
    reads_of,
    stock_as_flow,
    waiting_line,
)
from .store import AgentStore, StoreError

PARTS = ("stock", "effect", "flow", "machine", "attachment")


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


def starts(store: AgentStore) -> dict:
    return store.agent_state(MACHINE).get("machine_starts") or {}


def _starts(store: AgentStore, fn) -> dict:
    def change_it(d):
        d = dict(d or {})
        fn(d)
        return d
    return store.update_agent_state(MACHINE, "machine_starts", change_it)


def _predict(project: Project, store: AgentStore, mid: str, edits: list[dict], horizon_s: float, change: str) -> dict:
    from orchestra.sd.predictions import PredictionError, Predictions
    try:
        # Orchestra's own Events, never the store's: this package's reads its events flat (events.flat), and
        # Orchestra's record, which reads the bus through the Events it is given, would find no data and
        # keep the prediction as invalid (seen in the live test, A3)
        return Predictions(project.orchestra, MACHINE).record(edits, horizon_s, change=change,
                                                                                   subject=f"machine:{mid}")
    except PredictionError as e:
        raise StoreError(f"Orchestra refused the expected effect as a prediction: {e}") from None


def _ready(project: Project, lay: dict, mid: str, m: dict, done: list | tuple = ()) -> tuple[list[tuple[dict, dict]],
                                                                                              list[tuple[dict, str]]]:
    """The expected edits publishable now, each (as stored, as the judge reads it), and those that
    wait, each with why; leaving out the ones already published (``done``, as stored)."""
    core, keys = intended_api.unpublishable(project), intended_api.Keys(intended_api.current_model(project))
    ready, waiting = [], []
    for e in (m.get("expected_effect") or {}).get("edits") or []:
        if e in done:
            continue
        sent, why = publishable(project, lay, mid, e, keys=keys, core=core)
        why = why or cannot_judge(e)
        if why:
            waiting.append((e, why))
        else:
            ready.append((e, sent))
    return ready, waiting


def _first_run(m: dict) -> str:
    """When a confirmed machine first runs, in words."""
    e = m.get("engagement") or {}
    if e.get("mode") != "scheduled":
        return f"woken by {', '.join(m.get('receives') or []) or 'nothing yet'}"
    at, tz, now = e.get("at") or "00:00", e.get("tz"), time.time()
    nxt = next_at(at, None if e.get("missed") == "run once" else now, now, tz)
    day = "today" if datetime.fromtimestamp(nxt, zone(tz)).date() == datetime.fromtimestamp(now, zone(tz)).date() else "tomorrow"
    return f"first run at {at} {tz or '(the host clock)'}, {day}"


def confirm(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    part, eid = _element(project, a)
    pending = (a.get("change") or "").strip() or None
    check_request(project, store, prov, "confirm", part, eid, pending)
    if part != "machine" or pending:
        before = current(project, eid) if part == "machine" else None
        done = apply(project, store, [{"op": "confirm", "part": part, "id": eid, **({"change": pending} if pending else {})}], prov)
        lines = [f"{part} {eid}: {'change ' + pending if pending else 'it'} confirmed"]
        extra: dict = {}
        if before is not None:
            lines += _made_so(project, store, before, current(project, eid), prov, extra)
        lines += _refinement_after(project, store, prov)
        return answer(project, done, lines, **extra)
    m = current(project, eid)
    if m.get("state") == "declared":
        raise StoreError(f"machine {eid} is confirmed already")
    stock_as_flow(project, (m.get("expected_effect") or {}).get("edits") or [])   # before anything is confirmed
    here = m.get("managed") != "external"
    runs = here and m.get("kind") == "universal"
    if runs:
        state_machines.files(m)                          # its records check before anything is written
    change = uuid.uuid4().hex                            # the id of the system.changed its first turn will publish
    done = apply(project, store, [{"op": "confirm", "part": "machine", "id": eid}], prov)
    m = current(project, eid)
    lines = [f"machine {eid} confirmed", page_line(project, "the Machines page draws it solid, \"declared · not heard yet\"")]
    extra: dict = {"change": change}
    if runs:
        dst = state_machines.write(project, m, reads_of(project, store, m))
        shutil.rmtree(drafts_dir(project) / eid, ignore_errors=True)
        if (m.get("engagement") or {}).get("missed") != "run once":
            store.set_agent_state(eid, last_at_fire=time.time())
        lines.append(f"its spec, role and agent records are at {dst}, on {state_machines.runs_on(project)}; "
                     f"{_first_run(m)}. Nothing was committed, and the host runs it from there")
        extra["spec"] = str(dst)
    elif here:
        lines.append("next: the program is a worker's to write (hand_to_coordinator) unless it exists; when it does, "
                     "set_machine adds its [[machines]] entry, in the overlay")
    else:
        lines.append("nothing runs here: the declaration waits to be heard on the bus or measured")
    if here:
        _starts(store, lambda d: d.__setitem__(eid, {"change": change, "prediction": None, "published": [], "at": time.time(),
                                                     "announced": None, "runs": runs, "waiting": 0}))
    if here:
        lines += _publish_at_confirm(project, store, eid, m, change, extra)
    else:                                                # nothing changed here: no era begins, nothing is published
        extra.update(change=None, prediction=None)
        if (m.get("expected_effect") or {}).get("edits"):
            lines.append("its expected effect is kept as an expectation: published when you say it is new, or when it is "
                         "first heard")
    if here and not runs and extra.get("prediction"):
        lines.append(f"its prediction is judged once its program runs: announce_change with change {change} then")
    lines += _refinement_after(project, store, prov)
    return answer(project, done, lines, machine=eid, **extra)


def _made_so(project: Project, store: AgentStore, before: dict, m: dict, prov: dict, extra: dict) -> list[str]:
    """A confirmed pending change on a machine, made so here: a change of ``kind`` or ``managed``
    stops or starts its agent (``apply_kind``); any other rewrites its records; a change of its
    engagement counts its schedule from now (unless a missed run is ``run once``)."""
    if m.get("state") != "declared":
        draft(project, m, store)
        return []
    eid = m["id"]
    if (before.get("kind"), before.get("managed")) != (m.get("kind"), m.get("managed")):
        said, more = apply_kind(project, store, before, m, prov)
        extra.update(more)
        return said
    lines = []
    if draft(project, m, store) is not None:
        _restart(project, store, eid)
        lines.append(f"its spec, role and agent records in {machines_dir(project) / eid} are written again from the "
                     "declaration (no update_role or update_agent is needed); a running session starts over with them")
        if before.get("engagement") != m.get("engagement"):
            if (m.get("engagement") or {}).get("missed") != "run once":
                store.set_agent_state(eid, last_at_fire=time.time())
            lines.append(f"its engagement is now {(m.get('engagement') or {}).get('mode')}: {_first_run(m)}")
    return lines


def _publish_at_confirm(project: Project, store: AgentStore, mid: str, m: dict, change: str, extra: dict) -> list[str | None]:
    lay = intended_api.current(project)
    ready, waiting = _ready(project, lay, mid, m)
    _starts(store, lambda d: d[mid].update(waiting=len(waiting)))
    try:
        made = _publish(project, store, mid, ready, m, change) if ready else None
    except StoreError as e:                              # the confirm stands; the prediction is said to be missing
        return [f"its expected effect was not published: {e}; it is confirmed, with no prediction yet"]
    extra["prediction"] = made
    if not made:
        return ["no prediction published yet: " + ("; ".join(waiting_line(project, store, lay, e, w) for e, w in waiting)
                                                   if waiting else "it has no expected effect")]
    out = [(f"prediction {made} published as the Supervisor, about change {change}: judged "
            f"{float(m['expected_effect']['horizon_s']) / 86_400:.3g} days after its first turn")]
    out += [waiting_line(project, store, lay, e, w) for e, w in waiting]
    return out


def _publish(project: Project, store: AgentStore, mid: str, pairs: list[tuple[dict, dict]], m: dict, change: str,
             horizon_s: float | None = None) -> str | None:
    """Publish the edits (``pairs``: as stored, as sent) as the Supervisor's prediction of
    ``change``, and record it on the declaration and in the Supervisor's predictions."""
    horizon = float(horizon_s or (m.get("expected_effect") or {}).get("horizon_s") or 0)
    made = _predict(project, store, mid, [s for _, s in pairs], horizon, change)
    pid = made.get("id")
    apply(project, store, [{"op": "record_prediction", "machine": mid, "prediction": pid}],
          {"author": MACHINE, "source": MACHINE, "reason": f"the prediction published for {mid}'s change {change}"})
    by_host = (starts(store).get(mid) or {}).get("runs", True)
    store.add_prediction(MACHINE, {"id": pid, "change": change, "experiment": None, "ticket": None, "source": MACHINE,
                                   "subject": f"machine:{mid}", "horizon_s": horizon, "at": time.time(),
                                   "announced": f"by {mid}'s first turn (the host)" if by_host else None,
                                   "note": f"{mid}'s expected effect"})
    entry = {"prediction": pid, "change": change, "edits": [e for e, _ in pairs], "sent": [s for _, s in pairs],
             "at": time.time(), "horizon_s": horizon}
    _starts(store, lambda d: d.setdefault(mid, {}).update(prediction=pid, published=[*d[mid].get("published", []), entry]))
    return pid


def publish_waiting(project: Project, store: AgentStore) -> list[str]:
    """Publish the expected edits of confirmed machines that waited and can be published now
    (after a ``link``, or on the host's tick); what was said. Nothing is read while no confirmed
    machine has an edit waiting."""
    recs = {mid: rec for mid, rec in starts(store).items() if rec.get("waiting")}
    if not recs:
        return []
    lay = intended_api.current(project)
    said = []
    for mid, rec in recs.items():
        m = (lay.get("machines") or {}).get(mid)
        if m is None or m.get("state") != "declared":
            continue
        ready, waiting = _ready(project, lay, mid, m, done=[x for p in rec.get("published") or [] for x in p.get("edits") or []])
        left = len(waiting)
        _starts(store, lambda d, k=mid, n=left: d[k].update(waiting=n))
        if not ready:
            continue
        change, fresh = rec["change"], bool(rec.get("announced"))
        if fresh:                       # its start was announced already: a change of its own, which its next turn announces
            change = uuid.uuid4().hex
            _starts(store, lambda d, c=change, k=mid: d[k].update(next=c))
        pid = _publish(project, store, mid, ready, m, change)
        said.append(f"prediction {pid} published for {mid}'s waiting edit{'' if len(ready) == 1 else 's'}, about change {change}"
                    + ((" (a fresh change, announced at its next turn: its start was announced before the series was linked)"
                        if rec.get("runs", True) else "") if fresh else "")
                    + ("" if rec.get("runs", True) else f": announce_change with change {change} when its program runs"))
    return said


def renew(project: Project, store: AgentStore, ev: dict) -> str | None:
    """A designed machine's prediction judged ``interrupted``: the same edits recorded again as the
    Supervisor's prediction of the change that began the new era, for what is left of its horizon
    (at least what one needs); what to tell the Supervisor."""
    if ev.get("type") != "orchestra.prediction.judged" or ev.get("verdict") != "interrupted":
        return None
    for mid, rec in starts(store).items():
        hit = next((p for p in rec.get("published") or [] if p.get("prediction") == ev.get("causationid")), None) \
            if ev.get("causationid") else None
        hit = hit or next((p for p in rec.get("published") or [] if p.get("change") == ev.get("correlationid")
                           and not p.get("renewed")), None)
        if hit is None:
            continue
        new = intended_api.era_cause(project, ev.get("interrupted_by"))
        if new is None:
            return (f"event orchestra.prediction.judged: {mid}'s prediction {hit['prediction']} was interrupted (era "
                    f"{ev.get('interrupted_by')}), and the change that began that era cannot be read, so it was not renewed: "
                    "say so to the owner")
        m = current(project, mid)
        # each entry's horizon counts from its own start: the machine's announced start for the first change's entry, the
        # entry's own time for a renewed one (its horizon is already what was left at its renewal)
        announced = rec.get("announced") if isinstance(rec.get("announced"), int | float) else None
        start = announced if hit.get("change") == rec.get("change") and announced else hit.get("at") or time.time()
        left = start + float(hit.get("horizon_s") or 0) - time.time()
        floor, _ = min_horizon(project, store, m)
        horizon = max(left, floor)
        lay = intended_api.current(project)
        ready, waiting = _ready(project, lay, mid, {"expected_effect": {"edits": hit["edits"]}})
        if not ready:
            return (f"event orchestra.prediction.judged: {mid}'s prediction {hit['prediction']} was interrupted by change {new}; "
                    "none of its edits can be published in the new era (" + "; ".join(w for _, w in waiting) + "): say so")
        pid = _publish(project, store, mid, ready, m, new, horizon_s=horizon)
        _starts(store, lambda d, k=mid, old=hit["prediction"], new_id=pid: [p.update(renewed=new_id) for p in d[k].get("published")
                                                                            or [] if p.get("prediction") == old])
        store.update_prediction(None, prediction=hit["prediction"], judged="interrupted", judged_at=time.time(), renewed_as=pid)
        return (f"event orchestra.prediction.judged: {mid}'s prediction {hit['prediction']} was interrupted by change {new}; "
                f"its edits were recorded again as prediction {pid}, about that change, for what is left of its horizon: "
                f"{horizon / 86_400:.3g} days" + (" (raised to what one needs)" if floor > left else "")
                + ". Tell the owner in a line if they ask.")
    return None


def _restart(project: Project, store: AgentStore, name: str) -> None:
    from .org_store import all_agents
    ag = all_agents(project).get(name)
    if ag is not None and ag.resident:
        store.set_agent_state(name, restart_requested=True)


def _refinement_after(project: Project, store: AgentStore, prov: dict) -> list[str]:
    """After the owner's confirm or discard, the refinement pass over the map as it is now (§3)."""
    from .design_passes import run
    if prov.get("author") != OWNER and not prov.get("request"):
        return []
    try:
        got = run(project, store, store.events.read(max(0, store.events.end() - 5000), limit=5000)[0], force=True)
    except Exception as e:  # noqa: BLE001 - the confirm stands without its pass
        return [f"the refinement pass did not run: {e}"]
    if not got or not got["findings"]:
        return ["the refinement pass over the map found nothing new"]
    return [f"the refinement pass found {plural(len(got['findings']), 'thing')} to put to the owner (read_design, "
            "refinement): " + "; ".join(f["text"] for f in got["findings"][:3])]


def discard(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    from .design_instruments import remove_for
    part, eid = _element(project, a)
    pending = (a.get("change") or "").strip() or None
    check_request(project, store, prov, "discard", part, eid, pending)
    el = (intended_api.current(project).get(f"{part}s") or {}).get(eid) or {}
    retract = not pending and el.get("state") not in (None, "proposed")
    op = {"op": "retract", "part": part, "id": eid} if retract else \
        {"op": "discard", "part": part, "id": eid, **({"change": pending} if pending else {})}
    done = apply(project, store, [op], prov)
    said = [f"change {pending} on {part} {eid} discarded" if pending else
            f"{part} {eid} retracted: it was declared, and is kept only in the history" if retract else
            f"{part} {eid} discarded: kept only in the history"]
    if part == "machine" and not pending:
        shutil.rmtree(drafts_dir(project) / eid, ignore_errors=True)
        if state_machines.remove(project, eid):
            store.drain_wakeups(eid)
            said.append(f"retired: its records in {machines_dir(project) / eid} are removed, and the host stops its session at "
                        "its next look (a turn already running finishes)")
    if part in ("stock", "effect") and not pending:
        gone, kept = remove_for(project, store, f"{part}:{eid}", f"{part} {eid} was discarded", prov)
        if gone:
            said.append(f"its draft instruments removed, entries and scripts: {', '.join(gone)}")
        if kept:
            said.append(f"kept, as the owner chose them: {', '.join(kept)} (they still record; remove one only if the owner asks)")
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
        return (f"event {REQUESTED} request={rid}: the owner clicked the Universal | Programmatic switch on {element} on the "
                f"{ev.get('from') or 'page'}. Do it now: switch_kind with name {eid}, request {rid}, and a reason; then tell "
                "the owner in a line.")
    return (f"event {REQUESTED} request={rid}: the owner clicked {control} on {element}{on} on the {ev.get('from') or 'page'}. "
            f"Do it now: {control} with element {element}" + (f", change {change}" if change else "")
            + f", request {rid}, and a reason; then tell the owner in a line.")


def announce_start(store: AgentStore, name: str, source: str | None = None) -> str | None:
    """At a confirmed machine's first turn, publish the ``system.changed`` its prediction is about
    (as the machine, with the id chosen at confirm; ``source``: as the Supervisor, for one that runs
    elsewhere, at its confirm), and at a later turn the fresh change a prediction published after
    that is about (``publish_waiting``); the change id, or None."""
    rec = starts(store).get(name)
    if not rec:
        return None
    if rec.get("announced"):
        if not (nxt := rec.get("next")):
            return None
        store.events.publish("system.changed", {"machine": name, "note": f"{name}: its waiting expected edits are judged from now",
                                                "prediction_change": nxt}, source=source or name, id=nxt, subject=f"machine:{name}")
        _starts(store, lambda d: d[name].pop("next", None))
        return nxt
    store.events.publish("system.changed", {"machine": name, "note": f"{name} starts: its first turn",
                                            "prediction_change": rec["change"]},
                         source=source or name, id=rec["change"], subject=f"machine:{name}")
    _starts(store, lambda d: d[name].update(announced=time.time()))
    return rec["change"]


COMMANDS = {"confirm": confirm, "discard": discard}
