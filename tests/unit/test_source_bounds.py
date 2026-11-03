"""Every agents tool that lists, counts or reads another store's series stays inside the source's
bound: its ``match`` (the owner's scope) and its ``max_series`` (run 4: a shared Prometheus with two
namespaces, listed whole twice and read across both). A store with no bound is never asked for
every metric name."""

import json
import subprocess
import time
from types import SimpleNamespace

import pytest
from conftest import agents_project
from fake_store import FakeStore

from orchestra_agents import source_bounds, tooling
from orchestra_agents.design import begin_turn
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore, StoreError

OURS = 'namespace="hackriff"'


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def shared():
    """One store, two teams: hackriff's queue and its counters, and another namespace with the same
    names and more of its own."""
    now, step = time.time(), 15.0
    n = int(75 * 60 / step)
    t = [now - 75 * 60 + step * i for i in range(n)]
    dump = []
    for ns, rate in (("hackriff", 3), ("other", 7)):
        enq = [float(rate * i + (i % 5)) for i in range(n)]
        proc = [float(rate * i - (i % 3)) for i in range(n)]
        dump += [("analyze_jobs_enqueued_total", {"namespace": ns, "queue": "analyze"}, list(zip(t, enq))),
                 ("analyze_jobs_processed_total", {"namespace": ns, "queue": "analyze"}, list(zip(t, proc))),
                 ("queue_depth", {"namespace": ns, "queue": "analyze"}, [(x, 10 + e - p) for x, e, p in zip(t, enq, proc)])]
    dump += [(f"other_only_{k}", {"namespace": "other", "pod": f"p{j}"}, [(x, 1.0) for x in t]) for k in range(5) for j in range(3)]
    s = FakeStore(dump)
    yield s
    s.close()


