"""The Supervisor's explore loop, through the core's own commands as a library
(Orchestra's ``docs/proposals/metric-explorer.md``; its guide, "Exploring beyond the match"): the
explorer reads, discovery concludes.

- ``explore_source``: ``orchestra sources explore`` (``orchestra.sources.exploring.explore``), the
  bounded survey of a declared source's ``explore`` scope beyond its ``match``: every request within
  ``explore``, counted against the store's rate and daily cap, logged ``by: explore``, refused before
  anything is sent when its worst case would pass the cap;
- ``read_discovery_runs``: the run record (``ModelStore.runs()``): the last discovery runs, each as the
  experiment log summarises one (stocks, flows, left out, trust, fitted, R², requests), with its
  trigger, outcome and what was partial; with ``key``, the explain record of one series
  (``orchestra.web.dynamics.explain_view``: what discovery read and tried for it, and why it is a
  stock or was left out);
- ``read_experiments``, ``log_experiment``, ``judge_experiment``: the experiment log
  (``orchestra explorer log list|add|judge``, ``ModelStore.explorer()``), each entry recorded
  ``by`` ``supervisor``.

Reading changes nothing. The log records and changes nothing either: a change to ``match``,
``split``, ``kinds`` or ``names`` is made with ``set_setting`` key ``sources`` (``source_limits``
guards the scopes: ``explore`` is the owner's alone, and a ``match`` that widens needs the owner's
words). No credential's value is ever shown."""

from __future__ import annotations

import json

from .config import Project
from .store import StoreError

RUNS = 5                 # discovery runs read back by default
MOST_RUNS = 30
EXPERIMENTS = 20         # experiments shown, newest last
ROW_KEYS = ("family", "in_match", "kind", "reads_as", "type", "labels", "labels_more", "why", "declared", "form")


def _spec(project: Project, name: str):
    from orchestra.sources.spec import for_project
    specs = for_project(project.orchestra)
    spec = next((s for s in specs.sources if s.name == name), None)
    if spec is None:
        why = specs.refused.get(name or "") or f"no usable source {name!r} (sources: {', '.join(s.name for s in specs.sources) or 'none'})"
        raise StoreError(f"{name or 'explore_source needs source'}: {why}")
    return spec


def explore_source(project: Project, a: dict) -> dict:
    """The bounded survey (module docstring) of one declared source, as the core's command gives it."""
    from orchestra.sources import exploring
    spec = _spec(project, str(a.get("source") or "").strip())
    out = exploring.explore(project.orchestra.state_dir, spec, family=a.get("family") or None, limit=a.get("limit"),
                            minutes=a.get("minutes"))
    rows = [{k: r[k] for k in ROW_KEYS if r.get(k) not in (None, "", {}, [])} for r in out.get("families") or []]
    got = {"ok": not out.get("refused"), "source": spec.name, "explore": out.get("explore"), "match": out.get("match"),
           "lines": exploring.table(out), "families": rows, "not_surveyed": len(out.get("not_surveyed") or []),
           "requests": out.get("requests", 0), "notes": out.get("notes") or []}
    for k in ("refused", "error"):
        if out.get(k):
            got[k] = out[k]
    return got


def _runs(project: Project):
    from orchestra.sd.store import ModelStore
    return ModelStore.for_state(project.orchestra.state_dir)


def read_discovery_runs(project: Project, a: dict) -> dict:
    """The last discovery runs from the run record, newest last; with ``key``, one series' explain record."""
    from orchestra.sd.explorer import summary
    n = max(1, min(int(a.get("n") or RUNS), MOST_RUNS))
    runs = [r for r in _runs(project).runs().recent(0) if r.get("kind") == "discovery"][-n:]
    out = {"ok": True, "runs": [{**(summary(r) or {}), "trigger": r.get("trigger"), "partial": r.get("partial"),
                                 "duration_s": r.get("duration_s")} for r in runs]}
    if not runs:
        out["note"] = "no discovery run recorded yet: the host's next one (every [discovery] every) writes the first"
    if key := str(a.get("key") or "").strip():
        from orchestra.web.dynamics import explain_view
        out["explain"] = explain_view(project.orchestra, key)
    return out


def read_experiments(project: Project, a: dict) -> dict:
    """The experiment log, newest last (``n``, default 20), and the ids still to be judged."""
    log = _runs(project).explorer()
    entries = log.entries()
    n = max(1, int(a.get("n") or EXPERIMENTS))
    return {"ok": True, "path": str(log.path), "count": len(entries), "experiments": entries[-n:],
            "pending": [e["id"] for e in entries if not e.get("verdict")]}


def _change(v) -> dict:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError as e:
            raise StoreError(f"change is not JSON: {e}") from None
    return v


def log_experiment(project: Project, a: dict) -> dict:
    """Record one experiment before its effect is known, with the last discovery run as ``before``."""
    from orchestra.sd.explorer import ExperimentError
    try:
        e = _runs(project).explorer().add(_change(a.get("change")), str(a.get("expect") or ""), by="supervisor")
    except ExperimentError as err:
        raise StoreError(str(err)) from None
    return {"ok": True, "experiment": e, "next": "Make the change now (set_setting key sources), or ask the owner in one "
                                               "sentence when it widens match; judge it after discovery's next rounds."}


def judge_experiment(project: Project, a: dict) -> dict:
    """Judge one experiment by the discovery runs since it was recorded."""
    from orchestra.sd.explorer import ExperimentError
    try:
        e = _runs(project).explorer().judge(str(a.get("id") or ""), str(a.get("verdict") or ""), str(a.get("why") or ""),
                                            by="supervisor")
    except ExperimentError as err:
        raise StoreError(str(err)) from None
    return {"ok": True, "experiment": e}


READS = {"explore_source": explore_source, "read_discovery_runs": read_discovery_runs, "read_experiments": read_experiments}
WRITES = {"log_experiment": log_experiment, "judge_experiment": judge_experiment}
COMMANDS = {**READS, **WRITES}


def handle(project: Project, command: str, a: dict, role: str) -> dict:
    if command in WRITES and role != "supervisor":
        raise StoreError(f"only the supervisor records experiments ({command})")
    return COMMANDS[command](project, a)
