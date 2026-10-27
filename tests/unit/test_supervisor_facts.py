"""The Supervisor is given facts it cannot misremember, and a reply that contradicts them is
corrected by Orchestra (ledger.py, facts.py, truth.py). From the live test of 2026-10-27: right
after two commits of orchestra.toml it said it had not done anything, and it called DEMO-5 and
DEMO-6 "in flight" when both were DONE, though its prompt told it to say only what its tools did
and answered (37a026d)."""

import subprocess

import pytest
from conftest import FakeHarness, FakeSession, agents_project
from fake_tasks import FakeTasks

from orchestra_agents import ledger, tooling, truth
from orchestra_agents.events import Events
from orchestra_agents.harness.base import TurnResult
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore

TOML_TAIL = """
[project]
base_branch = "main"
ticket_prefix = "DEMO"
work = "code"

[tasks]
workers = 1
reviewers = 1
"""
LIVE_IN_FLIGHT = ("- **Real work:** DEMO-5 (four local collectors) and DEMO-6 (three external collectors, now Boulder for "
                  "weather) are already in flight.")


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
    toml = repo / "orchestra.toml"
    toml.write_text(toml.read_text() + TOML_TAIL)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    return p


@pytest.fixture
def tasks(project):
    """The task system, with the live test's two tickets DONE."""
    with FakeTasks(project) as t:
        for n in (5, 6):
            t.tickets[f"DEMO-{n}"] = {"id": f"DEMO-{n}", "title": f"collectors {n}", "state": "DONE"}
        yield t


class Scripted(FakeSession):
    """One reply per turn; a reply that is a function is called with the prompt during the turn
    (its tool calls, made as the MCP server makes them, after a tool_start as a harness sends it)
    and returns the reply."""

    def __init__(self, spec, replies):
        super().__init__(spec, None)
        self.replies = replies

    def prompt(self, text, timeout, on_event=None, idle_timeout=None):
        self.prompts.append(text)
        reply = self.replies.pop(0) if self.replies else "ok"
        if callable(reply):
            on_event and on_event({"type": "tool_start", "name": "set_capacity_limit", "args": {}})
            reply = reply(text)
        on_event and on_event({"type": "assistant_end", "text": reply, "tool_calls": []})
        return TurnResult(text=reply)


class ScriptedHarness(FakeHarness):
    def __init__(self, replies):
        super().__init__()
        self.replies = list(replies)

    def start(self, spec):
        s = Scripted(spec, self.replies)
        self.sessions.append(s)
        return s


def host_with(project, replies):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    host.harness = ScriptedHarness(replies)
    host._refresh_org(store.runtime()["agent_state"])
    return host, store


def as_supervisor(project, command, args):
    """A tool call as the Supervisor's MCP server makes it."""
    return tooling.handle(project, AgentStore(project, Events.for_project(project, source="supervisor")), command, args, role="supervisor")


def test_a_change_is_answered_with_what_changed_and_recorded(project):
    out = as_supervisor(project, "set_capacity_limit", {"workers": 2, "reason": "two tickets at once"})
    sha = git(project.repo_root, "rev-parse", "--short", "HEAD").strip()
    assert out["commit"] == sha
    assert out["done"] == f"Changed: committed {sha} to orchestra.toml on the base branch: at most 2 workers at once ([tasks])."
    store = AgentStore(project)
    assert ledger.since(store, "supervisor", 0) == [out["done"][len("Changed: "):-1]]
    again = as_supervisor(project, "set_capacity_limit", {"workers": 2, "reason": "the same again, to be sure"})   # already so: nothing changed
    assert again["commit"] is None and "done" not in again
    assert as_supervisor(project, "read_config", {}).get("done") is None                             # a read is not a change
    assert len(ledger.since(store, "supervisor", 0)) == 1