def _project(tmp_path, monkeypatch, url: str, match: list[str] | None):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    entry = {"name": "prom", "url": url, "max_series": 200, **({"match": match} if match else {})}
    toml = repo / "orchestra.toml"
    toml.write_text(toml.read_text() + "\n[[sources]]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in entry.items()))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    store = AgentStore(p, Events.for_project(p, source="supervisor"))
    begin_turn(store, "Read our store: only our namespace hackriff, at most 200 series.")   # the owner's limits (run 6)
    return p, store


def call(project, store, tool, **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role="supervisor")


def _outside(requests) -> list:
    """The requests that asked the store for anything outside hackriff's namespace."""
    out = []
    for path, form in requests:
        if path in ("series", "values"):
            if not form.get("match[]") or not all(OURS in m for m in form["match[]"]):
                out.append((path, form.get("match[]")))
        elif path == "query_range" and OURS not in form["query"][0]:
            out.append((path, form["query"][0]))
        if path == "series" and form.get("limit") and int(form["limit"][0]) > 201:      # names are capped by max_names
            out.append((path, f"limit {form['limit'][0]}"))
    return out


def test_the_tools_stay_inside_the_sources_match_on_a_store_of_two_namespaces(tmp_path, monkeypatch, shared):
    project, store = _project(tmp_path, monkeypatch, shared.url, [f"{{{OURS}}}"])
    names = call(project, store, "source_schema", source="prom")
    assert [n.split(" [")[0] for n in names["names"]] == ["analyze_jobs_enqueued_total", "analyze_jobs_processed_total",
                                                         "queue_depth"]            # not the other team's
    detail = call(project, store, "source_schema", source="prom", match=['{__name__=~".+"}'])
    assert detail["series_read"] == 3                                               # one series each: hackriff's alone
    got = call(project, store, "find_balance", level="all")
    assert got["ok"] and got["level"] == 'queue_depth{queue="analyze"}', got
    assert got["candidates_read"] == ["prom:analyze_jobs_enqueued_total", "prom:analyze_jobs_processed_total"]
    selects = [t["select"] for b in got["balances"] for t in b["terms"]] + [x["select"] for x in got.get("one_sided") or []]
    assert selects and all(OURS in x for x in selects)              # what it shows and declares reads the namespace too
    assert all(OURS in d["query"] for b in got["balances"] for d in b["declare"])
    preview = call(project, store, "preview_query", source="prom", query=f'sum(queue_depth{{{OURS}}})', minutes=10, step_s=60)
    assert preview["lines"]
    with pytest.raises(StoreError, match="outside prom's bound"):
        call(project, store, "preview_query", source="prom", query='sum(queue_depth{queue="analyze"})')
    assert shared.requests and _outside(shared.requests) == []


def test_a_store_with_no_bound_is_never_asked_for_every_metric_name(tmp_path, monkeypatch, shared):
    project, store = _project(tmp_path, monkeypatch, shared.url, None)
    with pytest.raises(StoreError, match="no bound .* never done"):
        call(project, store, "source_schema", source="prom")
    with pytest.raises(StoreError, match="no bound"):
        call(project, store, "source_schema", source="prom", match=['{__name__=~".+"}'])
    with pytest.raises(StoreError, match="no bound"):
        call(project, store, "find_balance", level="all")
    assert not [r for r in shared.requests if r[0] == "values"]          # no listing of every name was sent
    narrowed = call(project, store, "source_schema", source="prom", match=[f'{{{OURS}}}'])   # a match of its own bounds it
    assert narrowed["series_read"] == 3


def test_a_selector_is_anded_with_the_bound():
    w = source_bounds.within
    assert w("queue_depth", '{namespace="h"}') == 'queue_depth{namespace="h"}'
    assert w('queue_depth{queue="a"}', '{namespace="h"}') == 'queue_depth{namespace="h",queue="a"}'
    assert w('{__name__=~"x.*"}', '{namespace="h"}') == '{namespace="h",__name__=~"x.*"}'
    assert w('queue_depth{namespace="h"}', '{namespace="h"}') == 'queue_depth{namespace="h"}'
    assert w("queue_depth", '{__name__=~".+"}') == "queue_depth"                 # the default bounds nothing
    assert source_bounds.expr(['{a="1"}', '{b="2"}'], lambda s: f"increase({s}[60s])", "x") == \
        '(increase(x{a="1"}[60s])) or (increase(x{b="2"}[60s]))'
    assert source_bounds.holds(['{namespace="h"}'], 'sum(rate(x{namespace="h"}[1m]))')
    assert not source_bounds.holds(['{namespace="h"}'], "sum(rate(x[1m]))")


def _bare(requests) -> list:
    """Bootstrap run 5's rule: a metadata or label-names call names a metric or a selector, never bare."""
    return [(path, form) for path, form in requests
            if (path == "metadata" and not form.get("metric")) or (path in ("labels", "values") and not form.get("match[]"))]


@pytest.fixture
def many():
    """One store, two namespaces, with more of hackriff's names than Orchestra asks metadata for in one
    call (its FEW_NAMES): run 5's ``source_schema`` read the whole store's metadata, 43 KB, that way."""
    now, step = time.time(), 15.0
    t = [now - 30 * 60 + step * i for i in range(int(30 * 60 / step))]
    dump = [("queue_depth", {"namespace": ns, "queue": "analyze"}, [(x, 5.0) for x in t]) for ns in ("hackriff", "other")]
    dump += [(f"hk_part_{k}_total", {"namespace": "hackriff"}, [(x, float(i)) for i, x in enumerate(t)]) for k in range(14)]
    dump += [(f"other_part_{k}", {"namespace": "other"}, [(x, 1.0) for x in t]) for k in range(6)]
    s = FakeStore(dump)
    yield s
    s.close()


def test_no_tool_asks_the_store_for_every_metrics_metadata(tmp_path, monkeypatch, many):
    project, store = _project(tmp_path, monkeypatch, many.url, [f"{{{OURS}}}"])
    names = call(project, store, "source_schema", source="prom")
    assert len(names["names"]) == 15 and not any(n.startswith("other_") for n in names["names"])
    call(project, store, "source_schema", source="prom", match=['{__name__=~"hk_part_.*"}'])
    with pytest.raises(StoreError, match="no counter .* shares a word"):    # what it asked on the way is what is judged
        call(project, store, "find_balance", level="all")
    asked = [form["metric"] for path, form in many.requests if path == "metadata" and form.get("metric")]
    assert asked and _bare(many.requests) == [] and _outside(many.requests) == []
    from orchestra_agents.source_bounds import METADATA_NAMES

    calls: list[list[str]] = []
    src = SimpleNamespace(metadata=lambda names: calls.append(list(names)) or {})
    source_bounds.metadata(src, [f"m{i}" for i in range(100)])
    assert len(calls) == METADATA_NAMES and all(len(c) == 1 for c in calls)     # name by name, capped


def test_a_store_given_by_url_that_a_source_declares_is_read_within_its_match(tmp_path, monkeypatch, shared):
    """Run 5: 27 queries on the store, given by url with a ``job`` bound, named no namespace. The
    configured source's match holds for every read of its url, whatever bound is passed."""
    project, store = _project(tmp_path, monkeypatch, shared.url + "/", [f"{{{OURS}}}"])
    got = call(project, store, "source_schema", url=shared.url, bound='{queue="analyze"}')
    assert [n.split(" [")[0] for n in got["names"]] == ["analyze_jobs_enqueued_total", "analyze_jobs_processed_total",
                                                       "queue_depth"]
    assert got["bound"] == [f'{{{OURS},queue="analyze"}}']
    call(project, store, "source_schema", url=shared.url)               # no bound given: the declared match is the bound
    call(project, store, "find_balance", url=shared.url, bound='{queue="analyze"}', level="all")
    with pytest.raises(StoreError, match="outside"):
        call(project, store, "preview_query", url=shared.url, query='sum(queue_depth{queue="analyze"})')
    assert shared.requests and _outside(shared.requests) == [] and _bare(shared.requests) == []
    assert source_bounds.trial_bound([], "http://x", '{job="a"}') == ['{job="a"}']      # no source declares it: as given
