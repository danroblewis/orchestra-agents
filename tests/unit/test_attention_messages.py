"""The banner's "the Supervisor wrote to you" is only what the human can see and answer: a reply
in the Supervisor's transcript written for them since they last read the chat (attention.py,
GET /attention). The owner's complaint (2026-10-28): the banner kept asking for input while
the chat it opened showed nothing new. A turn an event woke that says nothing to the human, a
turn still running and a message already read never call; a message the transcript does not
hold is returned for the chat to show."""

import json
import subprocess

import pytest
from conftest import agents_project
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestra_agents import web
from orchestra_agents.attention import quiet, unread
from orchestra_agents.store import AgentStore

T0 = 1_700_000_000.0
WAKE = ("[Orchestra: a wake-up, not a message from the human, who is not present for this turn.]\n\n"
        "# Wake-up for the supervisor\n\n## Why you were woken\n- event orchestra.prediction.judged: your prediction p1 was judged\n\n"
        "## Project snapshot\n")
CHAT = "[Orchestra: events since your last turn]\n- x\n\n[The human's message]\nhow is it going?"


def u(text, t):
    return {"role": "user", "text": text, "tool_calls": [], "ts": T0 + t}


def a(text, t):
    return {"role": "assistant", "text": text, "tool_calls": [], "ts": T0 + t}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo, harness="dummy")


def supervisor(project, msgs, seen=0.0, **rec):
    store = AgentStore(project)
    f = project.state_dir / "supervisor.jsonl"
    f.write_text("".join(json.dumps(m) + "\n" for m in msgs), encoding="utf-8")
    store.set_session("supervisor", kind="supervisor", harness="dummy", session_file=str(f), status=rec.pop("status", "idle"), **rec)
    if seen:
        store._mutate_runtime(lambda d: d.__setitem__("supervisor_seen_at", T0 + seen))
    return store


def test_a_wake_up_that_says_nothing_to_the_human_does_not_call(project):
    store = supervisor(project, [u(WAKE, 0), a("Nothing to do.", 2), u(WAKE, 5), a("", 6)],
                       last_text="", settled_at=T0 + 6)
    assert unread(store.runtime()) is None


def test_a_wake_up_that_tells_the_human_something_calls_and_says_why_it_was_woken(project):
    store = supervisor(project, [u(CHAT, 0), a("All fine.", 1), u(WAKE, 10), a("The prediction held: tests pass faster.", 12),
                                 u(WAKE, 20), a("Noted.", 21)], seen=5, last_text="Noted.", settled_at=T0 + 21)
    m = unread(store.runtime())
    assert m["text"] == "The prediction held: tests pass faster." and m["index"] == 3 and m["in_chat"] and m["count"] == 1
    assert m["woken_by"] == ["event orchestra.prediction.judged: your prediction p1 was judged"]


def test_a_reply_the_human_has_read_does_not_call_again(project):
    store = supervisor(project, [u(CHAT, 0), a("All fine.", 1)], seen=2, last_text="All fine.", settled_at=T0 + 1.5)
    assert unread(store.runtime()) is None


def test_the_turn_still_running_is_not_a_message_yet(project):
    store = supervisor(project, [u(CHAT, 0), a("Working on it; first the tickets.", 1)], status="running", started_at=T0)
    assert unread(store.runtime()) is None


def test_what_the_transcript_does_not_hold_is_returned_for_the_chat_to_show(project):
    store = supervisor(project, [u(CHAT, 0), a("All fine.", 1)], seen=2,
                       last_text="DEMO-3 is blocked on a decision only you can make: which database?", settled_at=T0 + 9)
    m = unread(store.runtime())
    assert m["in_chat"] is False and m["index"] is None and m["text"].startswith("DEMO-3 is blocked")


def test_the_endpoint_answers_with_it_and_reading_clears_it(project):
    supervisor(project, [u(WAKE, 0), a("DEMO-2 landed: the todo list now saves.", 1)], last_text="x", settled_at=T0 + 1)
    app = FastAPI()
    app.include_router(web.router(project), prefix="/api/plugins/agents")
    c = TestClient(app)
    d = c.get("/api/plugins/agents/attention").json()
    assert d["count"] == 1 and d["message"]["text"] == "DEMO-2 landed: the todo list now saves."
    c.post("/api/plugins/agents/attention/seen")
    assert c.get("/api/plugins/agents/attention").json()["message"] is None


@pytest.mark.parametrize("text, says_nothing", [
    ("Nothing to do.", True), ("Noted.", True), ("Acknowledged", True), ("No action needed: the coordinator unblocked DEMO-3.", True),
    ("Noted. Which database do you want?", False), ("Noted: DEMO-3 needs your decision", False), ("DEMO-2 landed.", False),
])
def test_only_a_bare_acknowledgement_says_nothing(text, says_nothing):
    assert quiet(text) is says_nothing
