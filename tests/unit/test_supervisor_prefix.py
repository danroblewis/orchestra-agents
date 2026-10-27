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
    # events still arrive, as a digest
    evs = [{"type": "task.reported", "ticket": "T-1", "report": {"state": "BLOCKED", "blocked_reason": "no creds"}}]
    out = agents.supervisor_context(snap, evs, project, store)
    assert "[Orchestra: project context" not in out and "task.reported ticket=T-1" in out
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


def test_the_digest_stays_short_however_busy_the_bus_was(project):
    """A wake-up after a busy hour carries counts by type and only the few events that concern
    the Supervisor, not the event list: a long digest on every turn filled its context."""
    snap = {"counts": {"OPEN": 3}, "tickets": [], "milestones": [], "pipeline": {"paused": False}, "capacity": {}, "assignments": []}
    evs = [{"type": "task.working", "ticket": f"T-{i % 5}", "text": "x" * 200} for i in range(500)]
    evs += [{"type": "task.accepted", "ticket": f"T-{i}", "role": "worker", "to": "IN_PROGRESS"} for i in range(50)]
    evs += [{"type": "task.attention_needed", "to": "coordinator", "reason": "T-2 BLOCKED"}]
    evs += [{"type": "task.attention_needed", "to": "supervisor", "reason": f"decide {i}"} for i in range(12)]
    evs += [{"type": "orchestra.process.gave_up", "name": "telemetry"}]
    store = AgentStore(project)
    agents.supervisor_context(snap, [], project, store)                     # the context document, seen once
    out = agents.supervisor_context(snap, evs, project, store)
    lines = out.strip().splitlines()
    assert lines[0].startswith("[Orchestra: 564 events since your last turn: task.working=500, task.accepted=50, task.attention_needed=13")
    named = [ln for ln in lines if ln.startswith("- ")]
    assert len(named) == agents.DIGEST_EVENTS and named[-1] == "- orchestra.process.gave_up name=telemetry"
    assert not any("coordinator" in ln or "IN_PROGRESS" in ln or "task.working " in ln for ln in named)
    assert "(the last 8 of 13)" in out and lines[-1] == "[Orchestra: ticket counts now: OPEN=3]"
    assert len(out) < 2000
