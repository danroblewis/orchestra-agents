"""The Supervisor hands the human's requirements to the Coordinator, which plans them into a
milestone and tickets and reports the plan back (the owner, 2026-10-28). The Supervisor makes no
ticket itself; the tickets a plan names are followed, so the human hears when they are done."""

import subprocess
from types import SimpleNamespace

import pytest
from conftest import agents_project
from fake_tasks import FakeTasks

from orchestra_agents import handoff, tooling
from orchestra_agents.builtins import ROLES
from orchestra_agents.events import Events
from orchestra_agents.landings import Landings
from orchestra_agents.org import event_matches
from orchestra_agents.store import AgentStore, StoreError

ASK = ("A page that lists the open orders, newest first. Verified by a test that loads it with two orders and checks "
       "the order. Priority: soon.")


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def call(project, store, tool, role, **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role=role)


def test_the_supervisor_hands_on_and_makes_no_ticket(project):
    store = AgentStore(project, Events.for_project(project))
    for tool, args in (("create_ticket", {"title": "t", "objective": "o", "description": "d", "acceptance": "a",
                                          "verification": "v", "milestone_id": "M1"}), ("create_milestone", {"name": "M"})):
        with pytest.raises(StoreError, match="needs the 'planning' tool group"):
            call(project, store, tool, "supervisor", **args)
    with pytest.raises(StoreError, match="write the requirements out"):
        call(project, store, "hand_to_coordinator", "supervisor", requirements="a page")
    out = call(project, store, "hand_to_coordinator", "supervisor", requirements=ASK, context="The shop's objective 2.")
    rid = out["request"]
    assert rid.startswith("r-") and out["done"] == f"Changed: handed request {rid} to the Coordinator (work.requested)."
    [ev] = [e for e in store.events.read(0)[0] if e["type"] == "work.requested"]
    assert (ev["source"], ev["to"], ev["by"], ev["request"], ev["requirements"]) == ("supervisor", "coordinator", "supervisor", rid, ASK)
    assert event_matches(list(ROLES["coordinator"].wake_on), ev) and not event_matches(list(ROLES["supervisor"].wake_on), ev)
    said = handoff.wake_reason(ev)
    assert f"request={rid}" in said and f"requirements: {ASK}" in said and "context: The shop's objective 2." in said
    with pytest.raises(StoreError, match="needs the 'handoff' tool group"):
        call(project, store, "hand_to_coordinator", "coordinator", requirements=ASK)


def test_the_coordinator_plans_and_reports_back_and_the_tickets_are_followed(project):
    store = AgentStore(project, Events.for_project(project))
    rid = call(project, store, "hand_to_coordinator", "supervisor", requirements=ASK)["request"]
    with FakeTasks(project):
        m = call(project, store, "create_milestone", "coordinator", name="Orders page")["milestone"]["id"]
        t = call(project, store, "create_ticket", "coordinator", title="List open orders", objective="o", description="d",
                 acceptance="a", verification="v", milestone_id=m)["ticket"]["id"]
        with pytest.raises(StoreError, match="no request 'r-nope'"):
            call(project, store, "report_plan", "coordinator", request="r-nope", summary="s", tickets=[t])
        with pytest.raises(StoreError, match="no ticket T-99"):
            call(project, store, "report_plan", "coordinator", request=rid, summary="s", tickets=[t, "T-99"])
        with pytest.raises(StoreError, match="names the tickets you created, or the questions"):
            call(project, store, "report_plan", "coordinator", request=rid, summary="s")
        out = call(project, store, "report_plan", "coordinator", request=rid, summary="One ticket.", milestone=m, tickets=[t])
        assert out["tickets"] == [t] and "reported the plan" in out["done"]
        with pytest.raises(StoreError, match="already reported"):
            call(project, store, "report_plan", "coordinator", request=rid, summary="again", tickets=[t])
    evs = store.events.read(0)[0]
    asked = next(e for e in evs if e["type"] == "work.requested")
    [plan] = [e for e in evs if e["type"] == "work.planned"]
    assert (plan["source"], plan["to"], plan["tickets"], plan["causationid"]) == ("coordinator", "supervisor",
                                                                                   [{"id": t, "title": "List open orders"}], asked["id"])
    assert event_matches(list(ROLES["supervisor"].wake_on), plan)
    said = handoff.wake_reason(plan)
    assert f"Tickets: {t} (List open orders)." in said and "Tell the human" in said

    cfg = SimpleNamespace(work="code", base_branch="main")
    landings = Landings(store)
    assert landings.see(plan, cfg) is None and store.agent_state("supervisor")["asked_tickets"] == {t: "List open orders"}
    done = landings.see({"type": "gate.landed", "ticket": t}, cfg)
    assert done.startswith(f"ticket {t} (List open orders), which was made for the human's request, is DONE")


def test_creating_tickets_is_planning_and_the_human_s_decisions_are_the_supervisor_s(project):
    assert tooling.may_change_tickets(project, "coordinator", "create_ticket")
    assert not tooling.may_change_tickets(project, "supervisor", "create_ticket")
    assert tooling.may_change_tickets(project, "supervisor", "cancel_ticket")
    assert not tooling.may_change_tickets(project, "", "cancel_ticket")
