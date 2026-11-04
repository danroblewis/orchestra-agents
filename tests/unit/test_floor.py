"""The Workflows floor's contract as the agents copy it (floor.py), the roles they serve on it
(floor_roles.py), a confirmed Universal Machine's spec with ``role`` (machine_spec.py) and its exit
tool (``report_outcome``, tooling.py): the mapping from what an agent reports to the outcome an
offer finishes with, and who holds an offer by the bus's order."""

import subprocess
from pathlib import Path

import pytest
from conftest import agents_project
from floor_helpers import universal_machine

from orchestra_agents import access, floor, tooling
from orchestra_agents.events import Events
from orchestra_agents.floor_roles import served, spec_roles
from orchestra_agents.listening import listens_for
from orchestra_agents.machine_spec import load_all, parse, render
from orchestra_agents.org import built_in_agents
from orchestra_agents.store import AgentStore, StoreError


@pytest.mark.parametrize("report, outcomes, want", [
    ({"state": "READY_FOR_REVIEW", "summary": "done"}, [], ("ready", "done")),
    ({"state": "BLOCKED", "summary": "s", "blocked_reason": "no access"}, [], ("blocked", "no access")),
    ({"verdict": "APPROVED", "summary": "fine"}, ["approved", "rejected"], ("approved", "fine")),
    ({"verdict": "REJECTED", "summary": "no", "feedback": "1. fix x"}, [], ("rejected", "no\n\n1. fix x")),
    ({"outcome": "triaged", "summary": "a bug"}, ["triaged"], ("triaged", "a bug")),
    ({"outcome": "blocked", "summary": "why"}, ["triaged"], ("blocked", "why")),          # blocked is always allowed
])
def test_an_exit_tool_s_report_maps_to_an_outcome(report, outcomes, want):
    assert floor.outcome_of(report, outcomes) == want


def test_no_report_or_an_outcome_the_offer_does_not_allow_finishes_blocked():
    assert floor.outcome_of(None, []) == ("blocked", "the agent's turn ended without reporting an outcome")
    outcome, summary = floor.outcome_of({"state": "READY_FOR_REVIEW", "summary": "x"}, ["landed"])
    assert outcome == "blocked" and "'ready', not one of landed" in summary
    assert floor.outcome_of({"summary": "x"}, [])[0] == "blocked"


def ev(type, source, offer="I1.work.aa", **kw):
    return {"type": type, "source": source, "offer": offer, **kw}


def test_the_first_claim_after_the_offer_and_before_a_withdrawal_holds_it():
    made = ev("offer.made", "coordinator/dispatch", role="worker", item="I1")
    assert floor.winner([made, ev("offer.claimed", "worker/2"), ev("offer.claimed", "worker/1")], "I1.work.aa") == "worker/2"
    assert floor.winner([ev("offer.claimed", "worker/2"), made, ev("offer.claimed", "worker/1")], "I1.work.aa") == "worker/1"
    assert floor.winner([made, ev("offer.withdrawn", "coordinator/dispatch"), ev("offer.claimed", "worker/1")], "I1.work.aa") is None
    to = {**made, "to": "worker/3"}
    assert floor.winner([to, ev("offer.claimed", "worker/1"), ev("offer.claimed", "worker/3")], "I1.work.aa") == "worker/3"
    assert floor.winner([made, ev("offer.claimed", "worker/1", offer="other")], "I1.work.aa") is None


def test_a_withdrawal_stops_the_offer_it_names():
    assert floor.stops(ev("offer.withdrawn", "dispatch"), "I1.work.aa")
    assert floor.stops(ev("offer.withdrawn", "worker", instance="worker/1"), "I1.work.aa")
    assert not floor.stops(ev("offer.withdrawn", "worker", offer=None, instance="worker/1"), "I1.work.aa")
    assert not floor.stops(ev("offer.withdrawn", "dispatch", offer="I2.work.bb"), "I1.work.aa")


def test_the_workspace_is_the_handed_worktree():
    assert floor.workspace({"resources": [{"kind": "db", "handle": "x"}, {"kind": "worktree", "id": "w1", "handle": "/wt/1"}]}) == "/wt/1"
    assert floor.workspace({"resources": []}) is None


