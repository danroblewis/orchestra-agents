"""The explore loop as the Supervisor's standing duty: its schedule ([agents] explore_every), when a
round costs no turn, the tools over the core's survey, run record and experiment log, the standing
rule held in code (narrow, split, kinds, names on a wake-up; never a wider match nor explore), and
its line in the digest."""

import subprocess
import time

import pytest
from conftest import FakeHarness, agents_project
from orchestra.sd.store import ModelStore

from orchestra_agents import budget, explore_round, explore_tools, source_limits
from orchestra_agents.config import Config, ConfigError
from orchestra_agents.design import begin_turn
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.review import initiative
from orchestra_agents.store import AgentStore, StoreError

SOURCE = '\n[[sources]]\nname = "metrics"\nurl = "http://127.0.0.1:9/"\nmatch = [\'{namespace="team"}\']\n' \
         'explore = [\'{namespace="team"}\', \'{__name__=~"queue_.*", env="prod"}\']\n'
ENTRY = {"name": "metrics", "url": "http://127.0.0.1:9/", "match": ['{namespace="team"}'],
         "explore": ['{namespace="team"}', '{__name__=~"queue_.*", env="prod"}']}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    p = agents_project(repo)
    (repo / "orchestra.toml").write_text((repo / "orchestra.toml").read_text() + SOURCE)
    p.reload_config()
    return p


@pytest.fixture
def host(project):
    store = AgentStore(project)
    h = AgentHost(project, store, Events.for_project(project))
    h.harness = FakeHarness(reply="ok")
    h._refresh_org(store.runtime()["agent_state"])
    return h


def wakeups(h):
    return [w["reason"] for w in h.store.runtime()["wakeups"] if w["target"] == "supervisor"]


def test_explore_every_is_an_interval_off_at_zero():
    assert Config.from_raw({"agents": {"harness": "dummy"}}).explore_every == 21600.0   # 6h since 2026-11-04 (fewer wake-ups)
    assert Config.from_raw({"agents": {"harness": "dummy", "explore_every": "30m"}}).explore_every == 1800.0
    assert Config.from_raw({"agents": {"harness": "dummy", "explore_every": 0}}).explore_every == 0.0
    with pytest.raises(ConfigError, match="explore_every"):
        Config.from_raw({"agents": {"harness": "dummy", "explore_every": "soon"}})


def test_a_round_is_due_on_schedule_and_off_without_a_store_or_at_zero(host, project):
    now = time.time()
    assert host.explore.tick(now) is None                                       # not due yet
    assert host.store.agent_state("supervisor")["next_explore_at"] == pytest.approx(host.explore.started + 21600)
    rec = host.explore.tick(host.explore.started + 21601)
    assert rec["woke"] and rec["sources"] == ["metrics"]
    [why] = wakeups(host)
    assert why.startswith(explore_round.MARK) and '{__name__=~"queue_.*", env="prod"}' in why and "explore_source" in why
    assert initiative([why], set())                                             # the day's cap for its initiative holds it
    host.cfg.explore_every = 0
    assert host.explore.tick(now + 99_999) is None
    host.cfg.explore_every = 60                                                 # never under the floor
    assert host.explore.every() == explore_round.FLOOR_S


def test_no_turn_while_the_last_experiment_waits_for_discovery_or_past_the_cap(host, project):
    ms = ModelStore.for_state(project.orchestra.state_dir)
    e = ms.explorer().add({"source": "metrics", "split": ["queue"]}, "each queue its own stock", by="supervisor")
    rec = host.explore.run(explore_round._sources(project), time.time(), 3600)
    assert not rec["woke"] and e["id"] in rec["skipped"] and not wakeups(host)
    ms.runs().append({"id": "r1", "kind": "discovery", "ended": time.time() + 1, "result": {"stocks": 2}})
    host.cfg.review_usd_per_day = 0                                             # no initiative today
    rec = host.explore.run(explore_round._sources(project), time.time() + 2, 3600)
    assert not rec["woke"] and "review_usd_per_day" in rec["skipped"]
    host.cfg.review_usd_per_day = 2.0
    rec = host.explore.run(explore_round._sources(project), time.time() + 3, 3600)
    [why] = wakeups(host)
    assert rec["woke"] and f"First judge experiment {e['id']} (1 discovery run(s)" in why
    line = explore_round.digest_line(project, host.store, 0)
    assert line.startswith("Exploring: 3 round(s), 1 with a turn; 1 experiment(s) recorded.") and "to be judged" in line


