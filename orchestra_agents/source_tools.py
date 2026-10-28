"""Another time-series store, as the Supervisor reads it before and after declaring it under
``[[sources]]``: whether it answers, what metrics it has (names, types, labels and how many values
each), what a query returns, and what Orchestra infers to read from it. All through Orchestra's
``orchestra.sources`` (the same reads ``orchestra sources check|infer|preview`` makes); nothing is
written to the store, the metrics store or the configuration, and no credential's value is shown.

Each tool takes a declared source by ``source`` (its name in ``[[sources]]``), or a trial one by
``url`` (and ``auth``, as ``[[sources]]`` writes it: ``{"bearer_env": "WORK_TOKEN"}``), so a store
can be looked at before anything is declared. The credential is read from the environment of the
agents' tools, which is ``orchestra run``'s."""

from __future__ import annotations

import time
from collections import defaultdict

from .config import Project
from .store import StoreError

TRIAL = "undeclared"     # a store looked at by url: no name of its own until it is declared
SHOWN_METRICS = 150
SHOWN_NAMES = 400
LIMIT = 20000
LABEL_VALUES = 6


def _catalogue(project: Project, a: dict):
    from orchestra.metrics import for_project as metrics_for
    from orchestra.sources.catalogue import Catalogue
    from orchestra.sources.spec import for_project, load

    url = (a.get("url") or "").strip()
    if url:
        entry = {"name": TRIAL, "url": url, **({"auth": a["auth"]} if a.get("auth") else {}),
                 **({"match": a["match"]} if a.get("match") else {})}
        specs = load({"sources": [entry]}, project.repo_root)
        if specs.refused:
            raise StoreError("that source cannot be read: " + "; ".join(specs.refused.values()))
        name = TRIAL
    else:
        specs = for_project(project.orchestra)
        name = (a.get("source") or "").strip()
        names = [s.name for s in specs.sources]
        if name and name not in names:
            refused = specs.refused.get(name)
            raise StoreError(f"[[sources]] {name}: refused: {refused}" if refused else
                             f"no source {name!r} in [[sources]] (declared: {', '.join(names) or 'none'}); "
                             "give a url to look at one before declaring it")
    return Catalogue(project.orchestra, metrics_for(project.orchestra), specs), name


def check_sources(project: Project, a: dict) -> dict:
    """Each source (or the trial one): where it is, its credentials present or missing (never
    their values), whether it answers and which API endpoints work; and the refused entries."""
    from orchestra.sources import report
    cat, name = _catalogue(project, a)
    out = {"ok": True, "lines": report.check(cat)}
    if name == TRIAL:
        out["note"] = ("not declared yet: 'undeclared' only stands for the url here. Declare it under [[sources]] with a name "
                       "of the store's own (what it is, e.g. prod_metrics), which prefixes every series read from it")
    return out


def source_schema(project: Project, a: dict) -> dict:
    """What the store has over the last ``minutes`` (default 60). Without ``match``: every metric
    name, one line each (its type from the store's metadata, or guessed from its name; how many
    series; each label with how many values), so a big store fits in one answer. With ``match`` (a
    PromQL selector, or a list): those metrics in detail, with the help text and a few values of
    each label. ``limit`` caps the series read (default 20000)."""
    from orchestra.sources import SourceError
    cat, name = _catalogue(project, a)
    if not name:
        raise StoreError("give a source (its name in [[sources]]) or a url")
    src = cat.remotes[name]
    detail = bool(a.get("match"))
    match = a.get("match") or cat.spec(name).match
    match = [match] if isinstance(match, str) else list(match)
    now, limit = time.time(), int(a.get("limit") or LIMIT)
    start = now - float(a.get("minutes") or 60) * 60
    try:
        sets = src.series(match, start, now, limit)
        meta = src.metadata()
    except SourceError as e:
        raise StoreError(f"the store did not answer: {e}") from e
    fams: dict[str, int] = defaultdict(int)
    values: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for labels in sets:
        n = labels.get("__name__", "")
        fams[n] += 1
        for k, v in labels.items():
            if k != "__name__":
                values[n][k].add(v)

    def kind(n: str) -> str:
        m = meta.get(n) or meta.get(n.rsplit("_", 1)[0]) or {}
        return m.get("type") or _guess(n)

    out: dict = {"ok": True, "source": name, "series_read": len(sets), "metrics": len(fams)}
    if detail:
        rows = [{"name": n, "type": kind(n), "help": ((meta.get(n) or {}).get("help") or "")[:160], "series": fams[n],
                 "labels": {k: {"values": len(v), "e.g.": sorted(v)[:LABEL_VALUES]} for k, v in sorted(values[n].items())}}
                for n in sorted(fams)]
        out["names"] = rows[:SHOWN_METRICS]
    else:
        out["names"] = [f"{n} [{kind(n)}] {fams[n]} series" + ("; " + ", ".join(f"{k}={len(v)}" for k, v in sorted(values[n].items()))
                                                             if values[n] else "") for n in sorted(fams)][:SHOWN_NAMES]
        idle = sorted(set(meta) - set(fams))
        if idle:
            out["described_but_no_series_in_window"] = idle[:100]
        out["next"] = "source_schema with match (e.g. {__name__=~\"http_.*\"}) shows those metrics' help and label values"
    if len(fams) > (SHOWN_METRICS if detail else SHOWN_NAMES):
        out["more"] = "more names than shown: narrow with match"
    if len(sets) >= limit:
        out["capped"] = f"the read hit its limit of {limit} series: the counts are a lower bound and some names may be missing; narrow with match"
    out["hints"] = ("type 'guessed:' is from the name only (the store keeps no metadata for it). A counter only goes up; read it "
                    "as increments or a rate. A label with many values (ids, paths, pods) multiplies series: sum it away unless "
                    "it names the thing you model.")
    return out


def _guess(name: str) -> str:
    for end, kind in (("_total", "counter"), ("_count", "counter (a histogram's or summary's count)"),
                      ("_sum", "counter (a histogram's or summary's sum)"), ("_bucket", "histogram bucket"),
                      ("_created", "creation time: no measurement")):
        if name.endswith(end):
            return f"guessed: {kind}"
    return "guessed: gauge"


def preview_query(project: Project, a: dict) -> dict:
    """What a PromQL query returns from the source (or the project's own store, with neither
    source nor url) over the last ``minutes`` in ``step_s`` steps: each series' labels, points, min,
    max, last and a sparkline."""
    from orchestra.sources import report
    query = (a.get("query") or "").strip()
    if not query:
        raise StoreError("give a PromQL query")
    cat, name = _catalogue(project, a)
    cfg = project.orchestra.config
    lines = report.preview(cat, query, name or None, float(a.get("minutes") or cfg.discovery_window / 60),
                           float(a.get("step_s") or cfg.discovery_step))
    return {"ok": True, "lines": lines}


def infer_source(project: Project, a: dict) -> dict:
    """What Orchestra infers to read from the source over the discovery window: its metric
    families (kind, stats, reporting interval, labels with their roles and cardinality), the
    definitions it would read (each with its query, view, kind and score) and those it leaves out
    with why. This is what discovery would get; ``[[series]]`` entries pin what it does not rank."""
    from orchestra.sources import report
    cat, name = _catalogue(project, a)
    return {"ok": True, "lines": report.infer(cat, project.orchestra, name or None)}


COMMANDS = {"check_sources": check_sources, "source_schema": source_schema, "preview_query": preview_query,
            "infer_source": infer_source}
