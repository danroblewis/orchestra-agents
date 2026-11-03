"""Nothing is read from another store before the owner has given its limits (bootstrap run 6: told
only "read our Prometheus", the Supervisor read it at once within a ``job`` scope and a series cap
it chose itself, before the owner said "only our namespace hackriff, at most 200 series")."""

import json
import subprocess
import time

import pytest
from conftest import agents_project
from fake_store import FakeStore

from orchestra_agents import source_limits, tooling
from orchestra_agents.design import begin_turn
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore, StoreError

RUN6_1_1 = ("We use ... our own Prometheus at http://127.0.0.1:8904, which already scrapes the services (hk-api, "
            "hk-stream, hk-store), so read it rather than instrumenting the code. Proceed as you think best.")
RUN6_6_0 = ("Last, our Prometheus at http://127.0.0.1:8904: only our namespace, hackriff, at most 200 series, every 5 "
            "minutes. Never ask it for the list of every metric.")


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def prom():
    now, step = time.time(), 15.0
    t = [now - 3600 + step * i for i in range(240)]
    s = FakeStore([("queue_depth", {"namespace": "hackriff", "job": "hk-api", "queue": "writes"}, [(x, 5.0) for x in t])])
    yield s
    s.close()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    return p, AgentStore(p, Events.for_project(p, source="supervisor"))


def call(project, store, tool, **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role="supervisor")


def test_the_owners_words_give_limits_only_with_a_scope_and_a_cap_or_the_defaults():
    assert not source_limits.gave_limits([RUN6_1_1])
    assert not source_limits.gave_limits(["Proceed as you think best."])
    assert not source_limits.gave_limits(["only our namespace, hackriff"])          # a scope, no cap
    assert source_limits.gave_limits([RUN6_6_0])
    assert source_limits.gave_limits(['read {job="hk-api"}, at most 50 series'])
    assert source_limits.gave_limits(["Use the defaults for Prometheus."])


def test_a_store_is_read_only_after_the_owner_gave_its_limits(setup, prom):
    project, store = setup
    begin_turn(store, RUN6_1_1)
    assert call(project, store, "check_sources", url=prom.url)["ok"]          # whether it answers: no series read
    sent = len(prom.requests)
    scope = ['{job=~"hk-api|hk-stream|hk-store"}']                             # a scope of its own, from the code
    for tool, args in (("source_schema", {"bound": scope}), ("preview_query", {"query": 'sum(queue_depth{job="hk-api"})'}),
                       ("infer_source", {"match": scope}), ("find_balance", {"level": "queue_depth", "bound": scope})):
        with pytest.raises(StoreError, match="has not given this store's limits") as e:
            call(project, store, tool, url=prom.url, **args)
        assert "at most how many series" in str(e.value) and "Nothing was read" in str(e.value)
    entry = json.dumps([{"name": "hackriff_prom", "url": prom.url, "match": scope, "max_series": 200}])
    with pytest.raises(StoreError, match="declaring a match for it is refused"):  # nor declared as if it were theirs
        call(project, store, "set_setting", key="sources", value=entry, target="repo", reason="read the services")
    assert len(prom.requests) == sent                                          # nothing was asked of the store

    begin_turn(store, RUN6_6_0)                                                # the owner's limits arrive
    entry = json.dumps([{"name": "hackriff_prom", "url": prom.url, "match": ['{namespace="hackriff"}'], "max_series": 200}])
    assert call(project, store, "set_setting", key="sources", value=entry, target="repo", reason="the owner's scope")["commit"]
    got = call(project, store, "source_schema", source="hackriff_prom")
    assert got["names"][0].startswith("queue_depth")


def test_a_declared_match_is_the_owners_limits(setup, prom):
    project, store = setup
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text() + f'\n[[sources]]\nname = "prom"\nurl = "{prom.url}"\nmatch = [\'{{namespace="hackriff"}}\']\nmax_series = 200\n')
    project.reload_config()
    begin_turn(store, "What is in the queue?")
    assert call(project, store, "source_schema", source="prom")["names"]
    assert call(project, store, "source_schema", url=prom.url)["names"]       # the same store by its url
