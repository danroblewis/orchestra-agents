"""Who the planners are told fills the task system's roles: what is observed on the bus (an
announcement of a role's instance that listens for offers, an acceptance), with what each machine
said it is, never assumed from the agents' own configuration alone."""

import subprocess

import pytest
from conftest import agents_project

from orchestra_agents import prompting
from orchestra_agents.events import Events
from orchestra_agents.fillers import Fillers


@pytest.fixture
def events(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return Events.for_project(agents_project(repo))


def test_a_program_announced_before_the_host_started_and_an_agent_seen_accepting(events):
    events.publish("orchestra.machine.started", {"kind": "program", "listens_for": ["task.assigned", "task.released"]}, source="worker/shell")
    events.publish("orchestra.machine.started", {"kind": "program", "listens_for": []}, source="planner")
    fillers = Fillers(events)                                   # the host starts after the program announced
    assert fillers.lines(["worker", "reviewer"]) == [
        "The AI agents here fill: worker, reviewer (`[agents] serves`).",
        "Seen filling the worker role: worker/shell (a program)."]
    evs = [{"type": "orchestra.machine.started", "kind": "agent", "listens_for": [], "source": "reviewer/1"},
           {"type": "task.accepted", "role": "reviewer", "instance": "reviewer/1", "source": "reviewer/1"}]
    fillers.see(evs)
    lines = fillers.lines(["worker", "reviewer"])
    assert "Seen filling the reviewer role: reviewer/1 (an AI agent)." in lines
    task = prompting.wake_task("coordinator", ["T-1 BLOCKED"], {"counts": {}, "tickets": [], "milestones": [], "pipeline": {},
                                                                "capacity": {}, "assignments": []}, lines)
    assert "## Who fills the roles\nThe AI agents here fill: worker, reviewer" in task


def test_nothing_seen_is_said(events):
    assert Fillers(events).lines([]) == ["The AI agents here fill: no task role (`[agents] serves`).",
                                         "No machine has been seen filling a role yet."]
