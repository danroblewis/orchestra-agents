"""``find_balance``: the counters that fill and drain one level, searched for in the stores.

The Supervisor names a level (a PromQL selector: a queue's depth, a pool's open connections, a
volume's used bytes) and the stores to search (the declared ``[[sources]]`` by default, or trial
ones by ``url``). The tool:

1. **narrows** each store's metric names to candidates: counters (and gauges that only rise, such
   as Kafka offsets) that share a word with the level's name or its label values (the queue, the
   topic, the pool, the service), in the level's unit, the best ``max_candidates`` of them by how
   rare the shared words are in the store; a candidate that carries a label the level is selected
   by is read for the same value, and one whose label values name the level's subject is read per
   value; a staging or test copy is never read;
2. **reads** the level and every candidate through ``orchestra.sources`` (its retries, rate limit
   and a budget for the call), the counters as ``increase`` per step on the store, for the same
   window ending ``settle`` before now;
3. **searches** with Orchestra's own discovery code (``orchestra.sd.flows_of``: the balance search,
   the cross-resolution balance, the evidence gates), and
4. **returns** each balance found: the counters with their sign, the fit (R², the exact share,
   held-out R², the running residual, the shift null), whether discovery would accept it and why
   not, what is left unmeasured (a residual), and the ``[[series]]`` entries that declare it
   (``stock``/``role``), ready for ``set_setting`` key ``series``.

Nothing is written anywhere; no credential's value is read or shown."""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations, pairwise

from .config import Project
from .store import StoreError

MAX_CANDIDATES = 16       # metric names read as candidates, over every store
SAME_SCALE = 0.1          # two levels within this share of each other on average ...
SAME_CORR = 0.98          # ... whose values move together this closely are one quantity read twice
PARTS = re.compile(r"_(ready|unacked|bytes|max|pending|in_flight)$")   # a part or aspect of another level
MAX_LEVELS = 32           # levels searched in one call
MAX_GROUPS = 8            # series per candidate name, when read per value of a label naming the level's subject
WINDOW_MIN = 60
SETTLE = 90.0             # every read ends at least this long before now (a store written a minute late)
REQUESTS = 300            # per store, per call, retries included
DEADLINE = 150.0          # seconds, for all of a call's reads
STOP = {"total", "sum", "count", "bucket", "created", "info", "value", "values", "current", "active", "used", "stats", "stat",
        "status", "num", "number", "size", "the", "and", "per", "all", "http", "metrics", "prod", "production", "data",
        "seconds", "bytes", "byte", "ratio", "percent", "gauge", "counter"}
GENERIC = {"instance", "pod", "node", "job", "namespace", "endpoint", "service", "prometheus", "metrics_path", "cluster",
           "container", "uid", "image", "le", "quantile", "env", "environment", "deployment_environment", "stage", "region",
           "zone", "prometheus_replica", "replica", "host", "hostname", "version", "id"}
ENV_LABELS = ("env", "environment", "deployment_environment", "stage")
NOT_PROD = re.compile(r"^(staging|stage|stg|test|testing|dev|development|qa|sandbox|canary)$", re.IGNORECASE)
NOT_FLOWS = re.compile(r"_(bucket|count|sum|created)$|(^|_)(duration|latency|timestamp|ratio|quantile|percent|info|up)($|_)")
CUMULATIVE = re.compile(r"(^|_)(offsets?|sequence|seq|position|lsn|watermark|processed|consumed|produced|published|sent|received|"
                        r"created|deleted|completed|started|finished|handled|served|delivered|acked|acknowledged)($|_)", re.IGNORECASE)
NAME = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*")


