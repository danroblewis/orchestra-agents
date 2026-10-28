"""A project nobody has configured: the host still runs (no [agents] needed), on Claude Code with
Haiku when claude is found and on the dummy harness otherwise, and wakes the Supervisor once to set
the project up (the owner, 2026-10-28)."""

import subprocess

import pytest
from conftest import FakeHarness, agents_project
from fake_tasks import FakeTasks

from orchestra_agents import first_run
from orchestra_agents.config import Config
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore


def test_not_set_up_means_no_project_no_instruments_and_no_setup_that_ran():
    assert first_run.needs_setup({}, {}, "claude_code")
    assert not first_run.needs_setup({"project": {"work": "tasks"}}, {}, "claude_code")
    assert not first_run.needs_setup({"collect": [{"name": "x", "command": "true"}]}, {}, "claude_code")
    assert not first_run.needs_setup({"sources": [{"name": "w"}]}, {}, "claude_code")
    assert not first_run.needs_setup({}, {"setup": {"ran_at": 1, "harness": "claude_code"}}, "claude_code")
    assert not first_run.needs_setup({}, {"setup": {"ran_at": 1, "harness": "dummy"}}, "dummy")
    assert first_run.needs_setup({}, {"setup": {"ran_at": 1, "harness": "dummy"}}, "claude_code")   # a dummy could not set it up


def test_the_default_harness_is_claude_code_with_haiku_when_claude_is_found(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    claude.write_text("#!/bin/sh\n")
    claude.chmod(0o755)
    monkeypatch.setenv("CLAUDE_BINARY", str(claude))
    c = Config.from_raw({})
    assert (c.harness, c.model, c.enabled) == ("claude_code", "haiku", True) and "Claude Code with Haiku" in c.harness_note
    monkeypatch.setenv("CLAUDE_BINARY", str(tmp_path / "none"))
    c = Config.from_raw({})
    assert (c.harness, c.model) == ("dummy", None) and "no claude command" in c.harness_note
    c = Config.from_raw({"agents": {"harness": "pi", "model": "x"}})
    assert (c.harness, c.model, c.harness_note) == ("pi", "x", None)
    assert not Config.from_raw({"agents": {"enabled": False}}).enabled


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A repository with Orchestra's starter orchestra.toml and an [agents] section, never set up."""
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo, set_up=False)


def run_once(project):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    host.harness = harness = FakeHarness(reply="The repository has no tests yet; tell me which model to use.")
    with FakeTasks(project):
        host._refresh_org(store.runtime()["agent_state"])
        host.first_run.offer()                       # what run() does once, at start
        host.tick()
        for th in list(host.agent_threads.values()):
            th.join(20)
    return host, store, harness


def test_the_host_wakes_the_supervisor_once_to_set_the_project_up(fresh):
    _host, store, harness = run_once(fresh)
    [sup] = [s for s in harness.sessions if s.spec.role == "supervisor"]
    [prompt] = sup.prompts
    assert first_run.MARK in prompt and "list_guides" in prompt and "your reply is left for the human" in prompt.lower()
    assert sup.spec.read_only and [s.spec.read_only for s in harness.sessions if s.spec.role == "coordinator"] == [True]
    assert store.agent_state("supervisor")["setup"]["harness"] == fresh.config.harness
    _host, _store, again = run_once(fresh)                # it ran: a later host wakes nobody for it
    assert not [p for s in again.sessions for p in s.prompts]


def test_a_configured_project_is_not_set_up_again(fresh):
    toml = fresh.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text() + '\n[project]\nwork = "tasks"\n')
    fresh.reload_config()
    _host, store, harness = run_once(fresh)
    assert not [p for s in harness.sessions for p in s.prompts] and "setup" not in store.agent_state("supervisor")
