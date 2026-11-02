"""The modeling tools: an agent reads Orchestra's system dynamics model, and states the expected
effect of a change it is about to make as a prediction (Orchestra's reads,
``orchestra.sd.reads.ModelReads``, and predictions, ``orchestra.sd.predictions.Predictions``,
called in this process: the agents' tool server runs with Orchestra importable, so it holds the
objects rather than going over HTTP).

Every answer leads with ``summary``, short lines with the key numbers first (``model_text`` and
``run_text``); Orchestra's own JSON comes only with ``raw: true``.

Who may do what (the groups are checked before any of this runs, ``access.authorize``):

- ``model``: read the model, series, eras, observability, predictions and verdicts, check an
  equation, and the modeling guide;
- ``predict``: state a fix's expected effect as a prediction (``predict_fix``), and announce the
  change. Only the agent that made a prediction announces its change (``announce_change`` here,
  or the ``change`` of a Supervisor's setup tool, ``claim_change``).

The Supervisor's design tools (``declare_expected_effect``, ``confirm``: ``design_confirm.py``)
and ``predict_fix`` make the predictions.

Nothing here changes a machine, the model or a metric: a prediction is judged later; the one
thing published is the change's announcement (``system.changed``), as the agent that makes the
change."""

from __future__ import annotations

import time
from typing import Any

from orchestra.config import ConfigError, parse_duration

from . import model_text as T
from . import run_text as R
from .config import Project
from .protocol import ended
from .store import AgentStore, StoreError

READS = frozenset({"modeling_guide", "read_model", "read_series", "read_eras", "read_observability", "check_equation",
                   "read_predictions"})
COMMANDS = READS | frozenset({"announce_change", "predict_fix"})
CHANGED = "system.changed"
SERIES_POINTS = 20_000         # Orchestra's cap on the points of one series answer
RECENT = 6                     # the last readings a series summary shows
PREDICTIONS_SHOWN = 10
NO_SD = ("this read needs Orchestra's simulation extra beside the agents (numpy and scipy: "
         "pip install 'orchestra[sd]')")


def reads(project: Project):
    """Orchestra's model reads. Tests replace this to read a history they made instead of the
    metrics store."""
    from orchestra.sd.reads import ModelReads
    return ModelReads(project.orchestra)


def seconds(v, what: str) -> float:
    """A duration: seconds, or text such as 90s, 30m, 2h, 1d."""
    if isinstance(v, str) and v.strip().endswith("d") and v.strip()[:-1].replace(".", "", 1).isdigit():
        return float(v.strip()[:-1]) * 86400.0
    try:
        return parse_duration(v, 0.0)
    except ConfigError:
        raise StoreError(f"{what}: seconds, or a duration such as 90s, 30m, 2h or 1d; not {v!r}") from None


def window(a: dict) -> dict:
    out: dict[str, Any] = {}
    if a.get("era"):
        out["era"] = a["era"]
    for k in ("start", "end"):
        if a.get(k) is not None:
            out[k] = float(a[k])
    if a.get("span") is not None:
        out["span_s"] = seconds(a["span"], "span")
    return out


def answer(lines: list[str], raw: dict | None = None, **extra) -> dict:
    return {"ok": True, "summary": lines, **extra, **(raw or {})}


def handle_model(project: Project, store: AgentStore, command: str, a: dict, role: str, ticket: str) -> dict:
    from orchestra.sd.reads import ReadError

    source = store.events.source or role
    try:
        return _handle(project, store, command, a, role, ticket, source)
    except ReadError as e:
        raise StoreError(str(e)) from None
    except ImportError as e:
        raise StoreError(f"{NO_SD}: {e}") from None