def test_the_task_names_the_exit_tool_and_the_outcomes():
    o = {"offer": "I1.work.aa", "item": "I1", "step": "work", "workflow": "fix", "brief": "Fix the login.", "outcomes": ["ready"]}
    task, nudge = floor.task_text(o, "worker", "/wt/1")
    assert "Fix the login." in task and "/wt/1" in task and "report_ticket" in task and nudge.startswith("You stopped without")
    task, _ = floor.task_text({**o, "outcomes": ["triaged", "duplicate"]}, "triage", None)
    assert "`report_outcome`" in task and "`triaged`, `duplicate`" in task and "read only" in task


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def test_a_spec_s_role_parses_and_renders():
    spec = parse("triage", {"machine.toml": 'name = "triage"\nrole = "triager"\n'})
    assert spec.role == "triager" and 'role = "triager"' in render(spec)
    assert parse("x", {"machine.toml": 'role = "Not A Role!"\n'}).role is None


def test_a_confirmed_universal_machine_serves_its_role_and_the_built_ins_the_rest(project):
    assert served(project) == {"worker": "worker", "reviewer": "reviewer"}
    universal_machine(project)
    assert served(project) == {"triager": "triage", "worker": "worker", "reviewer": "reviewer"}
    d = project.repo_root / ".orchestra" / "machines" / "ghost"            # a spec whose name is no agent: ignored
    d.mkdir(parents=True)
    (d / "machine.toml").write_text('role = "haunter"\n')
    assert "haunter" not in spec_roles(project)


def test_a_spec_in_the_state_directory_is_read_and_the_repository_s_wins(project):
    universal_machine(project, in_state_dir=True)
    specs = load_all(project)
    assert specs["triage"].role == "triager" and specs["triage"].dir == f"{project.state_dir / 'machines'}/triage"
    d = project.repo_root / ".orchestra" / "machines" / "triage"
    d.mkdir(parents=True)
    (d / "machine.toml").write_text('role = "sorter"\n')
    assert load_all(project)["triage"].role == "sorter" and load_all(project)["triage"].dir == ".orchestra/machines/triage"


def test_the_machine_s_agent_holds_report_outcome_and_listens_for_offers(project):
    assert access.granted_groups(project, "triage") is None                 # no such agent yet
    universal_machine(project)
    assert "report_outcome" in access.granted_groups(project, "triage")
    # the built-in worker serves the floor here (serves = ["worker", "reviewer"]): it holds report_outcome
    # for an offer whose outcomes report_ticket does not report (floor.report_tool); never otherwise
    assert ("report_outcome" in access.granted_groups(project, "worker")) is ("worker" in project.config.serves)
    worker = built_in_agents()["worker"]
    assert {"offer.made", "offer.withdrawn"} <= set(listens_for(worker, "code", floor=True))
    assert "offer.made" not in listens_for(worker, "code")


def test_report_outcome_is_checked_against_the_offer_the_session_holds(project):
    universal_machine(project)
    store = AgentStore(project, Events.for_project(project, source="triage/1"))
    with pytest.raises(StoreError, match="holds none"):
        tooling.handle(project, store, "report_outcome", {"outcome": "triaged", "summary": "x"}, role="triage", ticket_env="I1.work.aa")
    store.set_session("triage-I1", kind="triage", offer="I1.work.aa", offer_outcomes=["triaged"])
    with pytest.raises(StoreError, match="one of triaged or blocked"):
        tooling.handle(project, store, "report_outcome", {"outcome": "fixed", "summary": "x"}, role="triage", ticket_env="I1.work.aa")
    with pytest.raises(StoreError, match="holds none"):          # the triage session's offer is not the worker's
        tooling.handle(project, store, "report_outcome", {"outcome": "ready", "summary": "x"}, role="worker", ticket_env="I1.work.aa")
    assert tooling.handle(project, store, "report_outcome", {"outcome": "triaged", "summary": "a bug"}, role="triage",
                          ticket_env="I1.work.aa")["ok"]
    assert store.pop_report("I1.work.aa") == {"outcome": "triaged", "summary": "a bug"}


