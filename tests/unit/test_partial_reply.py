"""While a reply streams, the session record carries the text so far, so the UI can show
what an agent is typing during a long generation."""

import subprocess

import pytest
from conftest import agents_project

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


def test_partial_reply_is_recorded_and_cleared(project):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    store.set_session("supervisor", kind="supervisor", status="running")
    fn = host.turns.on_event("supervisor")
    fn({"type": "assistant_start"})
    fn({"type": "token", "kind": "thinking", "delta": "let me see"})
    fn({"type": "token", "kind": "text", "delta": "I'll create "})
    host.turns._last_token_write["supervisor"] = 0              # force the periodic write
    fn({"type": "token", "kind": "text", "delta": "the ticket now."})
    s = store.find_session("supervisor")
    assert s["partial_text"] == "I'll create the ticket now." and s["thinking_chars"] == len("let me see")
    fn({"type": "assistant_end", "text": "I'll create the ticket now."})
    s = store.find_session("supervisor")
    assert s["partial_text"] == "" and s["thinking_chars"] == 0 and s["last_text"].endswith("now.")