def test_the_tools_read_and_write_through_the_core(project, monkeypatch):
    got = explore_tools.handle(project, "log_experiment", {"change": '{"source": "metrics", "names": {"*_x": "{queue} x"}}',
                                                           "expect": "plain names"}, "supervisor")
    xid = got["experiment"]["id"]
    assert got["experiment"]["by"] == "supervisor"
    with pytest.raises(StoreError, match="only the supervisor"):
        explore_tools.handle(project, "log_experiment", {"change": {"a": 1}, "expect": "x"}, "worker")
    with pytest.raises(StoreError, match="no discovery run has ended"):
        explore_tools.handle(project, "judge_experiment", {"id": xid, "verdict": "kept", "why": "x"}, "supervisor")
    assert explore_tools.handle(project, "read_experiments", {}, "worker")["pending"] == [xid]
    ModelStore.for_state(project.orchestra.state_dir).runs().append(
        {"id": "r1", "kind": "discovery", "trigger": "schedule", "ended": time.time() + 1, "outcome": "saved v1",
         "result": {"stocks": 2, "flows": 3}, "cost": {"metrics": {"requests": 40}}})
    [run] = explore_tools.handle(project, "read_discovery_runs", {}, "supervisor")["runs"]
    assert (run["id"], run["stocks"], run["requests"], run["trigger"]) == ("r1", 2, {"metrics": 40}, "schedule")
    time.sleep(1.1)
    judged = explore_tools.handle(project, "judge_experiment", {"id": xid, "verdict": "kept", "why": "names only"}, "supervisor")
    assert judged["experiment"]["verdict"] == "kept"
    with pytest.raises(StoreError, match="no usable source"):
        explore_tools.handle(project, "explore_source", {"source": "nope"}, "supervisor")
    from orchestra.sources import exploring
    monkeypatch.setattr(exploring, "explore", lambda state, spec, **kw: {
        "source": spec.name, "explore": spec.explore, "match": spec.match, "families": [
            {"family": "queue_depth", "in_match": False, "kind": "gauge", "reads_as": "level", "labels": {"queue": {"values": 3, "more": False}},
             "metrics": ["queue_depth"], "under": "x"}], "not_surveyed": [], "notes": [], "requests": 7, "limit": kw.get("limit") or 10})
    out = explore_tools.handle(project, "explore_source", {"source": "metrics", "limit": 3}, "supervisor")
    assert out["ok"] and out["requests"] == 7 and out["families"][0]["reads_as"] == "level" and "queue_depth" in "\n".join(out["lines"])


def test_on_a_wake_up_the_standing_rule_holds(project):
    store = AgentStore(project)
    begin_turn(store, None)                                                     # a wake-up: no owner's message
    narrow = {**ENTRY, "match": ['{namespace="team", service="api"}'], "split": ["queue"], "kinds": {"x": "flow"}}
    source_limits.check_scopes(project, store, [narrow])                        # narrow, split, kinds: the rule
    source_limits.check_declared(project, store, [narrow])                      # a narrowed match needs no new limits
    with pytest.raises(StoreError, match="match widens"):
        source_limits.check_scopes(project, store, [{**ENTRY, "match": [*ENTRY["match"], '{env="prod"}']}])
    with pytest.raises(StoreError, match="explore .* is the owner's alone"):
        source_limits.check_scopes(project, store, [{**ENTRY, "explore": ['{env="prod"}']}])
    begin_turn(store, 'yes, also read {env="prod"}, at most 200 series')       # the owner's turn: their words carry it
    source_limits.check_scopes(project, store, [{**ENTRY, "match": [*ENTRY["match"], '{env="prod"}']}])


def test_the_digest_carries_the_explore_line():
    from orchestra_agents import since_digest
    text = since_digest.digest([], 0, time.time(), "UTC", explore="Exploring: 1 round(s), 1 with a turn.")
    assert "Exploring: 1 round(s), 1 with a turn." in text
    assert budget.MACHINE == explore_round.MACHINE
