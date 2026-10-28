"""Another time-series store, as the Supervisor reads it before and after declaring it under
``[[sources]]``: whether it answers, what metrics it has (names, types, labels and how many values
each), what a query returns, and what Orchestra infers to read from it. All through Orchestra's
``orchestra.sources`` (the same reads ``orchestra sources check|infer|preview`` makes); nothing is
written to the store, the metrics store or the configuration, and no credential's value is shown.

Each tool takes a declared source by ``source`` (its name in ``[[sources]]``), or a trial one by
``url`` (and ``auth``, as ``[[sources]]`` writes it: ``{"bearer_file": "~/.config/x/token"}`` or
``{"bearer_env": "WORK_TOKEN"}``), so a store
can be looked at before anything is declared. The credential is read from the environment of the
agents' tools, which is ``orchestra run``'s."""

from __future__ import annotations

import re
import time
from collections import defaultdict

from .balance_tools import CUMULATIVE
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
    from orchestra.sources.listing import listing
    try:
        got = listing(src, match, start, now, concurrency=cat.spec(name).concurrency)
        meta = src.metadata()
    except SourceError as e:
        raise StoreError(f"the store did not answer: {e}") from e
    big = {**got.too_big, **got.failed}
    fams: dict[str, int] = defaultdict(int)
    values: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    sets = 0
    for n, label_sets in got.names.items():
        fams[n] += len(label_sets)
        sets += len(label_sets)
        for labels in label_sets[:limit]:
            for k, v in labels.items():
                values[n][k].add(v)

    def kind(n: str) -> str:
        m = meta.get(n) or meta.get(n.rsplit("_", 1)[0]) or {}
        return m.get("type") or _guess(n)

    out: dict = {"ok": True, "source": name, "series_read": sets, "metrics": len(fams), "requests": got.requests}
    if not detail and (lv := _levels(fams, values, kind)):           # first: what the answer is read for
        out["levels"] = lv
        out["levels_next"] = ("these gauges hold amounts (queues, lags, connections, volumes, inventories): each may be a "
                              "stock. Call find_balance with level \"all\" before choosing what to read")
    if got.notes:
        out["notes"] = got.notes
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
    if big:
        out["too_big_to_list"] = [f"{n} [{kind(n)}]: not listed ({why[:120]}); narrow it with label matchers in match"
                                  for n, why in sorted(big.items())][:100]
    if len(fams) > (SHOWN_METRICS if detail else SHOWN_NAMES):
        out["more"] = "more names than shown: narrow with match"
    if sets >= limit:
        out["capped"] = f"the read hit its limit of {limit} series: the counts are a lower bound and some names may be missing; narrow with match"
    if late := _lateness(src, sorted(fams, key=lambda n: -fams[n])[:3], now):
        out["newest_sample_age_s"] = late
        worst = max(late.values())
        if worst > 30:
            out["late"] = (f"the newest samples are up to {worst:.0f} s old: the store writes late or reports coarsely. "
                           "Set settle on its [[sources]] entry above that plus the store's own 30 s "
                           f"(here settle = \"{int((worst + 30) // 30 + 1) * 30}s\"), or the latest steps of every "
                           "discovery read as gaps")
    out["hints"] = ("type 'guessed:' is from the name only (the store keeps no metadata for it). A counter only goes up; read it "
                    "as increments or a rate. A label with many values (ids, paths, pods) multiplies series: sum it away unless "
                    "it names the thing you model.")
    return out


LEVEL_WORDS = re.compile(r"(^|_)(depth|messages|lag|active|open|inflight|in_flight|pending|backlog|queued|ready|unacked|"
                         r"waiting|used|inventory|units|sessions|connections|items|jobs|size)($|_)", re.IGNORECASE)
NOT_LEVELS = re.compile(r"_(total|bucket|count|sum|created|seconds|ratio|percent|timestamp|info)$|(^|_)(cpu|memory|heap|gc|"
                        r"threads?|goroutines|fds|scrape|up)($|_)|^(kube|node|prometheus|go|process|jvm)_", re.IGNORECASE)
SUBJECTS = ("queue", "consumergroup", "group", "topic", "persistentvolumeclaim", "volume", "pool", "warehouse",
            "cluster_name", "envoy_cluster_name", "deployment")


def _levels(fams: dict, values: dict, kind) -> list[str]:
    """The gauges that look like amounts held (``LEVEL_WORDS``, not ``NOT_LEVELS``), each with the
    label that names what it holds and a few of its values: the levels to search flows for."""
    out = []
    for n in sorted(fams):
        snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", n)
        if not LEVEL_WORDS.search(snake) or NOT_LEVELS.search(snake) or CUMULATIVE.search(snake) or "counter" in kind(n):
            continue
        sub = next((k for k in SUBJECTS if k in values[n]), None)
        if sub:
            vals = sorted(values[n][sub])
            out.append(f"{n} by {sub}: " + ", ".join(vals[:6]) + (f" (+{len(vals) - 6})" if len(vals) > 6 else ""))
        else:
            out.append(n)
    return out[:40]


def _lateness(src, names: list[str], now: float) -> dict[str, float]:
    """How old each metric's newest sample is now, in seconds: how late the store writes (a store
    written a minute late needs ``settle`` above it). Empty when the store cannot say."""
    from orchestra.sources import SourceError
    out = {}
    for n in names:
        if not re.fullmatch(r"[A-Za-z_:][A-Za-z0-9_:]*", n or ""):
            continue
        try:
            got = src.query_range(f"time() - max(timestamp({n}))", now - 120, now, 60)
        except SourceError:
            continue
        pts = [v for _, p in got for _, v in p[-1:]]
        if pts:
            out[n] = round(max(pts), 1)
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