def test_the_live_claims_are_found_and_honest_ones_pass():
    for said in ("I have not done anything.", "I haven't done anything yet.", "Nothing has been changed.", "I've made no changes",
                 "No changes were made to the setup."):
        assert truth.denies_doing(said), said
    assert truth.denies_doing("I made two changes, both committed.") is None
    assert truth.denies_doing("I haven't created these tickets, and I'd rather not.") is None     # a true refusal of one thing
    assert truth.correction("I have not done anything.", [], {}) is None                          # true when the record is empty

    done = {"DEMO-5": "DONE", "DEMO-6": "DONE"}
    assert truth.misstated(LIVE_IN_FLIGHT, done) == done
    assert truth.misstated("DEMO-5 is DONE and DEMO-6 has landed too.", done) == {}
    assert truth.misstated("- **DEMO-7** (repo sizes): DONE and running.\n- **DEMO-8**: still in review.",
                           {"DEMO-7": "DONE", "DEMO-8": "IN_REVIEW"}) == {}
    assert truth.misstated("DEMO-8 has landed on main.", {"DEMO-8": "IN_REVIEW"}) == {"DEMO-8": "IN_REVIEW"}


def test_a_nothing_done_reply_after_two_commits_is_corrected(project, tasks):
    def two_commits(prompt):
        as_supervisor(project, "set_capacity_limit", {"workers": 2, "reason": "more tickets at once"})
        as_supervisor(project, "set_gate_unit", {"command": "pytest -q", "reason": "branches must pass the tests"})
        return "I have not done anything. Tell me what you want changed."
    host, store = host_with(project, [two_commits])
    seen = []
    out = host.prompt_supervisor("raise the workers to two and gate on pytest", seen.append)
    shas = git(project.repo_root, "log", "--format=%h", "-2").split()
    fix = out["text"].split("\n\n", 1)[1]
    assert out["text"].startswith("I have not done anything.")
    assert fix.startswith("[Correction from Orchestra") and 'says "I have not done anything."' in fix
    assert all(f"committed {sha} to orchestra.toml" in fix for sha in shas)
    assert "at most 2 workers" in fix and "the gate's test unit: pytest -q" in fix
    assert [e["text"] for e in seen if e["type"] == "assistant_end"][-1] == fix                # on the live chat
    [mark] = [m for m in store.find_session("supervisor")["markers"] if m["type"] == "agent.corrected"]
    assert mark["text"] == fix                                                                    # and on its transcript

    # the next turn is told what its tools changed, whatever it remembers
    host.harness.replies.append("Two commits: the workers and the gate.")
    host.prompt_supervisor("what did you change?", lambda ev: None)
    nxt = host.harness.sessions[0].prompts[-1]
    assert "your tools' changes since your previous turn began" in nxt and all(f"committed {sha}" in nxt for sha in shas)


def test_an_honest_reply_after_commits_is_left_alone(project, tasks):
    def commit(prompt):
        as_supervisor(project, "set_capacity_limit", {"workers": 3, "reason": "more tickets at once"})
        return "I raised the workers to three (one commit of orchestra.toml)."
    host, store = host_with(project, [commit])
    out = host.prompt_supervisor("three workers", lambda ev: None)
    assert out["text"] == "I raised the workers to three (one commit of orchestra.toml)."
    assert not [m for m in store.find_session("supervisor")["markers"] if m["type"] == "agent.corrected"]


def test_a_ticket_called_in_flight_is_shown_in_its_true_state(project, tasks):
    host, store = host_with(project, ["DEMO-5 and DEMO-6 cover the measurements.", LIVE_IN_FLIGHT, "ok"])
    host.prompt_supervisor("measure the disk and the weather", lambda ev: None)

    # the wake-up's digest lists only live tickets; the facts in front of it give the ones the Supervisor named
    host._wake("supervisor", ["scheduled check-in"])
    woke = host.harness.sessions[0].prompts[-1]
    assert "Tickets (id, state, priority, milestone, title):" in woke and "- DEMO-5" not in woke
    assert "these tickets as the task system has them now" in woke and "DEMO-5 DONE, DEMO-6 DONE" in woke
    left = store.find_session("supervisor")["last_text"]           # the message the human finds waiting
    assert left.startswith(LIVE_IN_FLIGHT)
    assert "the task system says, now: DEMO-5 is DONE, DEMO-6 is DONE." in left

    # the human's message names a ticket: its state is in front of the turn, read just now
    tasks.tickets["DEMO-7"] = {"id": "DEMO-7", "title": "t", "state": "IN_PROGRESS"}
    host.prompt_supervisor("how is DEMO-7 doing?", lambda ev: None)
    assert "DEMO-7 IN_PROGRESS" in host.harness.sessions[0].prompts[-1]
