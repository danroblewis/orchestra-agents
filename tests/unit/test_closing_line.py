"""T2f s16: the "what changed since yesterday?" reply did not end on the model line (its last line
was a note on the tally). read_events with since answers ``closing_line``, the prompt says the
reply's final line is that line verbatim, and the host appends it to a reply of that turn that does
not end on it."""

import subprocess

import pytest
from conftest import FakeHarness, FakeSession, agents_project, supervisor_knows

from orchestra_agents import bus_reads, closing_line, modeling, protocol, tooling
from orchestra_agents.events import Events
from orchestra_agents.harness.base import TurnResult
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore

LINE = "model v5, changed by discovery at 2026-11-02 11:14:57Z; it has 3 stocks and 6 flows"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(protocol, "WAIT_S", 0.3)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


class Reading(FakeSession):
    """A turn that reads read_events with since (when ``reads``) and then replies ``reply``."""

    def __init__(self, spec, store, reply, reads):
        super().__init__(spec, None)
        self.store, self.reply, self.reads = store, reply, reads

    def prompt(self, text, timeout, on_event=None, idle_timeout=None):
        self.prompts.append(text)
        if self.reads:
            closing_line.record(self.store, LINE)
        on_event and on_event({"type": "assistant_end", "text": self.reply, "tool_calls": []})
        return TurnResult(text=self.reply)


class Harness(FakeHarness):
    def __init__(self, store, reply, reads=True):
        super().__init__()
        self.store, self.reply, self.reads = store, reply, reads

    def start(self, spec):
        s = Reading(spec, self.store, self.reply, self.reads)
        self.sessions.append(s)
        return s


def host_with(project, reply, reads=True):
    store = AgentStore(project)
    host = AgentHost(project, store, Events.for_project(project))
    host.harness = Harness(store, reply, reads)
    host._refresh_org(store.runtime()["agent_state"])
    return host


T2F = ("Items: SCRUM-31, SCRUM-32 and SCRUM-33 are Done.\n\nModel: " + LINE + "\n\n"
       "The notes above come from the window's tally, which counts all 202 events. I only read the first 100 to get the detail.")


def test_a_reply_that_does_not_end_on_the_closing_line_gets_it_appended(project):
    seen = []
    out = host_with(project, T2F).prompt_supervisor("What changed since yesterday?", seen.append)
    assert out["text"] == T2F + "\n\n" + LINE
    assert seen[-1] == {"type": "assistant_end", "text": LINE}


def test_a_reply_that_ends_on_it_or_did_not_read_it_is_left_alone(project):
    ends = "Items: three Done.\n\n**Model:** " + LINE + "."
    assert host_with(project, ends).prompt_supervisor("What changed since yesterday?", lambda ev: None)["text"] == ends
    assert host_with(project, T2F, reads=False).prompt_supervisor("hello", lambda ev: None)["text"] == T2F


def test_read_events_since_answers_the_closing_line_and_records_it_for_the_supervisor(project, monkeypatch):
    store = AgentStore(project)
    monkeypatch.setattr(bus_reads, "recent", lambda store, n=bus_reads.SCAN: [])
    monkeypatch.setattr(modeling, "model_line", lambda project, since=None: LINE)
    out = tooling._handle(project, store, "read_events", {"since": "1d"}, role="worker")
    assert out["closing_line"] == out["model"] == LINE and list(out).index("closing_line") > list(out).index("model")
    assert not store.agent_state("supervisor").get("closing_line")             # another agent's read is not the reply's
    tooling.handle(project, store, "read_events", {"since": "1d"}, role="supervisor")
    assert closing_line.due(store, 0.0) == LINE
    assert "closing_line" not in tooling.handle(project, store, "read_events", {"minutes": 60}, role="supervisor")


def test_the_prompt_says_the_final_line_is_the_closing_line_verbatim(project):
    text = " ".join(supervisor_knows(project).split())
    assert "its last line is the read's `closing_line`, the model line" in text
    assert "no rewording, no additions, no omissions, then nothing" in text
