"""The Supervisor's review round and its flow-keeping wake-ups, in the host: a round runs on its
schedule, costs no turn when nothing new is off, wakes the Supervisor once for what it finds, stops
waking it past the day's cap; Orchestra's statements that a stock stalled or a machine went quiet
wake it through its keep_flow duty; its overload signals are measured, served by the instrument,
and a crossing writes a split proposal into its spec and tells it to tell the owner. Its model is
its spec's when the harness takes the name."""

import subprocess
import time

import pytest
from conftest import FakeHarness, agents_project

from orchestra_agents import budget, load, measures, split_proposal, supervisor_spec
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.machine_spec import load as load_spec
from orchestra_agents.review import MARK, initiative
from orchestra_agents.store import AgentStore

HOST = "orchestra-host"


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    supervisor_spec.ensure(p)
    return p


@pytest.fixture
def host(project):
    store, events = AgentStore(project), Events.for_project(project)
    h = AgentHost(project, store, events)
    h.harness = FakeHarness(reply="ok")
    h._refresh_org(store.runtime()["agent_state"])
    return h


def wakeups(h, who="supervisor"):
    return [w["reason"] for w in h.store.runtime()["wakeups"] if w["target"] == who]


def proposal_done(h):
    """The split proposal for the signals crossed now is already written (the Supervisor's tool count
    crosses ~30 from the start), so a round is about the flow alone."""
    h.store.set_agent_state("supervisor", split_proposal={"at": time.time(), "signals": list(load.THRESHOLDS)})


def test_a_round_runs_on_its_schedule_and_costs_no_turn_when_nothing_is_off(host):
    proposal_done(host)
    t0 = host.review.started
    assert host.review.tick(t0 + 60) is None                                   # not due before [review] every (30m)
    assert host.store.agent_state("supervisor")["next_review_at"] == pytest.approx(t0 + 1800)
    got = host.review.tick(t0 + 1801)
    assert got["woke"] is False and got["findings"] == [] and wakeups(host) == []
    rec = host.store.agent_state("supervisor")
    assert rec["review"]["at"] == t0 + 1801 and rec["next_review_at"] == pytest.approx(t0 + 1801 + 1800)
    assert rec["load"]["tools"] > 30 and rec["load"]["prompt_tokens"] > 1000           # its own signals, measured each round
    assert host.review.tick(t0 + 1900) is None


def test_a_stall_found_wakes_it_once_and_the_cap_stops_its_initiative(host):
    proposal_done(host)
    host.events.publish("orchestra.model.stalled", {"kind": "stock", "id": "review_queue", "since": time.time() - 900, "level": 9,
                                                    "rose_by": 6, "outflows": [{"flow": "reviewed", "rate": 0, "usual": 2}]},
                        source=HOST, subject="stock:review_queue")
    t0 = host.review.started
    got = host.review.tick(t0 + 1801)
    assert got["woke"] and got["new"] == 1
    [reason] = wakeups(host)
    assert reason.startswith(MARK) and "stock review_queue stalled" in reason and "rose by 6" in reason
    host.store.drain_wakeups("supervisor")
    assert host.review.tick(t0 + 3602)["woke"] is False                        # the same stall: no second turn for it
    host.events.publish("orchestra.machine.quiet", {"machine": "baker", "instance": "baker/1", "last_seen": time.time() - 600,
                                                    "usual_gap": 60}, source=HOST, subject="baker")
    host.project.config.review_usd_per_day = 1.0
    budget.record(host.store, 1.25, initiative=True)
    got = host.review.tick(t0 + 5403)
    assert got["woke"] is False and "review_usd_per_day" in got["skipped"] and wakeups(host) == []


def test_a_stalled_stock_on_the_bus_wakes_it_through_keep_flow_within_the_cap(host):
    host.events.publish("orchestra.model.stalled", {"kind": "stock", "id": "orders", "since": time.time()}, source=HOST,
                        subject="stock:orders")
    host._wake_on_events()
    [reason] = wakeups(host)
    assert reason.startswith("event orchestra.model.stalled") and "subject=stock:orders" in reason
    assert initiative([reason], host._duty_types())                            # its duty's, so the day's cap applies
    host.store.drain_wakeups("supervisor")
    host.project.config.review_turns_per_day = 1
    budget.record(host.store, None, initiative=True)
    host.events.publish("orchestra.machine.quiet", {"machine": "baker"}, source=HOST, subject="baker")
    host.events.publish("task.attention_needed", {"to": "supervisor", "reason": "stuck"}, source="worker")
    host._wake_on_events()
    [left] = wakeups(host)                                                     # past the cap only the fixed core's wake-ups pass
    assert left.startswith("event task.attention_needed")


def test_crossed_signals_write_a_split_proposal_once_and_tell_it_to_tell_the_owner(host):
    t0 = host.review.started
    got = host.review.tick(t0 + 1801)
    assert got["woke"] and "tools" in got["crossed"]
    prop = got["proposal"]
    text = (host.project.repo_root / prop["path"]).read_text()
    assert text.startswith("# Should the Supervisor be two machines?") and "| tools |" in text and "keep_flow" in text
    assert git(host.project.repo_root, "log", "-1", "--format=%s").startswith("Supervisor: a split proposal")
    [reason] = wakeups(host)
    assert "split proposal was written" in reason and "split nothing" in reason
    host.store.drain_wakeups("supervisor")
    assert host.review.tick(t0 + 3602)["woke"] is False                        # once a week for the same signals
    series = measures.exposition(host.store.runtime(), ["supervisor"])
    assert 'agent_load{machine="supervisor",signal="tools"}' in series


def test_cohesion_clusters_duties_that_share_wake_ups_or_tools(project):
    spec = load_spec(project, "supervisor")
    from orchestra_agents.machine_spec import Duty
    spec.duties += [Duty(name="watch_gate", listens=["gate.rejected"], tools=["pipeline"]),
                    Duty(name="retry_ci", listens=["gate.rejected:x"], tools=["pipeline"]),
                    Duty(name="morning_note", tools=["context"])]
    groups = split_proposal.clusters(spec)
    assert sorted(map(sorted, groups)) == [["keep_flow", "retry_ci", "watch_gate"], ["morning_note"], ["review_round"]]


def test_the_supervisor_runs_on_its_spec_s_model_when_the_harness_takes_it(host):
    assert host.model_of("supervisor") is None                                # the fake harness takes no names
    host.harness.model_for = lambda wanted: wanted                            # a harness that does
    assert host.model_of("supervisor") == "sonnet" and host.model_of("coordinator") == host.cfg.model
    from orchestra_agents.harness.claude_code import ClaudeCodeHarness
    assert ClaudeCodeHarness().model_for("sonnet") == "sonnet" and ClaudeCodeHarness().model_for("gpt-5") is None
