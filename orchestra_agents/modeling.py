"""The modeling tools: an agent reads Orchestra's system dynamics model, experiments on a copy of
it, and commits an experiment as the prediction of a change it is about to make (Orchestra's
experiments, ``orchestra.sd.experiments.api.Experiments``, called in this process: the agents'
tool server runs with Orchestra importable, so it holds the object rather than going over HTTP).

Every answer leads with ``summary``, short lines with the key numbers first (``model_text`` and
``run_text``); Orchestra's own JSON, trajectories included, comes only with ``raw: true``.

Who may do what (the groups are checked before any of this runs, ``access.authorize``):

- ``model``: read the model, series, eras, observability, predictions and verdicts, experiments
  and their runs, check an equation, and the modeling guide;
- ``experiments``: make, edit, run forward and inverse, and discard one's own experiments
  (an experiment is its owner's, the agent instance that made it: Orchestra refuses another's);
- ``replay``: replay any experiment over a past window (a reviewer checks a worker's equations);
- ``predict``: commit one's own experiment as the prediction of a change, and announce the change.
  A session on a ticket commits only an experiment it made on that ticket (``add_experiment``),
  and only the agent that committed a prediction announces its change (``announce_change`` here,
  or the ``change`` of a Supervisor's setup tool, ``claim_change``).

Nothing here changes a machine, the model or a metric: an experiment is a statement about the
model and a prediction is judged later; the one thing published is the change's announcement
(``system.changed``), as the agent that makes the change."""

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
                   "read_predictions", "list_experiments", "read_experiment", "diff_experiment", "read_run"})
WRITES = frozenset({"create_experiment", "edit_experiment", "load_model", "discard_experiment", "run_forward", "run_inverse"})
COMMANDS = READS | WRITES | frozenset({"run_replay", "commit_prediction", "announce_change"})
CHANGED = "system.changed"
SERIES_POINTS = 20_000         # Orchestra's cap on the points of one series answer
RECENT = 6                     # the last readings a series summary shows
PREDICTIONS_SHOWN = 10
NO_SD = ("running the model needs Orchestra's simulation extra beside the agents (numpy and scipy: "
         "pip install 'orchestra[sd]'); reading the model does not")


def experiments(project: Project, source: str):
    """Orchestra's experiments as ``source`` (the agent instance). Tests replace this to read
    a history they made instead of the metrics store."""
    from orchestra.sd.experiments.api import Experiments
    return Experiments(project.orchestra, source)


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
    from orchestra.sd.experiments.api import ExperimentError

    source = store.events.source or role
    try:
        return _handle(project, store, command, a, role, ticket, source)
    except ExperimentError as e:
        where = f" (edit {e.extra['index']}; the draft is unchanged)" if e.extra.get("index") is not None else ""
        raise StoreError(f"{e}{where}") from None
    except ImportError as e:
        raise StoreError(f"{NO_SD}: {e}") from None


def _handle(project: Project, store: AgentStore, command: str, a: dict, role: str, ticket: str, source: str) -> dict:
    if command == "modeling_guide":
        from . import prompting
        return {"ok": True, "guide": prompting.modeling_guide()}
    if command == "announce_change":
        return announce_change(store, role, ticket, a)
    x = experiments(project, source)
    raw = bool(a.get("raw"))
    if command == "read_model":
        m = x.model(a.get("version"), raw=raw)["model"]
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
        got = x.check_equation(a.get("equation"), a.get("params"), experiment=a.get("experiment"))
        if not got.get("ok"):
            return {"ok": False, "summary": [f"refused: {got.get('error')}"], "error": got.get("error")}
        r = got["reads"]
        names = lambda k: ", ".join(r[k]) or "none"
        return answer([(f"parses: {got['equation']}{T.params(got.get('params'))}; reads stocks {names('stocks')}, inputs "
                       f"{names('inputs')}, parameters {names('params')}, functions {names('functions')}")], reads=r)
    if command == "read_predictions":
        return read_predictions(x, a, source)
    if command == "list_experiments":
        rows = x.list(owner=source if a.get("mine") else a.get("owner"), status=a.get("status"), era=a.get("era"))["experiments"]
        lines = [f"{len(rows)} experiment(s)" + (" of yours" if a.get("mine") else "")]
        verdict = lambda r: ((r.get("verdict") or {}).get("verdict") or (r.get("verdict") or {}).get("status")) if r.get("verdict") else None
        lines += [f"  {r['id']} \"{r.get('title')}\" ({r.get('status')}, {r.get('owner')}): {r.get('runs')} run(s)"
                  + (f", prediction {r['prediction']}" if r.get("prediction") else "") + (f": {verdict(r)}" if verdict(r) else "")
                  for r in rows[-40:]]
        return answer(lines)
    if command == "read_experiment":
        e = x.get(a["id"], base=raw)["experiment"]
        return answer(T.experiment_lines(e, x.diff(a["id"])["diff"], source, model=bool(a.get("draft"))), {"experiment": e} if raw else None)
    if command == "diff_experiment":
        d = x.diff(a["id"])["diff"]
        return answer(T.diff_lines(d), {"diff": d} if raw else None)
    if command == "read_run":
        got = x.run_result(a["experiment"], a["run"])["run"]
        return answer(R.RUN_LINES[got["mode"]](got), {"run": got} if raw else None)
    if command == "create_experiment":
        e = x.create(a.get("title"), a.get("purpose") or "", a.get("version"))["experiment"]
        store.add_experiment(role, e["id"], ticket)
        return answer(T.experiment_lines(e, None, source) + [
            ("the draft is a copy of the model above (read_model shows it): change it with edit_experiment or load_model, check it "
            "with run_replay, see what it does next with run_forward")], experiment=e["id"])
    if command in ("edit_experiment", "load_model"):
        edits = [{"op": "load_model", "model": a.get("model")}] if command == "load_model" else edit_list(a)
        got = x.edit(a["id"], edits)
        return answer(T.experiment_lines(got["experiment"], got["diff"], source, model=command == "load_model"), experiment=a["id"])
    if command == "discard_experiment":
        e = x.discard(a["id"])["experiment"]
        return answer([f"experiment {e['id']} discarded: it stays readable and changes no more"])
    if command in ("run_forward", "run_replay", "run_inverse"):
        return run(x, store, command, a, role, ticket, raw)
    if command == "commit_prediction":
        return commit(x, store, a, role, ticket, source)
    raise StoreError(f"unknown command {command!r}")