def tokens(text: str) -> set[str]:
    """The words of a name or a label value, lower case, plurals and stop words dropped."""
    text = re.sub(r"([a-z])([A-Z])", r"\1_\2", text)
    out = set()
    for w in re.split(r"[^A-Za-z0-9]+", text.lower()):
        if len(w) < 2 or w.isdigit() or w in STOP:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def matchers(selector: str) -> dict[str, str]:
    """The equality matchers of a selector (``{queue="email"}``): label -> value."""
    inner = selector.partition("{")[2].rpartition("}")[0]
    return {m.group(1): m.group(2) for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"', inner)}


def metric_of(selector: str) -> str:
    s = selector.strip()
    bare = s.partition("{")[0].strip()
    if NAME.fullmatch(bare):
        return bare
    m = re.search(r'__name__\s*=\s*"([^"]+)"', s)
    return m.group(1) if m else ""


def _with(selector: str, extra: dict[str, str]) -> str:
    if not extra:
        return selector
    add = ",".join(f'{k}="{v}"' for k, v in sorted(extra.items()))
    if "{" in selector:
        head, _, rest = selector.partition("{")
        return f"{head}{{{add},{rest}" if rest.strip() != "}" else f"{head}{{{add}}}"
    return f"{selector}{{{add}}}"


class Store:
    """One store searched: its name (as declared, or a trial name), the source, its spec."""

    def __init__(self, name: str, src, spec, declared: bool):
        self.name, self.src, self.spec, self.declared = name, src, spec, declared
        self.names: list[str] | None = None      # its metric names, listed once per call
        self.reads: dict = {}                    # candidate reads, by (selector, by): shared by the call's levels


def _stores(project: Project, a: dict) -> tuple[Store, list[Store]]:
    """The level's store and every store searched (the level's included)."""
    from orchestra.metrics import for_project as metrics_for
    from orchestra.sources.catalogue import Catalogue
    from orchestra.sources.spec import for_project, load

    specs = for_project(project.orchestra)
    declared = {s.name: s for s in specs.sources}
    trial: list[dict] = []

    def ref(x) -> str:
        if isinstance(x, dict) and not x.get("url"):
            x = x.get("name") or x.get("source") or ""
        if isinstance(x, dict):
            name = "undeclared" if not trial else f"undeclared_{len(trial) + 1}"
            trial.append({"name": name, "url": x["url"], **({"auth": x["auth"]} if x.get("auth") else {})})
            return name
        name = str(x or "").strip()
        if name not in declared:
            refused = specs.refused.get(name)
            raise StoreError(f"[[sources]] {name}: refused: {refused}" if refused else
                             f"no source {name!r} in [[sources]] (declared: {', '.join(declared) or 'none'}); give a url to search one "
                             "before declaring it")
        return name

    level_ref = ref({"url": a["url"], "auth": a.get("auth")} if a.get("url") else (a.get("source") or (next(iter(declared), ""))))
    names = [level_ref]
    for x in a.get("sources") or list(declared):
        n = ref(x)
        if n not in names:
            names.append(n)
    if trial:
        got = load({"sources": trial}, project.repo_root)
        if got.refused:
            raise StoreError("that store cannot be read: " + "; ".join(f"{k}: {v}" for k, v in got.refused.items()))
        for s in got.sources:
            declared[s.name] = s
    from orchestra.sources.spec import Specs
    cat = Catalogue(project.orchestra, metrics_for(project.orchestra), Specs(sources=[declared[n] for n in names]))
    stores = [Store(n, cat.remotes[n], declared[n], not n.startswith("undeclared")) for n in names]
    return stores[0], stores


def _production(level: str, sets: list[dict]) -> str:
    """The level, kept to production when its series carry an environment label with a staging
    or test value beside one other (``env="prod"`` added), unless the selector names one."""
    pinned = matchers(level)
    for k in ENV_LABELS:
        vals = {x[k] for x in sets if k in x}
        if k in pinned or not any(NOT_PROD.match(v) for v in vals):
            continue
        prod = sorted(v for v in vals if not NOT_PROD.match(v))
        if len(prod) == 1:
            return _with(level, {k: prod[0]})
    return level


def _budget(stores: list[Store], levels: int = 1) -> None:
    from orchestra.sources.limits import Budget
    until = time.monotonic() + DEADLINE * min(levels, MAX_LEVELS)
    for s in stores:
        s.src.budget = Budget(REQUESTS * levels, until, "find_balance's reads")


def _level_tokens(level: str, label_sets: list[dict]) -> set[str]:
    words = tokens(metric_of(level))
    pinned = matchers(level)
    for k, v in pinned.items():
        if k != "__name__" and k not in GENERIC:
            words |= tokens(v)
    for k in {k for labels in label_sets for k in labels} - GENERIC:
        vals = {labels.get(k) for labels in label_sets}
        if len(vals) == 1 and None not in vals:
            words |= tokens(vals.pop())
    return words


def _score(name: str, words: set[str], df: Counter, n_names: int) -> float:
    shared = tokens(name) & words
    return sum(math.log(1 + n_names / (1 + df[w])) for w in shared)


def _candidates(store: Store, level_metric: str, words: set[str], unit: str, start: float, end: float, name_unit,
                meta: dict) -> list[tuple[float, str, Store]]:
    from orchestra.sources import SourceError
    if store.names is None:
        try:
            store.names = sorted(set(store.src.names(['{__name__=~".+"}'], start, end)))
        except SourceError:
            from orchestra.sources.listing import listing
            store.names = sorted(listing(store.src, ['{__name__=~".+"}'], start, end).names)
    names = store.names
    df = Counter(w for n in names for w in tokens(n))
    out = []
    for n in names:
        if n == level_metric or NOT_FLOWS.search(n) or name_unit(n, {}) != unit:
            continue
        if _kind(store, n, meta) == "gauge" and not CUMULATIVE.search(n):
            continue                     # a gauge is a candidate only when its name says it counts (an offset)
        sc = _score(n, words, df, len(names))
        if sc > 0:
            out.append((sc, n, store))
    return out


def _plan(store: Store, name: str, level_labels: dict[str, str], words: set[str], start: float, end: float) -> tuple[str, list[str]]:
    """The selector a candidate is read with and the labels it is read by: the level's own subject
    labels as matchers where the candidate has that value, production only, and by a label whose
    values name the level's subject (a topic named for the consumer group's orders)."""
    sets = store.src.series([name], start, end, 2000)
    have: dict[str, set[str]] = {}
    for labels in sets:
        for k, v in labels.items():
            if k != "__name__":
                have.setdefault(k, set()).add(v)
    match = {k: v for k, v in level_labels.items() if k in have and v in have[k]}
    sel = _with(name, match)
    by = []
    for k in ENV_LABELS:
        if k in have and any(NOT_PROD.match(v) for v in have[k]):
            prod = sorted(v for v in have[k] if not NOT_PROD.match(v))
            if len(prod) == 1:
                sel = _with(sel, {k: prod[0]})
    for k, vals in sorted(have.items()):
        if k in GENERIC or k in match or len(vals) < 2 or len(vals) > MAX_GROUPS * 4:
            continue
        if any(tokens(v) & words for v in vals):
            by.append(k)
    return sel, by[:1]


def _resolution(store: Store, sel: str, end: float) -> float | None:
    from orchestra.sources import SourceError
    try:
        got = store.src.query_range(f"max(count_over_time({sel}[10m]))", end, end, 60)
    except SourceError:
        return None
    vals = [v for _, pts in got for _, v in pts if v > 0]
    return 600.0 / max(vals) if vals else None


def _read_counter(store: Store, name: str, sel: str, by: list[str], kind: str, start: float, end: float, step: float):
    """The candidate's series: (key name, labels, points, Declared). A counter is read as its
    increase per step (on a grid no finer than two of its samples), accumulated from 0; a gauge as
    its value, kept only when it never falls but at a reset (an offset)."""
    from orchestra.sd.series import Declared
    from orchestra.sources.fetch import accumulate
    from orchestra.sources.plan import counter_step, grid_step
    res = _resolution(store, sel, end)
    grp = f" by ({', '.join(by)})" if by else ""
    out = []
    if kind == "counter":
        s = counter_step(step, res)
        t0 = math.floor(start / s) * s
        rows = store.src.query_range(f"sum{grp}(increase({sel}[{int(s)}s]))", t0, end, s)
        for labels, pts in accumulate(rows, t0, s):
            out.append((labels, _held(pts, start, end, step), Declared("counter", resolution_s=s)))
    else:
        q = grid_step(step, res)
        rows = store.src.query_range(f"sum{grp}({sel})", math.floor(start / q) * q, end, q)
        for labels, pts in rows:
            drops = sum(1 for (_, a), (_, b) in pairwise(pts) if b < a)
            if pts and drops <= 2 and pts[-1][1] > pts[0][1]:
                out.append((labels, _held(pts, start, end, step), Declared("counter", baseline=True, resolution_s=res)))
    keep = []
    for labels, pts, kind_ in out:
        if any(NOT_PROD.match(v) for k, v in labels.items() if k in ENV_LABELS):
            continue
        keep.append((f"{store.name}:{name}", {k: v for k, v in labels.items() if k in by}, pts, kind_, sel, by))
    return keep[:MAX_GROUPS]


def _held(pts: list, start: float, end: float, step: float) -> list:
    """Points read on a coarser grid than the window's, held on its steps, as a store's samples read
    on a finer grid are: discovery then balances the level on the stretches between them (``sd.coarse``)."""
    if len(pts) < 2 or min(b - a for (a, _), (b, _) in pairwise(pts)) <= step * 1.01:
        return pts
    out, i = [], 0
    t = pts[0][0]
    while t <= end + 1e-6:
        while i + 1 < len(pts) and pts[i + 1][0] <= t + 1e-6:
            i += 1
        out.append((t, pts[i][1]))
        t += step
    return out


def _copy_of(pts: list, level: list) -> bool:
    """A gauge that is the level measured again (a queue's ready messages beside its depth, while
    nothing is unacked): its values less the level's are constant where both have a point."""
    at = dict(level)
    diffs = [v - at[t] for t, v in pts if t in at]
    if len(diffs) < 3:
        return False
    m = sum(diffs) / len(diffs)
    vals = [v for _, v in level]
    spread = max(vals) - min(vals)
    return spread > 0 and max(abs(d - m) for d in diffs) <= 0.01 * spread


def _kind(store: Store, name: str, meta: dict) -> str:
    m = meta.get(name) or {}
    t = (m.get("type") or "").lower()
    if t in ("counter", "gauge"):
        return t
    return "counter" if name.endswith("_total") else "gauge"


def _stock_name(given: str, level: str) -> str:
    """The stock's name: the one given, else the values the level is selected by (its queue, its
    volume), else its metric's name; lower case, at most 40 characters, cut at a word."""
    own = [v for k, v in matchers(level).items() if k != "__name__" and k not in ENV_LABELS]
    raw = given or ("_".join(own) if own else metric_of(level) or "level")
    name = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    while len(name) > 40 and "_" in name:
        name = name.rsplit("_", 1)[0]
    return name[:40] or "level"


def _decl_name(metric: str, labels: dict[str, str]) -> str:
    base = metric.removesuffix("_total")
    extra = "_".join(re.sub(r"[^a-z0-9]+", "_", v.lower()).strip("_") for v in labels.values())
    name = re.sub(r"[^a-z0-9_]+", "_", f"{base}_{extra}" if extra else base).strip("_")
    return name[:60]


def expand(levels) -> list[str]:
    """The levels asked for: one selector, a list, or ``source_schema``'s entries as written
    (``name by label: a, b (+3)``: one level per value shown)."""
    out = []
    for x in [levels] if isinstance(levels, str) else list(levels or []):
        x = str(x).strip()
        m = re.fullmatch(r"([A-Za-z_:][A-Za-z0-9_:]*) by ([A-Za-z_][A-Za-z0-9_]*): (.+?)(?: \(\+\d+\))?", x)
        if m:
            out += [f'{m.group(1)}{{{m.group(2)}="{v.strip()}"}}' for v in m.group(3).split(", ") if v.strip()]
        elif x:
            out.append(x)
    return list(dict.fromkeys(out))


def all_levels(stores: list[Store], start: float, end: float) -> list[str]:
    """Every searched store's levels, as ``source_schema`` lists them (``source_tools._levels``)."""
    from orchestra.sources import SourceError
    from orchestra.sources.listing import listing

    from .source_tools import _guess, _levels
    per: list[list[str]] = []
    for st in stores:
        try:
            got = listing(st.src, ['{__name__=~".+"}'], start, end, concurrency=st.spec.concurrency)
            meta = st.src.metadata()
        except SourceError:
            continue
        fams = {n: len(v) for n, v in got.names.items()}
        values: dict = {n: {} for n in fams}
        for n, sets in got.names.items():
            for labels in sets:
                for k, v in labels.items():
                    values[n].setdefault(k, set()).add(v)
        got = expand(_levels(fams, values, lambda n, meta=meta: (meta.get(n) or {}).get("type") or _guess(n)))
        per.append(sorted(got, key=lambda x: bool(PARTS.search(metric_of(x)))))      # a part of another level last
    out = []                                     # the stores taken in turn, so a cap cuts each store's least
    for i in range(max(map(len, per), default=0)):
        out += [lv[i] for lv in per if i < len(lv)]
    return out


def find_balance(project: Project, a: dict) -> dict:
    levels = expand(a.get("level"))
    if not levels:
        raise StoreError("give the level: a PromQL selector, e.g. rabbitmq_queue_messages{queue=\"email\"}, or a list of them")
    home, stores = _stores(project, a)
    if levels == ["all"]:
        levels = all_levels(stores, time.time() - WINDOW_MIN * 60, time.time())
        if not levels:
            raise StoreError("no store searched has a gauge that looks like an amount held: give the levels")
    later = levels[MAX_LEVELS:]
    levels = levels[:MAX_LEVELS]
    _budget(stores, len(levels))
    cfg = project.orchestra.config
    step = float(a.get("step_s") or cfg.discovery_step)
    end = time.time() - max([SETTLE, *(s.spec.settle for s in stores)])
    start = end - float(a.get("minutes") or WINDOW_MIN) * 60
    n = int((end - start) // step)
    end = start + n * step
    window = (start, end, step, n)
    if len(levels) == 1:
        got = _one(levels[0], home, stores, a, window)
        got.pop("_rows", None)
        return got
    briefs, rows = [], []
    for level in levels:
        try:
            got = _one(level, home, stores, a | {"stock": ""}, window)
        except StoreError as e:
            briefs.append({"level": level, "result": str(e)})
            continue
        rows.append((len(briefs), got.pop("_rows")))
        briefs.append(_brief(got))
    for (i, ra), (j, rb) in combinations(rows, 2):
        if _same_quantity(ra, rb):
            for x, y in ((i, j), (j, i)):
                briefs[x].setdefault("same_as", []).append(f"{briefs[y]['store']}:{briefs[y]['level']}")
    balanced = [b for b in briefs if b.get("accepted")]
    declare, taken = [], set()
    for b in sorted(balanced, key=lambda b: b["_score"], reverse=True):     # of one quantity read twice, the better fit
        me = f"{b['store']}:{b['level']}"
        if not taken & set(b.get("same_as", [])):
            declare += b["declare"]
            taken.add(me)
        else:
            b["not_declared"] = "the same quantity as a level declared here: declare only one"
    for b in briefs:
        b.pop("_score", None)
        b.pop("accepted", None)
    out: dict = {"ok": True, "window": f"{round((end - start) / 60)} min, {step:g} s steps",
                 "balanced": balanced,
                 "not_balanced": [f"{b['level']} ({b.get('store') or '?'}): {b.get('result') or b.get('fit')}"
                                  + (f"; one side: {'; '.join(b['one_sided'])}" if b.get("one_sided") else "")
                                  for b in briefs if b not in balanced],
                 "declare_all": declare}
    if later:
        out["not_searched_yet"] = later
        out["not_searched_note"] = f"at most {MAX_LEVELS} levels a call: call again with not_searched_yet"
    out["next"] = (f"{len(balanced)} level(s) balanced. Declare declare_all (with the [[series]] already there) in one "
                   "set_setting key series, and read each level and counter in it in its store's match. A level of "
                   "not_balanced with one side: declare the level and that side and name the residual")
    return out


def _same_quantity(a: list, b: list) -> bool:
    """Two levels that are one quantity read twice: where both have a point, their values agree
    within ``SAME_SCALE`` on average and move together (correlation ``SAME_CORR``). Their changes are
    not compared: a store written a minute late moves a minute after the other."""
    at = dict(b)
    pairs = [(v, at[t]) for t, v in a if t in at]
    if len(pairs) < 10:
        return False
    xs, ys = [p for p, _ in pairs], [q for _, q in pairs]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    if max(abs(mx), abs(my)) <= 0 or abs(mx - my) > SAME_SCALE * max(abs(mx), abs(my)):
        return False
    vx, vy = sum((x - mx) ** 2 for x in xs), sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return False
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (vx * vy) ** 0.5 >= SAME_CORR


def _brief(got: dict) -> dict:
    """One level's answer, short: its best balance and how it fits, or why there is none."""
    b = next(iter(got.get("balances") or []), None)
    out = {"level": got["level"], "store": got["store"]}
    if b:
        out["accepted"] = b["accepted"]
        out["_score"] = (b["accepted"], b["fit"]["r2"], b["fit"]["exact_share"])
        f = b["fit"]
        out["balance"] = b["balance"]
        out["fit"] = (f"{'accepted' if b['accepted'] else 'not accepted: ' + b.get('why_not', '')}; R² {f['r2']}, held out "
                      f"{f['held_out_r2']}, exact share {f['exact_share']}, unmeasured {b['unmeasured']:g}")
        if b.get("bursts"):
            out["bursts"] = b["bursts"]
        out["declare"] = b["declare"]
        if len(got["balances"]) > 1:
            out["also"] = [x["balance"] for x in got["balances"][1:]]
    else:
        out["result"] = got.get("result", "no balance")
        if got.get("one_sided"):
            out["one_sided"] = [f"{x['as']}: {x['store']}:{x['select']} (correlation {x['correlation']})" for x in got["one_sided"]]
    return out


def _one(level: str, home: Store, stores: list[Store], a: dict, window: tuple) -> dict:
    from orchestra.sd.flows_of import flows_of, name_unit
    from orchestra.sd.series import Declared, series_key
    from orchestra.sources import SourceError

    start, end, step, n = window
    # the level's store: the one given first, then the other searched stores, the first that has it
    tried = [home, *(st for st in stores if st is not home)] if a.get("url") or a.get("source") else stores
    lv_rows, lv_sets, errors = [], [], []
    for cand in tried:
        try:
            sets = cand.src.series([level], start, end, 2000) if metric_of(level) else []
            level = _production(level, sets)
            rows = cand.src.query_range(f"sum({level})", math.floor(start / step) * step, end, step)
        except SourceError as e:
            errors.append(f"{cand.name}: {e}")
            continue
        if rows and rows[0][1]:
            home, lv_rows, lv_sets = cand, rows, sets
            break
    if not lv_rows:
        where = ", ".join(c.name for c in tried)
        raise StoreError(f"{level} has no points in {where} over the window: check it with preview_query"
                         + (f" ({'; '.join(errors)})" if errors else ""))
    words = _level_tokens(level, lv_sets)
    unit = name_unit(metric_of(level) or "level", {})
    level_labels = {k: v for k, v in matchers(level).items() if k != "__name__" and k not in GENERIC}
    for k in {k for labels in lv_sets for k in labels} - GENERIC - {"__name__"}:
        vals = {labels.get(k) for labels in lv_sets}
        if len(vals) == 1 and None not in vals:
            level_labels.setdefault(k, vals.pop())

    notes: list[str] = []
    if len(lv_sets) > 1:
        notes.append(f"the level sums {len(lv_sets)} series; give a narrower selector to balance one queue, group or volume alone")
    metas = {}
    for st in stores:
        if not hasattr(st, "meta"):
            try:
                st.meta = st.src.metadata()
            except SourceError:
                st.meta = {}
        metas[st.name] = st.meta
    if a.get("candidates"):
        picked = []
        for sel in a["candidates"]:
            store = next((s for s in stores if str(sel).startswith(f"{s.name}:")), home)
            picked.append((0.0, str(sel).removeprefix(f"{store.name}:"), store))
    else:
        scored: list = []
        for store in stores:
            try:
                scored += _candidates(store, metric_of(level), words, unit, start, end, name_unit, metas[store.name])
            except SourceError as e:
                notes.append(f"{store.name}: its names could not be listed: {e}")
        scored.sort(key=lambda x: (-x[0], x[1]))
        picked = scored[:int(a.get("max_candidates") or MAX_CANDIDATES)]
    if not picked:
        raise StoreError(f"no counter in any store shares a word with {level} in its unit ({unit}); give candidates")

    def read(job):
        _, name, store = job
        bare = metric_of(name) or name
        try:
            sel, by = (name, []) if "{" in name else _plan(store, name, level_labels, words, start, end)
            key = (sel, tuple(by))
            if key not in store.reads:
                store.reads[key] = _read_counter(store, bare, sel, by, _kind(store, bare, metas[store.name]), start, end, step)
            return store.reads[key], None
        except SourceError as e:
            return [], f"{store.name}:{name}: {e}"
    dump, kinds, refs = [], {}, {}
    copies = []
    with ThreadPoolExecutor(4) as pool:
        for got, err in pool.map(read, picked):
            if err:
                notes.append(err)
            for name, labels, pts, decl, sel, by in got:
                key = series_key(name, labels)
                if decl.baseline and _copy_of(pts, lv_rows[0][1]):
                    copies.append(key)
                    continue
                dump.append((name, labels, pts))
                kinds[key] = decl
                refs[key] = (name.partition(":")[0], _with(sel, {k: v for k, v in labels.items()}), name.partition(":")[2], labels)
    lname = f"{home.name}:{metric_of(level) or 'level'}"
    found = flows_of((lname, {}, lv_rows[0][1]), dump, start, step, n, kinds | {lname: Declared("level")})
    stock = _stock_name(a.get("stock") or "", level)
    out: dict = {"ok": True, "level": level, "store": home.name, "unit": unit, "_rows": lv_rows[0][1],
                 "window": f"{round((end - start) / 60)} min to {round(time.time() - end)} s ago, {step:g} s steps",
                 "candidates_read": sorted(refs), "balances": []}
    if found.why:
        out["result"] = found.why
    if found.sides:
        out["one_sided"] = [{"series": k, "store": refs[k][0], "select": refs[k][1], "correlation": r,
                             "as": "inflow" if r > 0 else "outflow"} for k, r in found.sides if k in refs]
        out["one_sided_note"] = ("no balance, but these counters' changes move with the level's: each is likely one side of it "
                                 "(an inflow when positive, an outflow when negative), and what fills or drains the rest is not "
                                 "measured here: declare the level and that side, and name the rest as a residual")
    for b in found.balances:
        out["balances"].append(_shown(b, refs, home, level, stock))
    if copies:
        out["copies_of_the_level"] = copies
    if found.other_units:
        out["other_units"] = dict(list(found.other_units.items())[:10])
    if found.still:
        out["did_not_move"] = found.still[:10]
    if notes:
        out["notes"] = notes
    if any(not s.declared for s in stores):
        out["undeclared"] = ("a store given by url is called undeclared here: in the declarations, replace its name with the "
                             "name you declare it under in [[sources]]")
    out["next"] = ("declare an accepted balance (its declare list, added to the [[series]] already there) with set_setting key "
                   "series; one not accepted is a hypothesis: declare it only when its counters plainly measure what fills and "
                   "drains the level, and name what it leaves unmeasured as a residual")
    return out


def _shown(b, refs: dict, home: Store, level: str, stock: str) -> dict:
    ev = b.evidence
    terms = []
    for key, sign in b.terms:
        src, sel, metric, labels = refs.get(key, ("?", key, key, {}))
        terms.append({"series": key, "store": src, "select": sel, "role": "inflow" if sign > 0 else "outflow",
                      "counted": b.counted.get(key)})
    fit = {"r2": round(b.r2, 3), "exact_share": round(b.exact, 3), "held_out_r2": ev.get("held_out_r2"),
           "running_residual": ev.get("cum_residual_ratio"), "shift_null_p": ev.get("p_null"), "units": ev.get("units"),
           "on": b.on}
    shown = {"balance": " ".join(f"{'+' if t['role'] == 'inflow' else '−'}{t['series']}" for t in terms),
             "accepted": b.accepted, **({"why_not": b.why} if b.why else {}), "fit": fit, "terms": terms,
             "level_change": b.level_change, "unmeasured": b.residual}
    if b.bursts:
        x = b.bursts
        shown["bursts"] = (f"the level {'fell' if x['direction'] == 'falls' else 'rose' if x['direction'] == 'rises' else 'jumped'} "
                           f"in {x['units']} bursts (total {x['total']:g}, against a usual change of {x['usual_change']:g}), which "
                           "no counter here measures: an unmeasured "
                           f"{'outflow' if x['direction'] == 'falls' else 'inflow' if x['direction'] == 'rises' else 'flow'} "
                           "(a purge, retention, a top-up): name it as a residual; the balance is judged without them")
    declare = [{"source": home.name, "name": f"{stock}_level"[:60], "query": f"sum({level})", "kind": "level", "stock": stock,
                "role": "level"}]
    seen = {declare[0]["name"]}
    for t in terms:
        src, sel, metric, labels = refs.get(t["series"], ("?", t["series"], t["series"], {}))
        name, k = _decl_name(metric, labels), 2
        while name in seen:
            name, k = f"{_decl_name(metric, labels)[:54]}_{k}", k + 1
        seen.add(name)
        declare.append({"source": src, "name": name, "query": f"sum({sel})", "kind": "counter", "stock": stock, "role": t["role"]})
    shown["declare"] = declare
    return shown


COMMANDS = {"find_balance": find_balance}
