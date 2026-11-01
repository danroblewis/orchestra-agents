"""A chat that reaches the host before its first tick has refreshed the organisation still opens
the Supervisor with its tools: the session stays resident, so opening it with none would leave the
Supervisor without tools until it is rotated."""

import subprocess

from conftest import FakeHarness, agents_project

from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.org import built_in_agents
from orchestra_agents.store import AgentStore


def test_a_session_opened_before_the_first_refresh_has_its_roles_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    project = agents_project(repo)
    events = Events.for_project(project)
    host = AgentHost(project, AgentStore(project, events), events)
    host.harness = FakeHarness(reply="done")
    assert host.role_defs == {}
    session = host._session_for(built_in_agents()["supervisor"], "supervisor")
    assert {"design", "work"} <= set(session.spec.tool_groups)
