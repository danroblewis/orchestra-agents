"""The owner's chat messages, each shown from the moment the host accepts it, queued or being
answered, until the Supervisor's transcript holds it, across a refresh of the page."""

import subprocess
import threading
import time

import pytest
from conftest import FakeHarness, FakeSession, agents_project
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestra_agents import owner_messages, prompting, protocol, web
from orchestra_agents.events import Events
from orchestra_agents.harness.base import TurnResult
from orchestra_agents.host import AgentHost
from orchestra_agents.session_turns import owner_words
from orchestra_agents.store import AgentStore

CONTEXT = "[Orchestra: now 2026-11-04 09:00 CET (UTC+01:00), the zone the pages show.]\n\n[Orchestra: 3 events since your last turn: x=3]\n\nsome notes\n"
MULTI = "First line of my request.\n\nSecond paragraph, after a blank line.\n\n\n- a list item\n- another\n\nLast line."


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(protocol, "WAIT_S", 0.3)                 # no task system here: the snapshot gives up quickly
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def _said(text: str, at: float | None = None) -> dict:
    return {"role": "user", "text": prompting.chat_turn(CONTEXT, text), "tool_calls": [], "ts": at or time.time()}


def test_accepted_then_shown_then_gone_once_the_transcript_holds_it(project):
    store = AgentStore(project)
    e = owner_messages.accept(store, MULTI)
    assert [(p["text"], p["status"], p["place"]) for p in owner_messages.pending(store, [])] == [(MULTI, "waiting", 1)]
    owner_messages.answering(store, e["id"])
    assert owner_messages.pending(store, [])[0]["status"] == "answering"
    held = [_said(MULTI)]
    assert owner_messages.pending(store, held) == []              # the transcript shows it: the chat shows it there
    owner_messages.settle(store, e["id"], held)
    assert store.runtime()["owner_messages"] == []                # and the record lets it go


def test_the_same_words_twice_are_two_messages_and_an_old_one_is_no_match(project):
    store = AgentStore(project)
    old = [_said("yes", time.time() - 3600)]                      # an earlier "yes", answered long ago
    a = owner_messages.accept(store, "yes")
    b = owner_messages.accept(store, "yes")
    assert len(owner_messages.pending(store, old)) == 2
    assert [p["id"] for p in owner_messages.pending(store, old + [_said("yes")])] == [b["id"]]
    assert a["id"] != b["id"]


def test_a_turn_that_failed_before_the_transcript_took_it_says_so(project):
    store = AgentStore(project)
    e = owner_messages.accept(store, "hello")
    owner_messages.settle(store, e["id"], [], "the harness exited")
    [p] = owner_messages.pending(store, [])
    assert p["status"] == "failed" and p["error"] == "the harness exited"
    f = owner_messages.accept(store, "and this")
    owner_messages.interrupted(store)                             # the next host: what the last one held is failed, said why
    assert next(p for p in owner_messages.pending(store, []) if p["id"] == f["id"])["error"].startswith("the agents' host stopped")


class Blocking(FakeSession):
    """A Supervisor turn that waits for ``go`` and then answers."""
    def __init__(self, spec, go):
        super().__init__(spec, None)
        self.go = go
        self.started = threading.Event()

    def prompt(self, text, timeout, on_event=None, idle_timeout=None):
        self.prompts.append(text)
        self.started.set()
        self.go.wait(10)
        return TurnResult(text="answered")


class BlockingHarness(FakeHarness):
    def __init__(self):
        super().__init__()
        self.go = threading.Event()

    def start(self, spec):
        s = Blocking(spec, self.go)
        self.sessions.append(s)
        return s


def _host(project, harness):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    host.harness = harness
    host._refresh_org(store.runtime()["agent_state"])
    return host, store


def _wait(cond, s=10.0):
    end = time.time() + s
    while time.time() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_messages_sent_while_a_turn_runs_queue_in_order_and_a_refresh_mid_turn_lists_them(project):
    harness = BlockingHarness()
    host, store = _host(project, harness)
    app = FastAPI()
    app.include_router(web.router(project), prefix="/api/plugins/agents")
    client = TestClient(app)
    said: dict[str, list] = {}

    def send(text):
        said[text] = []
        threading.Thread(target=host.prompt_supervisor, args=(text, said[text].append), daemon=True).start()

    send(MULTI)
    _wait(lambda: harness.sessions and harness.sessions[0].started.is_set())
    assert said[MULTI][0]["type"] == "accepted"                   # the stream says so first
    send("second")
    _wait(lambda: len(store.runtime()["owner_messages"]) == 2)
    time.sleep(0.05)
    send("third")
    _wait(lambda: len(store.runtime()["owner_messages"]) == 3)

    # a page opened (or refreshed) now: the message being answered and the two queued behind it
    d = client.get("/api/plugins/agents/sessions/supervisor/transcript").json()
    assert [(p["text"], p["status"], p.get("place")) for p in d["pending"]] == [
        (MULTI, "answering", None), ("second", "waiting", 1), ("third", "waiting", 2)]

    harness.go.set()
    _wait(lambda: len(harness.sessions[0].prompts) == 3)
    prompts = harness.sessions[0].prompts                         # answered in the order accepted
    assert [owner_words(p)[1] for p in prompts] == [MULTI, "second", "third"]
    _wait(lambda: all(p["status"] == "answered" for p in store.runtime()["owner_messages"]))
