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

import hashlib
import re
import time
from collections import defaultdict

from .balance_tools import CUMULATIVE
from .config import Project
from .store import StoreError

TRIAL = "trial"          # a store looked at by url is named trial_<hash of its url> until it is declared
SHOWN_METRICS = 150
SHOWN_NAMES = 400
LIMIT = 20000
LABEL_VALUES = 6
SCHEMA_REQUESTS = 100    # what one source_schema may ask of a store, and for how long
SCHEMA_SECONDS = 60.0
PREVIEW_REQUESTS = 20    # ... one preview_query
PREVIEW_SECONDS = 60.0
PREVIEW_SERIES = 50      # a preview returns at most this many series (topk)


def trial_name(url: str) -> str:
    """A store looked at by url, named by its url: the same name every call, so what Orchestra
    keeps of it (its rate, its read cache) carries over between calls."""
    return f"{TRIAL}_{hashlib.sha256(url.strip().rstrip('/').encode()).hexdigest()[:8]}"


def _catalogue(project: Project, a: dict):
    from orchestra.metrics import for_project as metrics_for
    from orchestra.sources.catalogue import Catalogue
    from orchestra.sources.spec import for_project, load

    url = (a.get("url") or "").strip()
    if url:
        name = trial_name(url)
        entry = {"name": name, "url": url, **({"auth": a["auth"]} if a.get("auth") else {}),
                 **({"match": a["match"]} if a.get("match") else {})}
        specs = load({"sources": [entry]}, project.repo_root)
        if specs.refused:
            raise StoreError("that source cannot be read: " + "; ".join(specs.refused.values()))
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
    if name.startswith(TRIAL + "_"):
        out["note"] = (f"not declared yet: '{name}' only stands for the url here. Declare it under [[sources]] with a name "
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
    spec = cat.spec(name)
    detail = bool(a.get("match"))
    now, limit = time.time(), int(a.get("limit") or LIMIT)
    start = now - float(a.get("minutes") or 60) * 60
    from orchestra.sources.limits import Budget
    from orchestra.sources.listing import TooManyNames, listing
    src.budget = Budget(SCHEMA_REQUESTS, time.monotonic() + SCHEMA_SECONDS, "source_schema's reads")
    try:
        if detail:
            match = [a["match"]] if isinstance(a["match"], str) else list(a["match"])
            got = listing(src, match, start, now, concurrency=spec.concurrency, max_names=spec.max_names, limit=limit)
            names = sorted(got.names)
        else:                                  # names only: one request, whatever the store's size
            names = sorted(set(src.names(spec.match, start, now, limit=spec.max_names + 1)))
            if len(names) > spec.max_names:
                raise TooManyNames(f"more than {spec.max_names} metric names")
            # the levels' label values, listed for those names alone (what each holds)
            wanted = level_names(names)
            got = listing(src, wanted, start, now, concurrency=spec.concurrency, limit=limit) if wanted else None
        meta = src.metadata(sorted(got.names) if detail else names)
    except TooManyNames as e:
        raise StoreError(f"the store is large: {e}. Give match, a selector per family you need (e.g. "
                         "{__name__=~\"rabbitmq_.*\"}), and look at each") from e
    except SourceError as e:
        raise StoreError(f"the store did not answer: {e}") from e
    finally:
        src.budget = None
    fams: dict[str, int] = defaultdict(int)
    values: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    sets = 0
    for n, label_sets in (got.names.items() if got else []):
        fams[n] += len(label_sets)
        sets += len(label_sets)
        for labels in label_sets[:limit]:
            for k, v in labels.items():
                values[n][k].add(v)

    def kind(n: str) -> str:
        m = meta.get(n) or meta.get(n.rsplit("_", 1)[0]) or {}
        return m.get("type") or _guess(n)

    out: dict = {"ok": True, "source": name, "metrics": len(names)}
    if not detail and (lv := _levels({n: fams.get(n, 0) for n in level_names(names)}, values, kind)):   # first: what the answer is read for
        out["levels"] = lv
        out["levels_next"] = ("these gauges hold amounts (queues, lags, connections, volumes, inventories): each may be a "
                              "stock. Call find_balance with level \"all\" before choosing what to read")
    if got is not None and got.notes:
        out["notes"] = got.notes
    if detail:
        out["series_read"] = sets
        rows = [{"name": n, "type": kind(n), "help": ((meta.get(n) or {}).get("help") or "")[:160], "series": fams[n],
                 "labels": {k: {"values": len(v), "e.g.": sorted(v)[:LABEL_VALUES]} for k, v in sorted(values[n].items())}}
                for n in sorted(fams)]
        out["names"] = rows[:SHOWN_METRICS]
        big = {**got.too_big, **got.failed}
        if big:
            out["too_big_to_list"] = [f"{n} [{kind(n)}]: not listed ({why[:120]}); narrow it with label matchers in match"
                                      for n, why in sorted(big.items())][:100]
        if sets >= limit:
            out["capped"] = f"the read hit its limit of {limit} series: the counts are a lower bound; narrow with match"
    else:
        out["names"] = [f"{n} [{kind(n)}]" for n in names][:SHOWN_NAMES]
        out["next"] = ("source_schema with match (e.g. {__name__=~\"http_.*\"}) shows those metrics' series, labels, help and "
                       "label values")
    if len(names) > (SHOWN_METRICS if detail else SHOWN_NAMES):
        out["more"] = "more names than shown: narrow with match"
    probe = _probes(sorted(fams, key=lambda n: fams[n]) if fams else names)
    if late := _lateness(src, probe, now):
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


def level_names(names: list[str]) -> list[str]:
    """The names that look like amounts held, by name alone (``_levels`` without the type)."""
    out = []
    for n in names:
        snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", n)
        if LEVEL_WORDS.search(snake) and not NOT_LEVELS.search(snake) and not CUMULATIVE.search(snake):
            out.append(n)
    return out


def _levels(fams: dict, values: dict, kind) -> list[str]:
    """The gauges that look like amounts held (``LEVEL_WORDS``, not ``NOT_LEVELS``), each with the
    label that names what it holds and a few of its values: the levels to search flows for."""
    out = []
    for n in sorted(fams):
        snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", n)
        if not LEVEL_WORDS.search(snake) or NOT_LEVELS.search(snake) or CUMULATIVE.search(snake) or "counter" in kind(n):
            continue
        sub = next((k for k in SUBJECTS if k in values.get(n, {})), None)
        if sub:
            vals = sorted(values[n][sub])
            out.append(f"{n} by {sub}: " + ", ".join(vals[:6]) + (f" (+{len(vals) - 6})" if len(vals) > 6 else ""))
        else:
            out.append(n)
    return out[:40]


def _probes(names: list[str], most: int = 4) -> list[str]:
    """The metrics to ask how late the store writes: the first (cheapest) of each name prefix, as a
    rule one exporter each, since one exporter may write a minute later than the others."""
    out, seen = [], set()
    for n in names:
        head = re.split(r"[_:]", n, maxsplit=1)[0]
        if head not in seen:
            seen.add(head)
            out.append(n)
        if len(out) >= most:
            break
    return out


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


AGGREGATED = re.compile(r"\b(sum|avg|min|max|count|topk|bottomk|quantile|group|count_values|stddev|stdvar|histogram_quantile)\s*(\(|by\b|without\b)")
MATCHER = re.compile(r"\{[^}]*[A-Za-z_]\w*\s*(=|!=|=~|!~)\s*\"")


def preview_query(project: Project, a: dict) -> dict:
    """What a PromQL query returns from the source (or the project's own store, with neither
    source nor url) over the last ``minutes`` in ``step_s`` steps: each series' labels, points, min,
    max, last and a sparkline."""
    from orchestra.sources import report
    query = (a.get("query") or "").strip()
    if not query:
        raise StoreError("give a PromQL query")
    if not AGGREGATED.search(query) and not MATCHER.search(query):
        raise StoreError("a query with no aggregation and no label matcher reads every series of a metric: add a matcher "
                         "({queue=\"x\"}) or aggregate it (sum by (queue) (...))")
    cat, name = _catalogue(project, a)
    cfg = project.orchestra.config
    if name:
        from orchestra.sources.limits import Budget
        cat.remotes[name].budget = Budget(PREVIEW_REQUESTS, time.monotonic() + PREVIEW_SECONDS, "preview_query's reads")
    lines = report.preview(cat, f"topk({PREVIEW_SERIES}, {query})", name or None, float(a.get("minutes") or cfg.discovery_window / 60),
                           float(a.get("step_s") or cfg.discovery_step))
    return {"ok": True, "lines": lines}


def infer_source(project: Project, a: dict) -> dict:
    """What Orchestra infers to read from the source over the discovery window: its metric
    families (kind, stats, reporting interval, labels with their roles and cardinality), the
    definitions it would read (each with its query, view, kind and score) and those it leaves out
    with why. This is what discovery would get; ``[[series]]`` entries pin what it does not rank."""
    from orchestra.sources import report
    cat, name = _catalogue(project, a)
    return {"ok": True, "lines": report.infer(cat, project.orchestra, name or None, refresh=bool(a.get("refresh")))}


COMMANDS = {"check_sources": check_sources, "source_schema": source_schema, "preview_query": preview_query,
            "infer_source": infer_source}
