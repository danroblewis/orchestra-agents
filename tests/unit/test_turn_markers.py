"""A turn Orchestra cut off is an event with what the agent was doing, so the transcript can say so."""

import subprocess
import time
from types import SimpleNamespace

import pytest
from conftest import FakeHarness, agents_project

from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo, provider="p", model="m")


def test_timed_out_turn_is_recorded_with_what_the_agent_was_doing(project):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    now_t = time.time()
    store.set_session("retro-1", kind="retrospective", status="running", phase="tool", tool_name="bash",
                      phase_since=now_t - 130, last_output_at=now_t - 12, started_at=now_t - 900)
    res = lambda **kw: SimpleNamespace(**{"timed_out": False, "error": None, "text": "", "stopped_by": None, **kw})
    host.turns.note("retro-1", res(timed_out=True, text="Let me read the transcript."), "retrospective")
    host.turns.note("retro-1", res(text="fine"), "retrospective")   # a normal end: nothing
    host.turns.note("retro-2", res(error="pi exited 1"), "retrospective")
    evs = [e for e in events.read(0)[0] if e["type"] in ("session.timed_out", "session.error")]
    assert [e["type"] for e in evs] == ["session.timed_out", "session.error"]
    t = evs[0]
    assert t["session"] == "retro-1" and t["agent"] == "retrospective" and t["limit"] == project.config.turn_timeout
    assert t["doing"] == "running `bash`" and 125 <= t["phase_for"] <= 135 and 10 <= t["silent_for"] <= 15
    assert t["last_text"] == "Let me read the transcript."
    assert evs[1]["error"] == "pi exited 1"
    assert [m["type"] for m in store.find_session("retro-1")["markers"]] == ["session.timed_out"]   # kept on the record for the transcript
    host.turns.note("retro-2", res(error="pi exited"), "retrospective", released=lambda: "operator stopped it")
    last = events.read(0)[0][-1]
    assert last["type"] == "session.interrupted" and last["reason"] == "released by the task system: operator stopped it"
    host.stop.set()                                                                      # a harness exit during shutdown is not an error
    host.turns.note("retro-3", res(error="pi exited"), "retrospective")
    last = events.read(0)[0][-1]
    assert last["type"] == "session.interrupted" and last["session"] == "retro-3"


def test_shutdown_marks_every_running_turn(project):
    store, events = AgentStore(project), Events.for_project(project)
    store.set_session("worker-T-1", kind="worker", status="running", ticket_id="T-1")
    store.set_session("po", kind="po", status="idle")
    host = AgentHost(project, store, events)
    host.harness = FakeHarness(reply="ok")
    host.stop.set()
    host.run(once=True)
    cut = [e for e in events.read(0)[0] if e["type"] == "session.interrupted"]
    assert [(e["session"], e["ticket"]) for e in cut] == [("worker-T-1", "T-1")]     # found running at startup: a crash
    assert "crashed" in cut[0]["reason"]
    store.set_session("po", status="running")
    host._shutdown_markers()
    cut = [e for e in events.read(0)[0] if e["type"] == "session.interrupted"]
    assert cut[-1]["session"] == "po" and "shut down" in cut[-1]["reason"]           # cut off by a clean stop
