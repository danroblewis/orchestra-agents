"""The runtime record: nothing is written when nothing changed, and a session keeps a bounded
list of the host's markers."""

import subprocess

import pytest
from conftest import agents_project

from orchestra_agents.store import MARKERS_KEPT, AgentStore


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def test_draining_no_wakeups_writes_nothing(project):
    store = AgentStore(project)
    store.add_wakeup("supervisor", "look")
    before = project.runtime_file.stat().st_mtime_ns
    assert store.drain_wakeups("coordinator") == []
    assert project.runtime_file.stat().st_mtime_ns == before                 # the host asks every second for every agent
    assert [w["reason"] for w in store.drain_wakeups("supervisor")] == ["look"]
    assert project.runtime_file.stat().st_mtime_ns != before


def test_markers_are_kept_on_the_session_and_bounded(project):
    store = AgentStore(project)
    store.add_marker("nobody", {"type": "turn.error"})                        # no such session: nothing to attach to
    assert store.find_session("nobody") is None
    store.set_session("supervisor", kind="supervisor")
    for n in range(MARKERS_KEPT + 5):
        store.add_marker("supervisor", {"type": "session.started", "n": n})
    kept = store.find_session("supervisor")["markers"]
    assert len(kept) == MARKERS_KEPT and kept[-1]["n"] == MARKERS_KEPT + 4
    store.purge_ticket_sessions("T-1")                                        # no such ticket: no error, nothing archived
    assert store.runtime()["archived_sessions"] == {}
