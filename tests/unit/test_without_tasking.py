"""Without the task system installed (a Workflows-only project, the owner's Q5: "we can just not
include it"), nothing of it reaches the agents: no `snapshot` or ticket tools, no "the task
system did not answer", no tickets in the prompts, no Coordinator announced, and no
`task.query_requested` on the bus. With it installed, the Supervisor's prompt names the
workflows before the task system."""

import subprocess

import pytest
from conftest import FakeHarness, agents_project

from orchestra_agents import installed, prompting
from orchestra_agents.builtins import ROLES
from orchestra_agents.events import Events
from orchestra_agents.harness.tools import BY_NAME, tools_for
from orchestra_agents.host import AgentHost
from orchestra_agents.org import AgentDef, built_in_agents
from orchestra_agents.store import AgentStore


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


@pytest.fixture
def no_tasking(monkeypatch):
    monkeypatch.setattr(installed, "tasking", lambda: False)


def test_no_task_system_tool_is_offered(no_tasking):
    names = {t.name for t in tools_for(ROLES["supervisor"].tools)}
    for gone in ("snapshot", "list_tickets", "create_ticket", "hand_to_coordinator", "get_gate_run", "set_capacity"):
        assert gone not in names
    assert {"read_events", "list_items", "request_work", "declare_machine"} <= names
    assert "tickets" not in BY_NAME["read_events"].description and "gate runs" not in BY_NAME["read_events"].description


def test_the_task_system_tools_come_back_with_it(monkeypatch):
    monkeypatch.setattr(installed, "tasking", lambda: True)
    assert {"snapshot", "list_tickets", "hand_to_coordinator"} <= {t.name for t in tools_for(ROLES["supervisor"].tools)}


def test_the_prompts_say_nothing_of_tickets(project, no_tasking):
    sup = prompting.agent_prompt(project, built_in_agents()["supervisor"])
    for gone in ("## Ticket states", "Coordinator can *plan*", "The task system offers", "`snapshot`", "{task_system",
                 "{ticket_states}"):
        assert gone not in sup
    custom = AgentDef(name="scout", roles=["scout"], team=None)
    assert "creating or editing tickets" not in prompting.agent_prompt(project, custom)
    from orchestra_agents.tooling import snapshot
    snap = snapshot(AgentStore(project), "supervisor")
    assert snap.get("absent") and not snap.get("unavailable")
    woken = prompting.wake_task("supervisor", ["event attention.needed"], snap)
    assert "did not answer" not in woken and "Ticket counts" not in woken and "Execution health" not in woken
    digest = prompting.supervisor_context(snap, [{"type": "item.requested", "source": "owner"}], None, None)
    assert "task system" not in digest and "ticket counts" not in digest


def test_workflows_come_before_the_task_system(project, monkeypatch):
    monkeypatch.setattr(installed, "tasking", lambda: True)
    sup = prompting.agent_prompt(project, built_in_agents()["supervisor"])
    assert sup.index("declare_workflow") < sup.index("The task system offers") < sup.index("## Ticket states")
    assert "{task_system" not in sup and "{ticket_states}" not in sup


def test_no_coordinator_is_announced_and_nothing_asks_the_task_system(project, no_tasking):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    host.harness = FakeHarness(reply="ok")
    host._refresh_org(store.runtime()["agent_state"])
    host._wake("coordinator", ["event work.requested"])
    host._wake("supervisor", ["event attention.needed"])
    host.prompt_supervisor("what needs me?", lambda ev: None)
    evs = events.read(0)[0]
    started = [e for e in evs if e["type"] == "orchestra.machine.started"]
    assert "coordinator" not in {e["source"] for e in started} and "supervisor" in {e["source"] for e in started}
    sup = next(e for e in started if e["source"] == "supervisor")
    assert not {"request.done", "request.refused", "ticket.created", "gate.landed", "work.planned"} & set(sup["listens_for"])
    assert not [e for e in evs if e["type"] == "task.query_requested"]
    assert "coordinator" not in host.residents.procs
