"""End-to-end with the fake Claude Code: the Supervisor's tools run through the real MCP server, so
the record of what it changed is made where a real session's calls are made (tooling.handle), and
its reply is checked against that record and the task system's answer. The fake replies as the
live test's Haiku did (2026-10-27): "I have not done anything" right after two commits, and two
DONE tickets called in flight. Orchestra corrects both, in the reply the human reads."""

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from conftest import agents_project
from fake_tasks import FakeTasks

from orchestra_agents.events import Events
from orchestra_agents.harness import read_transcript
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore

FAKE = Path(__file__).parent / "fake_claude.py"


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def chat(tmp_path, monkeypatch):
    """A project on the fake Claude Code with the task system's two DONE tickets; returns a function
    that sends the human's message with the Supervisor's scripted steps and gives the reply."""
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    fake = tmp_path / "claude"
    shutil.copy(FAKE, fake)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "supervisor.json"
    monkeypatch.setenv("CLAUDE_BINARY", str(fake))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("FAKE_CLAUDE_SUPERVISOR", str(script))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    project = agents_project(repo, harness="claude_code")
    toml = repo / "orchestra.toml"
    toml.write_text(toml.read_text() + '\n[project]\nbase_branch = "main"\nticket_prefix = "DEMO"\nwork = "code"\n\n[tasks]\nworkers = 1\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    project.reload_config()
    monkeypatch.setenv("ORCHESTRA_REPO", str(repo))
    events = Events.for_project(project)
    host = AgentHost(project, AgentStore(project, events), events)
    host._refresh_org(host.store.runtime()["agent_state"])
    with FakeTasks(project) as tasks:
        for n in (5, 6):
            tasks.tickets[f"DEMO-{n}"] = {"id": f"DEMO-{n}", "title": f"collectors {n}", "state": "DONE"}

        def say(message: str, steps: list[dict]) -> tuple[str, list[dict]]:
            script.write_text(json.dumps(steps))
            seen: list[dict] = []
            out = host.prompt_supervisor(message, seen.append)
            assert not out.get("error"), out
            return out["text"], seen
        try:
            yield say, repo, host
        finally:
            host.residents.close_all()


def test_a_commit_then_nothing_done_is_corrected(chat):
    say, repo, _host = chat
    text, seen = say("Two at once, and gate on pytest.", [
        {"tool": "set_capacity_limit", "args": {"workers": 2, "reason": "the owner wants two tickets at once"}},
        {"tool": "set_gate_unit", "args": {"command": "pytest -q", "reason": "branches must pass the tests to land"}},
        {"reply": "I have not done anything."}])
    shas = git(repo, "log", "--format=%h", "-2").split()
    assert text.startswith("I have not done anything.\n\n[Correction from Orchestra")
    assert all(f"committed {sha} to orchestra.toml" in text for sha in shas)
    assert [e["text"] for e in seen if e["type"] == "assistant_end"][-1].startswith("[Correction from Orchestra")
    assert git(repo, "log", "-2", "--format=%s").count("Supervisor:") == 2     # the two commits are the Supervisor's


def test_a_tool_answer_says_what_changed(chat):
    say, repo, _host = chat
    text, _ = say("Two at once.", [{"tool": "set_capacity_limit", "args": {"workers": 2, "reason": "the owner wants two at once"}}])
    sha = git(repo, "rev-parse", "--short", "HEAD").strip()
    assert f'"done": "Changed: committed {sha} to orchestra.toml on the base branch: at most 2 workers at once ([tasks])."' in text
    assert "Correction" not in text                                            # the fake's reply is the tool's answer: true


def test_done_tickets_called_in_flight_are_shown_done(chat):
    say, _repo, host = chat
    text, _ = say("Keep it busy overnight.", [{"reply": "DEMO-5 and DEMO-6 are already in flight."}])
    assert text.endswith("the task system says, now: DEMO-5 is DONE, DEMO-6 is DONE.")
    say("Anything else?", [{"reply": "Nothing else."}])
    sess = host.store.find_session("supervisor")
    prompts = [m["text"] for m in read_transcript(sess) if m["role"] == "user"]
    assert "DEMO-5 DONE, DEMO-6 DONE" in prompts[-1]                           # the next turn is told where they are