def _handle(project: Project, store: AgentStore, command: str, a: dict, role: str, ticket: str, source: str) -> dict:
    if command == "modeling_guide":
        from . import prompting
        return {"ok": True, "guide": prompting.modeling_guide()}
    if command == "announce_change":
        return announce_change(store, role, ticket, a)
    x = reads(project)
    raw = bool(a.get("raw"))
    if command == "read_model":
        from orchestra.sd.reads import ReadError
        try:
            m = x.model(a.get("version"), raw=raw)["model"]
        except ReadError as e:
            if (said := no_model(e)) is None:
                raise
            return answer(said, model=None, **({"reason": e.reason} if isinstance(getattr(e, "reason", None), dict) else {}))
        return answer(T.model_lines(m), {"model": m} if raw else None)
    if command == "read_series":
        return read_series(x, a)
    if command == "read_eras":
        e = x.eras()
        return answer(T.eras_lines(e), {"eras": e} if raw else None)
    if command == "read_observability":
        o = x.observability(a.get("version"))
        return answer(T.observability_lines(o), {"observability": o} if raw else None)
    if command == "check_equation":
        got = x.check_equation(a.get("equation"), a.get("params"), version=a.get("version"))
        if not got.get("ok"):
            return {"ok": False, "summary": [f"refused: {got.get('error')}"], "error": got.get("error")}
        r = got["reads"]
        names = lambda k: ", ".join(r[k]) or "none"
        return answer([(f"parses: {got['equation']}{T.params(got.get('params'))}; reads stocks {names('stocks')}, inputs "
                       f"{names('inputs')}, parameters {names('params')}, functions {names('functions')}")], reads=r)
    if command == "read_predictions":
        return read_predictions(x, a, source)
    raise StoreError(f"unknown command {command!r}")


NO_MODEL = ("no model yet; the Dynamics page lists the series looked at and why each was left out; metrics are being "
            "collected, and discovery saves a model once a counter has enough events")
WAITS = ("an expectation recorded meanwhile (declare_expected_effect) is kept as a draft: a factor waits for discovery to find "
         "its flow, a level for its stock, and is judged only then")


def no_model(e: Exception) -> list[str] | None:
    """What read_model says when there is no model: Orchestra's own paragraph (``NoModel.text``:
    the series looked at, why each was left out, events per counter against the minimum) when the
    installed core gives one, else that the Dynamics page lists them (never "no metrics"); then
    what a recorded expectation waits for. None for any other read error."""
    text = (getattr(e, "text", None) or "").strip()
    if not text and "no model" not in str(e):
        return None
    return [text or NO_MODEL, WAITS]


