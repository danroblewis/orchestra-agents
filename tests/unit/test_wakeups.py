"""Who the host wakes on what: the built-in planners on the task system's attention events
addressed to them, custom agents on what they subscribe to, never a worker or reviewer (their
events are assignments, answered by the runner); and which roles are the agents' at all."""

import subprocess

import pytest

from orchestra_agents.config import init_project
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.org import all_agents
from orchestra_agents.store import AgentStore
from orchestra_agents.web import declared


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return init_project(repo)


def test_attention_wakes_the_planner_it_names(project):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    host._refresh_org()
    events.publish("task.attention_needed", {"to": "coordinator", "reason": "DEMO-3 is blocked", "ticket": "DEMO-3"}, subject="DEMO-3")
    events.publish("task.assigned", {"assignment": "a1", "role": "reviewer", "ticket": "DEMO-1"}, subject="DEMO-1")
    events.publish("ticket.transitioned", {"ticket": "DEMO-1", "from": "OPEN", "to": "IN_PROGRESS"}, subject="DEMO-1")
    host._wake_on_events()
    pending = store.runtime()["wakeups"]
    assert [w["target"] for w in pending] == ["coordinator"]
    assert "task.attention_needed" in pending[0]["reason"] and "DEMO-3 is blocked" in pending[0]["reason"]
    events.publish("task.attention_needed", {"to": "supervisor", "reason": "the base branch is broken"})
    host._wake_on_events()
    assert [w["target"] for w in store.runtime()["wakeups"]] == ["coordinator", "supervisor"]


def test_a_role_another_machine_fills_is_not_an_agent_here(project):
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text().replace('serves = ["worker", "reviewer"]', 'serves = ["reviewer"]'))
    project.reload_config()
    assert project.config.serves == ["reviewer"]
    assert set(all_agents(project)) == {"supervisor", "coordinator", "reviewer"}
    assert [d.id for d in declared(project)] == ["agents", "supervisor", "coordinator", "reviewer"]
    assert {d.kind for d in declared(project)} == {"program", "agent"} and declared(project)[1].mode == "continuous"
