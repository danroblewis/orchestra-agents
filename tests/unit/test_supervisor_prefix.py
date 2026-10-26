"""What the host puts in front of a turn: the context and digest before a human's message to the
Supervisor, and the execution health a wake-up reads from the task system's snapshot."""

import subprocess

import pytest
from conftest import agents_project

from orchestra_agents import prompting as agents
from orchestra_agents.prompting import execution_health
from orchestra_agents.store import AgentStore


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo, provider="p", model="m")


def test_context_document_is_sent_once_until_it_changes(project):
    store = AgentStore(project)
    snap = {"counts": {}, "tickets": [], "milestones": [], "pipeline": {"paused": False}, "capacity": {}, "assignments": []}   # what tooling.snapshot gives when no task system answers
    first = agents.supervisor_context(snap, [], project, store)
    assert first.startswith("[Orchestra: project context]") and "## Objectives" in first
    assert agents.supervisor_context(snap, [], project, store) == ""          # "yes, go ahead" arrives as itself
    agents.write_context(project, "# Project context\n\n## Objectives\n- ship it\n")
    again = agents.supervisor_context(snap, [], project, store)
    assert again.startswith("[Orchestra: project context, updated]") and "ship it" in again
    assert agents.supervisor_context(snap, [], project, store) == ""
    # events still arrive, compactly
    evs = [{"type": "ticket.transitioned", "ticket": "T-1", "from": "OPEN", "to": "DONE"}]
    out = agents.supervisor_context(snap, evs, project, store)
    assert "[Orchestra: project context" not in out and "ticket.transitioned ticket=T-1" in out
    # without a store (the CLI's temporary session) the document is always included
    assert agents.supervisor_context(snap, [], project).startswith("[Orchestra: project context]")


def test_execution_health_reads_the_snapshot_as_the_task_system_sends_it():
    """The keys are the task system's (tasking's snapshot()): a ticket's effective priority is
    under priority_info, the dial note under capacity, and who is working is in assignments,
    whatever machine holds them."""
    ticket = {"id": "T-1", "state": "OPEN", "title": "t", "priority": 5, "priority_info": {"base": 5, "effective": 3}, "ready": True,
              "why_waiting": "ready", "milestone_id": "M1", "review_rounds": 0, "objective": ""}
    snap = {"counts": {"OPEN": 1}, "tickets": [ticket], "milestones": [{"id": "M1", "status": "open", "name": "m"}],
            "pipeline": {"paused": False}, "capacity": {"workers": 2, "reviewers": 1, "max_workers": 2, "max_reviewers": 1, "note": "one at a time"},
            "assignments": [{"id": "a1", "role": "worker", "instance": "worker/1", "ticket": "T-1"}]}
    lines = execution_health(snap)
    assert lines[0] == "Worker capacity: 2 of a configured maximum 2 (dial note: one at a time)"
    assert lines[2] == "Workers running: 1 ['worker/1']" and lines[3] == "Reviewers running: 0"
    assert lines[4].startswith("Ready tickets") and "T-1(p3)" in lines[4]
    assert not any(ln.startswith("DIAGNOSIS") for ln in lines)
    snap["assignments"] = []
    snap["capacity"]["note"] = None
    lines = execution_health(snap)
    assert lines[0] == "Worker capacity: 2 of a configured maximum 2"
    assert any(ln.startswith("DIAGNOSIS: tickets are ready and capacity exists but no worker is running") for ln in lines)