def edit_list(a: dict) -> list:
    if a.get("edits") is not None and a.get("edit") is not None:
        raise StoreError("give edit (one) or edits (a batch), not both")
    edits = a.get("edits") if a.get("edits") is not None else [a.get("edit")] if a.get("edit") is not None else None
    if not isinstance(edits, list) or not edits:
        raise StoreError("edits: a list of edits, applied together or not at all (or edit: one), each with its op "
                         "(add_flow, replace_flow, remove_flow, set_params, add_stock, remove_stock, set_level, set_input)")
    return edits


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
    if a.get("experiment"):
        preds = [x.prediction(a["experiment"])["prediction"]]
    elif a.get("id"):
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


def run(x, store: AgentStore, command: str, a: dict, role: str, ticket: str, raw: bool) -> dict:
    mode = command.removeprefix("run_")
    opts: dict[str, Any] = {}
    if mode == "forward":
        opts["horizon_s"] = seconds(a.get("horizon") or "1h", "horizon")
        for k in ("stocks", "bounds", "members"):
            if a.get(k) is not None:
                opts[k] = a[k]
    else:
        opts.update(window(a))
    if mode == "inverse":
        for k in ("targets", "hold", "new_flows", "max_changes", "alternatives", "check"):
            if a.get(k) is not None:
                opts[k] = a[k]
        opts["deadline_s"] = a.get("deadline_s") or 60
    got = x.run(a["id"], mode, **opts)["run"]
    if mode == "inverse" and got["result"].get("experiment"):
        store.add_experiment(role, got["result"]["experiment"], ticket)
    lines = R.RUN_LINES[mode](got)
    return answer(lines, {"run": got} if raw else None, run=got["id"], experiment=a["id"])


def commit(x, store: AgentStore, a: dict, role: str, ticket: str, source: str) -> dict:
    """The experiment as the prediction of the caller's own change. A session on a ticket commits
    only an experiment it made on that ticket."""
    eid = a["id"]
    if ticket:
        mine = {e["id"]: e.get("ticket") for e in store.agent_state(role).get("experiments") or []}
        if mine.get(eid) != ticket:
            raise StoreError(f"refused: experiment {eid} was not made on your ticket {ticket}; a prediction is for your own "
                             "ticket's change, so commit an experiment you made on it")
    horizon = seconds(a.get("horizon"), "horizon")
    made = x.commit(eid, horizon, subject=a.get("subject") or ticket or None)
    p, change = made["prediction"], made["change"]
    store.add_prediction(role, {"id": p.get("id"), "change": change, "experiment": eid, "ticket": ticket, "source": source,
                                "horizon_s": horizon, "at": time.time(), "announced": None})
    lines = [f"prediction {p.get('id')} recorded for change {change}; it will be judged {T.dur(horizon)} after the change is announced",
             "  expects: " + ("nothing to change (the null test)" if not p.get("expect") else
                              "; ".join(R.expectation(e) for e in p.get("expect") or [])),
             "  stocks' predicted trajectories: " + ("from your forward run" if p.get("trajectories") else
                                                      "none (Orchestra will run the previous era's model with your edits)"),
             (f"next: make the change, then announce it with this change id ({change}): announce_change, or, for a configuration "
             "change, the setup tool's change argument. Until it is announced nothing is judged.")]
    return answer(lines, prediction=p.get("id"), change=change)


def own_prediction(store: AgentStore, role: str, change: str) -> dict:
    found = store.find_prediction(change)
    if found is None or found[0] != role:
        raise StoreError(f"refused: {change!r} is not the change of a prediction you committed; commit_prediction gives the id")
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
    ev = store.events.publish(CHANGED, {"prediction": rec.get("id"), "experiment": rec.get("experiment"), "ticket": rec.get("ticket") or None,
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
