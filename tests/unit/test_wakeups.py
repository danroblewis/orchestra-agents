"""Who the host wakes on what: the built-in planners on the task system's attention events
addressed to them, custom agents on what they subscribe to, never a worker or reviewer (their
events are assignments, answered by the runner); which roles are the agents' at all; and the
runner's side of an assignment: a slot per instance, a release that is not a failure, a finished
ticket archived, and a report for every open assignment at shutdown."""

import subprocess
import threading
import time

import pytest
from conftest import FakeHarness, agents_project

from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.org_store import all_agents
from orchestra_agents.store import AgentStore
from orchestra_agents.web import declared


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def host_with(project, harness):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    host.harness = harness
    return host, store, events


def wait_for(events, type_, n=1, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        got = [e for e in events.read(0)[0] if e["type"] == type_]
        if len(got) >= n:
            return got
        time.sleep(0.05)
    raise AssertionError(f"no {type_} within {timeout}s")


def assigned(events, aid, ticket, workspace, role="worker"):
    events.publish("task.assigned", {"assignment": aid, "role": role, "ticket": ticket, "kind": "work", "workspace": str(workspace),
                                     "branch": None, "lease_s": 180, "brief": {"id": ticket, "title": "t", "description": "d"}}, subject=ticket)


def test_attention_wakes_the_planner_it_names(project):
    host, store, events = host_with(project, FakeHarness(reply="ok"))
    host._refresh_org(store.runtime()["agent_state"])
    events.publish("task.attention_needed", {"to": "coordinator", "reason": "DEMO-3 is blocked", "ticket": "DEMO-3"}, subject="DEMO-3")
    events.publish("task.assigned", {"assignment": "a1", "role": "reviewer", "ticket": "DEMO-1"}, subject="DEMO-1")
    events.publish("ticket.transitioned", {"ticket": "DEMO-1", "from": "OPEN", "to": "IN_PROGRESS"}, subject="DEMO-1")
    host._wake_on_events()
    pending = store.runtime()["wakeups"]
    assert [w["target"] for w in pending] == ["coordinator"]
    assert "task.attention_needed" in pending[0]["reason"] and "DEMO-3 is blocked" in pending[0]["reason"]
    events.publish("task.attention_needed", {"to": "supervisor", "reason": "the base branch is broken"})
    host._wake_on_events()
    assert [w["target"] for w in store.runtime()["wakeups"]] == ["coordinator", "supervisor"]
    assert set(host.residents.procs) == {"supervisor", "coordinator"}         # the built-in residents came up with the org


def test_a_role_another_machine_fills_is_not_an_agent_here(project):
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text().replace('serves = ["worker", "reviewer"]', 'serves = ["reviewer"]'))
    project.reload_config()
    assert project.config.serves == ["reviewer"]
    assert set(all_agents(project)) == {"supervisor", "coordinator", "reviewer"}
    assert [d.id for d in declared(project)] == ["agents", "supervisor", "coordinator", "reviewer"]
    assert {d.kind for d in declared(project)} == {"program", "agent"} and declared(project)[1].mode == "continuous"


def test_a_finished_ticket_has_its_sessions_archived(project):
    host, store, events = host_with(project, FakeHarness(reply="ok"))
    store.set_session("worker-T-1", kind="worker", status="idle", ticket_id="T-1")
    store.set_session("reviewer-T-1", kind="reviewer", status="idle", ticket_id="T-1")
    store.set_session("worker-T-2", kind="worker", status="idle", ticket_id="T-2")
    events.publish("ticket.transitioned", {"ticket": "T-1", "from": "IN_REVIEW", "to": "DONE"}, subject="T-1")
    host.runner.tick()
    rt = store.runtime()
    assert set(rt["archived_sessions"]) == {"worker-T-1", "reviewer-T-1"} and set(rt["sessions"]) == {"worker-T-2"}
    assert rt["archived_sessions"]["worker-T-1"]["status"] == "archived"


def test_two_offers_in_one_tick_take_different_slots(project, tmp_path):
    host, _store, events = host_with(project, FakeHarness(reply="done"))
    for n in (1, 2):
        (tmp_path / f"w{n}").mkdir()
        assigned(events, f"a{n}", f"T-{n}", tmp_path / f"w{n}")
    host.runner.tick()
    for th in list(host.runner.threads.values()):
        th.join(20)
    accepted = [e for e in events.read(0)[0] if e["type"] == "task.accepted"]
    assert {e["instance"] for e in accepted} == {"worker/1", "worker/2"}
    assert all(e["source"] == e["instance"] for e in accepted)
    reported = [e for e in events.read(0)[0] if e["type"] == "task.reported"]
    assert {e["assignment"] for e in reported} == {"a1", "a2"} and all(e["report"] is None for e in reported)   # the fake never reports


def test_a_released_assignment_is_an_interruption_not_a_failure(project, tmp_path):
    host, _store, events = host_with(project, FakeHarness())         # turns block until the session is killed
    (tmp_path / "w").mkdir()
    assigned(events, "a1", "T-1", tmp_path / "w")
    host.runner.tick()
    wait_for(events, "task.accepted")
    events.publish("task.released", {"assignment": "a1", "ticket": "T-1", "role": "worker", "reason": "operator stopped it"}, subject="T-1")
    host.runner.tick()
    host.runner.threads["a1"].join(20)
    evs = events.read(0)[0]
    cut = [e for e in evs if e["type"] == "turn.interrupted"]
    assert cut and cut[-1]["reason"] == "released by the task system: operator stopped it" and cut[-1]["session"] == "worker-T-1"
    assert not [e for e in evs if e["type"] in ("task.reported", "turn.error")]
    assert host.runner.released == {}


def test_shutdown_reports_every_open_assignment_as_failed(project, tmp_path):
    host, store, events = host_with(project, FakeHarness())
    (tmp_path / "w").mkdir()
    th = threading.Thread(target=host.run, daemon=True)
    th.start()
    assigned(events, "a1", "T-1", tmp_path / "w")
    wait_for(events, "task.accepted", timeout=20)
    host.stop.set()
    th.join(30)
    assert not th.is_alive()
    reported = [e for e in events.read(0)[0] if e["type"] == "task.reported"]      # on the bus before run() returned
    assert len(reported) == 1 and reported[0]["assignment"] == "a1" and reported[0]["failed"] is True
    assert store.runtime().get("control") is None
    assert all(s["status"] == "idle" and s["pid"] is None for s in store.sessions())