# event sequences both readings must agree on: (offer.made's to, then (type, source, data) in bus order)
TABLE = [
    (None, [("offer.claimed", "worker/2", {}), ("offer.claimed", "worker/1", {})]),
    (None, [("offer.withdrawn", "coordinator/dispatch", {}), ("offer.claimed", "worker/1", {})]),
    ("worker/3", [("offer.claimed", "worker/1", {}), ("offer.claimed", "worker/3", {})]),
    (None, [("offer.claimed", "worker/1", {"offer": "other"})]),
    (None, [("offer.alive", "worker/1", {}), ("offer.claimed", "worker/4", {}), ("offer.withdrawn", "worker", {"instance": "worker/4"})]),
    (None, []),
]
STOPS = [("offer.withdrawn", {"offer": "I1.work.aa"}), ("offer.withdrawn", {"offer": "I1.work.aa", "instance": "worker/2"}),
         ("offer.withdrawn", {"offer": "I2.work.bb", "instance": "worker/1"}), ("offer.made", {"offer": "I1.work.aa"})]


def test_the_copied_names_match_the_workflows_contract():
    contract = pytest.importorskip("orchestra_workflows.contract")
    assert (floor.MADE, floor.CLAIMED, floor.ALIVE, floor.FINISHED, floor.WITHDRAWN, floor.BLOCKED) == (
        contract.MADE, contract.CLAIMED, contract.ALIVE, contract.FINISHED, contract.WITHDRAWN, contract.BLOCKED)
    assert tuple(contract.LISTENS_FOR) == floor.LISTENS_FOR
    if hasattr(contract, "RETRY_REQUESTED"):                            # W17: the planner's retry
        assert floor.RETRY_REQUESTED == contract.RETRY_REQUESTED
    acts = pytest.importorskip("orchestra_workflows.floor.acts")
    assert floor.ATTENTION_NEEDED == acts.ATTENTION                       # the floor's attention event
    assert floor.CLOSED == acts.CLOSED                                    # an item's end
    oid = "I1.work.aa"
    for to, seq in TABLE:                                                 # the claim rule, read both ways
        made = {"offer": oid, "role": "worker", "item": "I1", "to": to}
        envelopes = [{"type": "offer.made", "source": "coordinator/dispatch", "data": made}] + [
            {"type": t, "source": s, "data": {"offer": oid, **d}} for t, s, d in seq]
        flat = [{"type": e["type"], "source": e["source"], **e["data"]} for e in envelopes]
        assert floor.winner(flat, oid) == contract.winner(envelopes, oid), (to, seq)
    for t, d in STOPS:                                                    # the withdrawal rule, read both ways
        assert floor.stops({"type": t, **d}, oid) == contract.stops({"type": t, "data": d}, oid, "worker/1"), d


CONTRACT_DOC = Path.home() / "orchestra-workflows" / "docs" / "machine-contract.md"


@pytest.mark.skipif(not CONTRACT_DOC.exists(), reason="the Workflows plugin's repository is not beside this one")
def test_the_copied_item_closed_is_the_one_the_contract_document_describes():
    row = next(line for line in CONTRACT_DOC.read_text().splitlines() if line.startswith(f"| `{floor.CLOSED}`"))
    for said in ("<planner>/dispatch", "the end of every workflow it entered", "reason: finished", "`workflow`",
                 "`last_step`", "`outcome`", "Nothing waits on a closed item"):
        assert said in row, said
    assert all(f"`{k}`" in row for k in floor.CLOSED_BY_FLOOR), row


def a_host(project, harness):
    from orchestra_agents.host import AgentHost
    host = AgentHost(project, AgentStore(project), Events.for_project(project))
    host.harness = harness
    return host


def test_a_turn_with_no_outcome_finishes_blocked_and_rework_for_another_machine_is_left(project, monkeypatch):
    from conftest import FakeHarness
    from floor_helpers import ScriptedFloor
    monkeypatch.delenv("ORCHESTRA_SOURCE", raising=False)
    host, floor_ = a_host(project, FakeHarness(reply="I did something.")), ScriptedFloor(project)
    oid = floor_.offer("worker", "I7", "Do it.")
    other = floor_.offer("worker", "I8", "Rework.", to="robot/1")          # addressed to another machine's instance
    host.floor.tick()
    assert set(host.floor.threads) == {oid}
    host.floor.threads[oid].join(20)
    [fin] = floor_.of(oid, "offer.finished")
    assert fin["source"] == "worker/1" and fin["data"]["outcome"] == "blocked"
    assert "without reporting an outcome" in fin["data"]["summary"] and "I did something." in fin["data"]["summary"]
    assert len(host.harness.sessions[0].prompts) == 2 and host.harness.sessions[0].prompts[1].startswith("You stopped without")
    assert not floor_.of(other, "offer.claimed") and host._slots == {}