def read_series(x, a: dict) -> dict:
    ids = a.get("ids")
    ids = [i for i in (ids.split(",") if isinstance(ids, str) else ids or []) if i]
    kw = {k: v for k, v in window(a).items() if k in ("start", "end", "span_s")}
    if a.get("points"):
        got = x.series(ids, points=True, limit=int(a.get("limit") or 500), after=a.get("after"), version=a.get("version"), **kw)
        for row in got["series"].values():
            if "next" not in row and row.get("points"):
                row["recent"] = [v for _, v in row["points"][-RECENT:]]
        pages = {k: {"points": v.get("points"), **({"next": v["next"]} if "next" in v else {})} for k, v in got["series"].items()}
        return answer(T.series_lines(got) + ["points: [time (unix seconds), value]; a series with next continues with after=next"],
                      points=pages)
    got = x.series(ids, points=True, limit=max(1, min(5000, SERIES_POINTS // max(1, len(ids)))), version=a.get("version"), **kw)
    for row in got["series"].values():
        pts = row.pop("points", None) or []
        if "next" not in row and pts:
            row["recent"] = [v for _, v in pts[-RECENT:]]
        row.pop("next", None)
    return answer(T.series_lines(got))


def read_predictions(x, a: dict, source: str) -> dict:
    if a.get("id"):
        preds = [x.predictions(a["id"])["prediction"]]
    else:
        preds = [p for p in x.predictions()["predictions"]
                 if (not a.get("change") or p.get("change") == a["change"]) and (not a.get("mine") or p.get("source") == source)]
    shown = preds[-PREDICTIONS_SHOWN:]
    lines = ([f"{len(preds)} prediction(s); the last {len(shown)}:"] if len(preds) > len(shown) else []) or \
        ([] if preds else ["no predictions" + (" of yours" if a.get("mine") else "")])
    for p in shown:
        lines += R.prediction_lines(p)
    raw = {"predictions": [p if a.get("raw") else {k: v for k, v in p.items() if k != "trajectories"} for p in shown]}
    return answer(lines, raw if a.get("raw") else None)


def own_prediction(store: AgentStore, role: str, change: str) -> dict:
    found = store.find_prediction(change)
    if found is None or found[0] != role:
        raise StoreError(f"refused: {change!r} is not the change of a prediction you made; predict_fix (or the Supervisor's confirm) gives the id")
    rec = found[1]
    if rec.get("announced"):
        raise StoreError(f"change {change} was already announced ({rec['announced']})")
    return rec


def claim_change(store: AgentStore, role: str, change: str | None) -> dict | None:
    """The prediction whose change a setup tool's announcement carries, checked to be the
    caller's and not announced yet, and marked announced. None when no change is given."""
    if not change:
        return None
    rec = own_prediction(store, role, change)
    store.update_prediction(change, announced="with config.changed")
    return rec


def announce_change(store: AgentStore, role: str, ticket: str, a: dict) -> dict:
    change = a.get("change") or ""
    rec = own_prediction(store, role, change)
    when = a.get("when") or "now"
    if when == "landed":
        if not rec.get("ticket"):
            raise StoreError("when landed: only a prediction made on a ticket waits for the ticket's end")
        store.update_prediction(change, announce_on="landed", note=a.get("note") or "")
        return answer([(f"change {change} will be announced when ticket {rec['ticket']} ends done (the gate lands it, or in tasks "
                       "mode its review approves it); the prediction is judged from then")])
    if when != "now":
        raise StoreError("when: now (the change is in effect) or landed (it takes effect when your ticket lands)")
    ev = publish(store, rec, a.get("note") or "")
    return answer([(f"announced: {CHANGED} with id {change}; Orchestra begins a new era from it and judges prediction "
                   f"{rec.get('id')} {T.dur(rec.get('horizon_s'))} later (orchestra.prediction.judged wakes you)")], event=ev.get("id"))


def publish(store: AgentStore, rec: dict, note: str, source: str | None = None) -> dict:
    ev = store.events.publish(CHANGED, {"prediction": rec.get("id"), "ticket": rec.get("ticket") or None,
                                        "note": note}, source=source, id=rec["change"],
                              **({"subject": rec["ticket"]} if rec.get("ticket") else {}))
    store.update_prediction(rec["change"], announced=f"{CHANGED} at {T.when(time.time())}")
    return ev


def announce_landed(store: AgentStore, ev: dict, work: str) -> list[str]:
    """The changes waiting for a ticket that ``ev`` ends: announced, as the instance that
    committed them, when it ended done; given up when it was cancelled. Returns what was done."""
    end = ended(ev, work)
    if end is None:
        return []
    tid, state = end
    done = []
    for st in store.runtime()["agent_state"].values():
        for rec in st.get("predictions") or []:
            if rec.get("ticket") != tid or rec.get("announce_on") != "landed" or rec.get("announced"):
                continue
            if state == "DONE":
                publish(store, rec, rec.get("note") or "", source=rec.get("source"))
                done.append(f"announced change {rec['change']} of {tid}")
            else:
                store.update_prediction(rec["change"], announced=f"never: ticket {tid} was {state}")
    return done


def predict_fix(project: Project, store: AgentStore, a: dict, role: str, ticket: str) -> dict:
    """A fix's expected effect, stated before it is made (the Supervisor's
    flow-keeping: every fix carries a prediction): Orchestra's ``Predictions.record`` publishes
    ``orchestra.prediction.made`` as the caller, and the record is the caller's, so its setup
    tools' ``change`` and ``announce_change`` take its id."""
    from orchestra.sd.predictions import PredictionError, Predictions

    expect = a.get("expect")
    if not isinstance(expect, list):
        raise StoreError("expect is a list: [{flow, factor}], [{stock, below}], ... or [] for a fix that should change nothing")
    horizon = seconds(a.get("horizon"), "horizon")
    note = (a.get("note") or "").strip()
    if len(note) < 10:
        raise StoreError("say what the fix is, in a sentence")
    source = store.events.source or role
    try:
        made = Predictions(project.orchestra, source).record(expect, horizon, subject=a.get("subject") or None)
    except PredictionError as e:
        raise StoreError(f"Orchestra refused the prediction: {e}") from None
    change = made.get("change") or (made.get("data") or {}).get("change")
    store.add_prediction(role, {"id": made.get("id"), "change": change, "ticket": ticket, "source": source,
                                "horizon_s": horizon, "at": time.time(), "announced": None, "note": note})
    stated = made.get("expect") or (made.get("data") or {}).get("expect") or expect       # Orchestra's checked shape, with each kind
    effect = "the fix should change nothing measured (the null test)" if not expect else "; ".join(R.expectation(e) for e in stated)
    lines = [f"prediction {made.get('id')} recorded for change {change}: {effect}",
             (f"next: make the fix with change={change} (a setup tool), or announce it with announce_change once it is made; "
              f"Orchestra judges it {T.dur(horizon)} after the announcement")]
    return answer(lines, prediction=made.get("id"), change=change)
