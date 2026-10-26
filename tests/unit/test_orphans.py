"""No orphans: a turn's process that outlived its host is stopped when the next host starts, and
a second host for the same project does not start at all."""

import subprocess
import time

import psutil
import pytest
from conftest import FakeHarness, agents_project

from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore


def test_an_orphaned_turn_is_stopped():
    p = subprocess.Popen(["sleep", "60"], start_new_session=True)       # as a harness starts a turn
    AgentHost._kill_orphan(p.pid, "worker-T-1")
    p.wait(timeout=5)
    assert p.returncode is not None


def test_a_process_that_is_not_a_group_leader_is_stopped_too():
    p = subprocess.Popen(["sleep", "60"])                              # in this process's group, as an older Pi session was
    AgentHost._kill_orphan(p.pid, "worker-T-1")
    p.wait(timeout=5)
    assert p.returncode is not None


def test_a_pid_that_is_gone_is_left_alone():
    p = subprocess.Popen(["true"], start_new_session=True)
    p.wait()
    time.sleep(0.1)
    AgentHost._kill_orphan(p.pid, "worker-T-1")                              # no error
    assert not psutil.pid_exists(p.pid) or psutil.Process(p.pid).status() == psutil.STATUS_ZOMBIE


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def test_recover_stops_the_turns_process_and_marks_the_rest_idle(project):
    """A session's pid is the turn's own process, recorded from the harness's turn_start; a
    session running with no pid (a harness that starts a process per turn, between prompts)
    is only marked idle."""
    store, events = AgentStore(project), Events.for_project(project)
    turn = subprocess.Popen(["sleep", "60"], start_new_session=True)
    store.set_session("worker-T-1", kind="worker", status="running", ticket_id="T-1", pid=turn.pid)
    store.set_session("coordinator", kind="coordinator", status="running", pid=None)
    host = AgentHost(project, store, events)
    host.harness = FakeHarness(reply="ok")
    host.recover()
    turn.wait(timeout=5)
    assert turn.returncode is not None
    assert {s["id"]: (s["status"], s["pid"]) for s in store.sessions()} == {"worker-T-1": ("idle", None), "coordinator": ("idle", None)}
    cut = [e for e in events.read(0)[0] if e["type"] == "turn.interrupted"]
    assert {e["session"] for e in cut} == {"worker-T-1", "coordinator"}


def test_a_second_host_for_the_project_exits_without_touching_anything(project):
    store, events = AgentStore(project), Events.for_project(project)
    other = subprocess.Popen(["sleep", "60"], start_new_session=True)     # stands for the host already running
    try:
        store.set_control(12345, other.pid)
        store.set_session("worker-T-1", kind="worker", status="running", ticket_id="T-1", pid=other.pid)
        host = AgentHost(project, store, events)
        host.harness = FakeHarness(reply="ok")
        assert host.run(once=True) == 1
        assert other.poll() is None                                       # not killed as an orphan
        assert store.find_session("worker-T-1")["status"] == "running" and store.runtime()["control"]["pid"] == other.pid
        assert not [e for e in events.read(0)[0] if e["type"] in ("turn.interrupted", "orchestra.machine.started")]
    finally:
        other.kill()