def test_a_claim_read_back_while_the_host_stops_opens_no_session_and_finishes_nothing(project, monkeypatch):
    from conftest import FakeHarness
    from floor_helpers import ScriptedFloor
    monkeypatch.delenv("ORCHESTRA_SOURCE", raising=False)
    host, floor_ = a_host(project, FakeHarness(reply="done")), ScriptedFloor(project)
    after = host.floor.events.after

    def stopping(start):                                  # the host is told to stop while it reads its claim back
        host.stop.set()
        return after(start)
    monkeypatch.setattr(host.floor.events, "after", stopping)
    oid = floor_.offer("worker", "I9", "Do it.")
    host.floor.tick()
    host.floor.threads[oid].join(20)
    assert floor_.of(oid, "offer.claimed") and not floor_.of(oid, "offer.finished")   # the lease takes it back
    assert host.harness.sessions == [] and host._slots == {}


def test_the_floor_s_attention_wakes_the_planner_it_names(project, request):
    from conftest import FakeHarness
    host = a_host(project, FakeHarness())
    store = host.store
    request.addfinalizer(host.residents.close_all)
    host._refresh_org(store.runtime()["agent_state"])
    host._wake_at = store.events.end()
    store.events.publish(floor.ATTENTION_NEEDED, {"to": "coordinator", "reason": "review passed its within",
                                                  "workflow": "loop", "step": "review", "guard": "within"},
                         source="coordinator/dispatch", subject="I1")
    host._wake_on_events()
    woke = {w["target"]: w["reason"] for w in store.runtime()["wakeups"]}
    assert "coordinator" in woke and "supervisor" not in woke
    assert "the Workflows floor asks the coordinator to look at I1 (workflow loop step review)" in woke["coordinator"]
    assert "guard" not in woke["coordinator"].split(": ", 1)[1].replace("review passed its within", "")
    assert "explain_item says what it waits for and what ends the wait" in woke["coordinator"]
    store.events.publish(floor.ATTENTION_NEEDED, {"to": "supervisor", "reason": "x"}, source="coordinator/dispatch", subject="I2")
    host._wake_on_events()
    assert any(w["target"] == "supervisor" and "attention.needed" in w["reason"] for w in store.runtime()["wakeups"])


def test_the_built_in_exit_tool_only_when_the_offer_has_its_outcomes():
    """R3 e (09:41Z "a 'task' is a type of 'item'"): a worker's step whose outcomes are not ready is
    given report_outcome with those outcomes, never report_ticket folded into blocked."""
    assert floor.report_tool("worker", ["ready", "blocked"]) == "report_ticket"
    assert floor.report_tool("worker", []) == "report_ticket"                       # any outcome goes on
    assert floor.report_tool("worker", ["drafted", "skipped"]) == "report_outcome"
    assert floor.report_tool("reviewer", ["approved", "rejected"]) == "report_review"
    assert floor.report_tool("reviewer", ["approved"]) == "report_outcome"
    assert floor.report_tool("triage", ["triaged"]) == "report_outcome"
    task, _ = floor.task_text({"item": "I1", "offer": "I1.w.1", "outcomes": ["drafted", "skipped"]}, "worker", None)
    assert "`report_outcome` with `outcome` one of `drafted`, `skipped` or `blocked`" in task and "report_ticket" not in task
    assert floor.outcome_of({"outcome": "drafted", "summary": "ok"}, ["drafted", "skipped"]) == ("drafted", "ok")


def test_a_worker_session_on_such_an_offer_holds_report_outcome_not_report_ticket(project):
    from types import SimpleNamespace

    from orchestra_agents.floor_runner import FloorRunner
    me = SimpleNamespace(host=SimpleNamespace(project=project))
    _, tools = FloorRunner._session_of(me, "worker", {"outcomes": ["drafted", "skipped"]})
    assert "report_outcome" in tools and "report_ticket" not in tools
    _, tools = FloorRunner._session_of(me, "worker", {"outcomes": ["ready", "blocked"]})
    assert "report_ticket" in tools and "report_outcome" not in tools
