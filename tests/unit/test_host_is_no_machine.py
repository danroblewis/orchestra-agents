"""The conformance test for the rule that a plugin's infrastructure is no machine (Orchestra's
AGENTS.md and PLUGINS.md; the owner, 2026-10-27): the agents' host is the shared runtime that runs
the configured agents, and it never announces itself, never publishes under its own name
(``agents``) and writes no metrics. Every event it sends is an agent's, every subscription is
announced by the agent that reacts, and the measurements reach the store through the plugin's
instrument (``agents.turns``)."""

import subprocess
import sys

import pytest
from conftest import FakeHarness, agents_project
from fake_tasks import FakeTasks

from orchestra_agents import cli, web
from orchestra_agents.events import HOST, Events
from orchestra_agents.host import AgentHost
from orchestra_agents.listening import listens_for
from orchestra_agents.org import built_in_agents
from orchestra_agents.probes import Prober, ProbeSpec
from orchestra_agents.store import AgentStore


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ORCHESTRA_SOURCE", raising=False)
    monkeypatch.delenv("ORCHESTRA_ROLE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def host_with(project, harness):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    host.harness = harness
    return host, store, events


def a_run(project, tmp_path):
    """The host through the paths that published as ``agents`` before: its start (with a turn the
    previous host left running, whose slot is gone), the residents coming up, a worker's turn on an
    assignment, a planner woken by attention, an ephemeral session's end and a probe that fails."""
    host, store, events = host_with(project, FakeHarness(reply="done"))
    store.set_session("worker-T-9", kind="worker", status="running", instance="worker/3", ticket_id="T-9")
    host.stop.set()
    host.run(once=True)
    (tmp_path / "w").mkdir()
    events.publish("task.assigned", {"assignment": "a1", "role": "worker", "ticket": "T-1", "kind": "work", "workspace": str(tmp_path / "w"),
                                     "branch": None, "lease_s": 180, "brief": {"id": "T-1", "title": "t", "description": "d"}},
                   source="worker", subject="T-1")
    host.runner.tick()
    host.runner.threads["a1"].join(20)
    events.publish("task.attention_needed", {"to": "coordinator", "reason": "T-1 is blocked", "ticket": "T-1"}, source="worker", subject="T-1")
    host._refresh_org(store.runtime()["agent_state"])
    host._wake_on_events()
    with FakeTasks(project):
        host._wake("coordinator", [w["reason"] for w in store.drain_wakeups("coordinator")])
    run_probe = Prober(project, store, events)
    run_probe._run(ProbeSpec(name="always_fails", command="false", every=60))
    return host, store, events


def test_the_host_never_announces_itself_nor_publishes_as_itself(project, tmp_path):
    _host, _store, events = a_run(project, tmp_path)
    evs = events.read(0)[0]
    assert {"session.interrupted", "session.started", "session.settled", "agent.said", "task.accepted", "probe.failed"} <= {e["type"] for e in evs}
    assert all(e["source"] for e in evs)
    assert not [e for e in evs if e["source"].partition("/")[0] == HOST], [e for e in evs if e["source"].partition("/")[0] == HOST]
    started = {e["source"] for e in evs if e["type"] == "orchestra.machine.started"}
    assert HOST not in started and {"supervisor", "coordinator", "worker/1"} <= started
    [cut] = [e for e in evs if e["type"] == "session.interrupted"]
    assert cut["source"] == "worker/3"                                     # the previous host's turn: the instance its record names
    [probe] = [e for e in evs if e["type"] == "probe.failed"]
    assert probe["source"] == "supervisor"                                 # a probe with no team's lead is the Supervisor's
    with pytest.raises(ValueError, match="no machine"):
        events.publish("x.happened", {}, source=HOST)
    with pytest.raises(ValueError, match="no machine"):
        events.publish("x.happened", {}, source=f"{HOST}/1")
    with pytest.raises(ValueError, match="no machine"):
        Events.for_project(project).publish("x.happened", {})            # the host has no source of its own


def test_each_subscription_is_announced_by_the_agent_that_reacts(project, tmp_path):
    _host, _store, events = a_run(project, tmp_path)
    said = {e["source"]: e["listens_for"] for e in events.read(0)[0] if e["type"] == "orchestra.machine.started"}
    # the Supervisor: its wake-ups, the answers to its own requests, its tickets' ends (tasks mode:
    # an approval ends a ticket), ticket.created, by which it remembers its own, and the verdicts
    # on its predictions
    assert said["supervisor"] == ["gate.landed", "orchestra.prediction.judged", "orchestra.process.gave_up", "request.done",
                                  "request.refused", "review.approved", "task.attention_needed", "ticket.created"]
    assert said["coordinator"] == ["request.done", "request.refused", "task.attention_needed"]
    # a per-ticket role asks nothing; a worker hears its predictions' verdicts and its ticket's end
    # (a predicted change announced when it lands)
    assert said["worker/1"] == ["changes.requested", "gate.landed", "orchestra.prediction.judged", "request.done",
                                "review.approved", "task.assigned", "task.released"]
    builtins = built_in_agents()
    assert "review.approved" not in listens_for(builtins["supervisor"], "code")              # in code mode the gate ends a ticket
    assert {d.id: d.listens_for for d in web.declared(project)}["supervisor"] == said["supervisor"]   # declared as announced


def test_the_measurements_reach_the_store_through_the_plugins_instrument(project, tmp_path, monkeypatch, capsys):
    from orchestra.metrics import for_project
    a_run(project, tmp_path)
    for name in ("agents_working", "agent_turns_started_total", "agent_turns_finished_total"):
        assert not for_project(project.orchestra).dump(name, {}), name                    # the host wrote none
    [c] = web.collectors(project)
    assert c.name == "turns" and c.kind == "collect" and c.target.endswith("-m orchestra_agents metrics")
    monkeypatch.chdir(project.repo_root)
    capsys.readouterr()                                                                     # the host's log so far
    assert cli.main(["metrics"]) == 0
    text = capsys.readouterr().out
    for role in ("coordinator", "reviewer", "supervisor", "worker"):
        assert f'agents_working{{role="{role}",machine="{role}"}} 0' in text               # every measured role, even at 0
    assert 'agent_turns_started_total{machine="worker",role="worker"} 1' in text
    assert 'agent_turns_finished_total{machine="worker",role="worker"} 1' in text
    assert 'agent_turns_finished_total{machine="coordinator",role="coordinator"} 1' in text
    out = subprocess.run([sys.executable, "-m", "orchestra_agents", "metrics"], cwd=project.repo_root, capture_output=True, text=True, check=True)
    assert out.stdout == text                                                               # what the instrument runs
