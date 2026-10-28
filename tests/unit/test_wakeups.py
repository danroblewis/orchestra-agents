"""Who the host wakes on what: the built-in planners on the task system's attention events
addressed to them, custom agents on what they subscribe to, never a worker or reviewer (their
events are assignments, answered by the runner); which roles are the agents' at all; and the
runner's side of an assignment: a slot per instance, a claim that lost the race on the bus, a
release that is not a failure, rejected work coming back to the same slot and session, a finished
ticket archived, and a report for every open assignment at shutdown."""

import json
import subprocess
import threading
import time

import pytest
from conftest import FakeHarness, agents_project

from orchestra_agents import protocol
from orchestra_agents.config import ConfigError
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.org_store import all_agents
from orchestra_agents.store import AgentStore
from orchestra_agents.web import declared


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def host_with(project, harness):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    host.harness = harness
    return host, store, events


def wait_for(events, type_, n=1, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        got = [e for e in events.read(0)[0] if e["type"] == type_]
        if len(got) >= n:
            return got
        time.sleep(0.05)
    raise AssertionError(f"no {type_} within {timeout}s")


def assigned(events, aid, ticket, workspace, role="worker"):
    events.publish("task.assigned", {"assignment": aid, "role": role, "ticket": ticket, "kind": "work", "workspace": str(workspace),
                                     "branch": None, "lease_s": 180, "brief": {"id": ticket, "title": "t", "description": "d"}}, source=role, subject=ticket)


def test_attention_wakes_the_planner_it_names(project):
    host, store, events = host_with(project, FakeHarness(reply="ok"))
    host._refresh_org(store.runtime()["agent_state"])
    events.publish("task.attention_needed", {"to": "coordinator", "reason": "DEMO-3 is blocked", "ticket": "DEMO-3"}, source="worker", subject="DEMO-3")
    events.publish("task.assigned", {"assignment": "a1", "role": "reviewer", "ticket": "DEMO-1"}, source="reviewer", subject="DEMO-1")
    events.publish("task.accepted", {"assignment": "a1", "ticket": "DEMO-1", "role": "reviewer"}, source="reviewer/1", subject="DEMO-1")
    host._wake_on_events()
    pending = store.runtime()["wakeups"]
    assert [w["target"] for w in pending] == ["coordinator"]
    assert "task.attention_needed" in pending[0]["reason"] and "DEMO-3 is blocked" in pending[0]["reason"]
    events.publish("task.attention_needed", {"to": "supervisor", "reason": "the base branch is broken"}, source="merge_gate")
    host._wake_on_events()
    assert [w["target"] for w in store.runtime()["wakeups"]] == ["coordinator", "supervisor"]
    assert set(host.residents.procs) == {"supervisor", "coordinator"}         # the built-in residents came up with the org


def test_a_role_another_machine_fills_is_not_an_agent_here(project):
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text().replace('serves = ["worker", "reviewer"]', 'serves = ["reviewer"]'))
    project.reload_config()
    assert project.config.serves == ["reviewer"]
    assert set(all_agents(project)) == {"supervisor", "coordinator", "reviewer"}
    assert [d.id for d in declared(project)] == ["supervisor", "coordinator", "reviewer"]      # the host is no machine
    assert {d.kind for d in declared(project)} == {"agent"} and declared(project)[0].mode == "continuous"


def test_a_finished_ticket_has_its_sessions_archived(project):
    host, store, events = host_with(project, FakeHarness(reply="ok"))
    store.set_session("worker-T-1", kind="worker", status="idle", ticket_id="T-1")
    store.set_session("reviewer-T-1", kind="reviewer", status="idle", ticket_id="T-1")
    store.set_session("worker-T-2", kind="worker", status="idle", ticket_id="T-2")
    events.publish("gate.landed", {"ticket": "T-1", "gate_run": "g1"}, source="merge_gate", subject="T-1")    # the gate's act: it is done
    host.runner.tick()
    rt = store.runtime()
    assert set(rt["archived_sessions"]) == {"worker-T-1", "reviewer-T-1"} and set(rt["sessions"]) == {"worker-T-2"}
    assert rt["archived_sessions"]["worker-T-1"]["status"] == "archived"


def test_two_offers_in_one_tick_take_different_slots(project, tmp_path):
    host, _store, events = host_with(project, FakeHarness(reply="done"))
    for n in (1, 2):
        (tmp_path / f"w{n}").mkdir()
        assigned(events, f"a{n}", f"T-{n}", tmp_path / f"w{n}")
    host.runner.tick()
    for th in list(host.runner.threads.values()):
        th.join(20)
    accepted = [e for e in events.read(0)[0] if e["type"] == "task.accepted"]
    assert {e["instance"] for e in accepted} == {"worker/1", "worker/2"}
    assert all(e["source"] == e["instance"] for e in accepted)
    reported = [e for e in events.read(0)[0] if e["type"] == "task.reported"]
    assert {e["assignment"] for e in reported} == {"a1", "a2"} and all(e["report"] is None for e in reported)   # the fake never reports


def test_a_released_assignment_is_an_interruption_not_a_failure(project, tmp_path):
    host, _store, events = host_with(project, FakeHarness())         # turns block until the session is killed
    (tmp_path / "w").mkdir()
    assigned(events, "a1", "T-1", tmp_path / "w")
    host.runner.tick()
    wait_for(events, "session.started")                                  # confirmed, and the turn is running
    events.publish("task.released", {"assignment": "a1", "ticket": "T-1", "role": "worker", "reason": "operator stopped it"}, source="worker", subject="T-1")
    host.runner.tick()
    host.runner.threads["a1"].join(20)
    evs = events.read(0)[0]
    cut = [e for e in evs if e["type"] == "session.interrupted"]
    assert cut and cut[-1]["reason"] == "released by the task system: operator stopped it" and cut[-1]["session"] == "worker-T-1"
    assert not [e for e in evs if e["type"] in ("task.reported", "session.error")]
    assert host.runner.released == {}


def test_shutdown_reports_every_open_assignment_as_failed(project, tmp_path):
    host, store, events = host_with(project, FakeHarness())
    (tmp_path / "w").mkdir()
    th = threading.Thread(target=host.run, daemon=True)
    th.start()
    assigned(events, "a1", "T-1", tmp_path / "w")
    wait_for(events, "task.accepted", timeout=20)
    host.stop.set()
    th.join(30)
    assert not th.is_alive()
    reported = [e for e in events.read(0)[0] if e["type"] == "task.reported"]      # on the bus before run() returned
    assert len(reported) == 1 and reported[0]["assignment"] == "a1" and reported[0]["failed"] is True
    assert store.runtime().get("control") is None
    assert all(s["status"] == "idle" and s["pid"] is None for s in store.sessions())


def test_a_session_that_fails_to_start_keeps_no_slot_and_announces_no_machine(project, tmp_path):
    class NoHarness(FakeHarness):
        def start(self, spec):
            raise RuntimeError("no such binary")
    host, _store, events = host_with(project, NoHarness())
    for n in (1, 2):
        (tmp_path / f"w{n}").mkdir()
        assigned(events, f"a{n}", f"T-{n}", tmp_path / f"w{n}")
        host.runner.tick()
        host.runner.threads[f"a{n}"].join(20)
    evs = events.read(0)[0]
    reported = [e for e in evs if e["type"] == "task.reported"]
    assert [(e["assignment"], e["source"], e["failed"]) for e in reported] == [("a1", "worker/1", True), ("a2", "worker/1", True)]
    assert "no such binary" in reported[0]["text"]
    assert host._slots == {} and events._bound == {}
    assert not [e for e in evs if e["type"] == "orchestra.machine.started"]     # a session that never opened is no machine


def test_a_rotated_ticket_session_starts_its_turn_once(project, tmp_path):
    """When the session file of a re-engaged ticket session is too large to keep, the session
    is rotated and started fresh: one turn start, one started counter, not two."""
    host, store, events = host_with(project, FakeHarness(reply="done"))
    big = project.sessions_dir / "worker-T-1.jsonl"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_text(json.dumps({"type": "message", "message": {"role": "assistant", "usage": {"totalTokens": project.config.compact_tokens + 1}}}) + "\n")
    store.set_session("worker-T-1", kind="worker", status="idle", ticket_id="T-1", session_file=str(big))
    (tmp_path / "w").mkdir()
    assigned(events, "a1", "T-1", tmp_path / "w")
    host.runner.tick()
    host.runner.threads["a1"].join(20)
    assert [e["type"] for e in events.read(0)[0] if e["type"] == "agent.reset"]      # it was rotated
    rec = store.find_session("worker-T-1")
    assert [m["type"] for m in rec["markers"]] == ["agent.reset", "session.started"]      # the fresh record: its reset, then one turn start
    counts = store.runtime()["metric_counts"]
    assert counts["agent_turns_started_total|machine=worker|role=worker"] == 1 == counts["agent_turns_finished_total|machine=worker|role=worker"]


def test_the_coordinators_schedule_is_the_projects(project):
    """Without `coordinator_every` the Coordinator is woken by events only; with it, it is a
    scheduled agent on that interval."""
    host, store, _events = host_with(project, FakeHarness(reply="ok"))
    host._refresh_org(store.runtime()["agent_state"])
    c = host.agents["coordinator"]
    assert c.kind == "continuous" and c.every is None
    host._started_at -= 3600 * 24                              # however long ago the host started
    host._schedule_wakeups(store.runtime()["agent_state"])
    assert not [w for w in store.runtime()["wakeups"] if w["reason"].startswith("scheduled check-in")]
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text() + 'coordinator_every = "5m"\n')
    project.reload_config()
    c = all_agents(project)["coordinator"]
    assert c.kind == "scheduled" and c.every == "5m"
    toml.write_text(toml.read_text().replace('coordinator_every = "5m"', 'coordinator_every = "soon"'))
    with pytest.raises(ConfigError, match="coordinator_every"):
        project.reload_config()


def test_serves_must_name_roles_the_agents_can_fill(project):
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text().replace('serves = ["worker", "reviewer"]', 'serves = ["reviewr", "planner"]'))
    with pytest.raises(ConfigError, match="reviewr.*worker, reviewer"):
        project.reload_config()


def test_an_unreachable_task_system_skips_a_scheduled_wake_up_and_is_named_in_an_event_one(project, monkeypatch, capsys):
    """A wake-up's snapshot is a query to the task system. With no answer, a wake-up that was
    only a schedule costs no turn; one caused by an event runs, told that the picture is empty
    because nothing answered, not because the project is."""
    monkeypatch.setattr(protocol, "WAIT_S", 0.3)
    harness = FakeHarness(reply="ok")
    host, store, events = host_with(project, harness)
    host._refresh_org(store.runtime()["agent_state"])
    host._wake("coordinator", ["scheduled check-in (every 5m)"])
    assert not [e for e in events.read(0)[0] if e["type"] == "session.started"]
    assert "coordinator: the task system did not answer; the scheduled wake-up is skipped" in capsys.readouterr().out
    host._wake("coordinator", ["event task.attention_needed reason=DEMO-3 is blocked"])
    assert [e["session"] for e in events.read(0)[0] if e["type"] == "session.started"] == ["coordinator"]
    prompt = harness.sessions[-1].prompts[-1] if harness.sessions[-1].prompts else host.residents.procs["coordinator"].prompts[-1]
    assert "The task system did not answer" in prompt and "not because the project is empty" in prompt
    assert "DIAGNOSIS" not in prompt and "## Execution health" not in prompt


def test_a_supervisor_chat_and_a_wake_up_take_turns(project, monkeypatch):
    """One turn at a time on the Supervisor's session: a chat registers like a wake-up, so a
    queued wake-up waits behind it, a restart waits for it, and a paused Supervisor refuses."""
    from orchestra_agents.store import StoreError
    monkeypatch.setattr(protocol, "WAIT_S", 0.3)                          # no task system here: the snapshot gives up quickly
    host, store, events = host_with(project, FakeHarness())              # turns block until the session is killed
    host._refresh_org(store.runtime()["agent_state"])
    sup = host.residents.procs["supervisor"]
    out: dict = {}
    chat = threading.Thread(target=lambda: out.setdefault("r", host.prompt_supervisor("hello", lambda ev: None)), daemon=True)
    chat.start()
    end = time.time() + 10
    while time.time() < end and (store.find_session("supervisor") or {}).get("status") != "running":
        time.sleep(0.05)
    assert "supervisor" in host.agent_threads                             # the chat stands where a wake-up would
    store.set_agent_state("supervisor", restart_requested=True)
    host.residents.stop_changed(host.agents, store.runtime()["agent_state"])
    assert host.residents.procs["supervisor"] is sup and sup.alive()      # not closed mid-reply
    store.set_agent_state("supervisor", restart_requested=False)
    store.add_wakeup("supervisor", "escalation from the coordinator")
    host.tick()
    time.sleep(0.3)
    assert len(wait_for(events, "session.started")) == 1                  # the wake-up waits for the chat
    assert store.runtime()["wakeups"] and chat.is_alive()
    sup.kill()                                                            # the chat's turn ends
    chat.join(10)
    assert not chat.is_alive() and "supervisor" not in host.agent_threads
    host.tick()
    host.agent_threads["supervisor"].join(10)
    order = [e["type"] for e in events.read(0)[0] if e.get("session") == "supervisor" and e["type"] in ("session.started", "session.settled")]
    assert order == ["session.started", "session.settled", "session.started", "session.settled"]
    store.set_agent_state("supervisor", paused=True, crash_loop="3 crashes in 15 minutes")
    with pytest.raises(StoreError, match="paused .3 crashes"):
        host.prompt_supervisor("anyone there?", lambda ev: None)


def test_a_per_ticket_instance_announces_itself_once_per_source_not_per_session(project, tmp_path):
    host, _store, events = host_with(project, FakeHarness(reply="ok"))
    for n in (1, 2):                                                  # two tickets, one after the other, on the same slot
        (tmp_path / f"w{n}").mkdir()
        assigned(events, f"a{n}", f"T-{n}", tmp_path / f"w{n}")
        host.runner.tick()
        host.runner.threads[f"a{n}"].join(20)
    started = [e for e in events.read(0)[0] if e["type"] == "orchestra.machine.started"]
    assert [e["source"] for e in started] == ["worker/1"]            # the slot is one machine instance, announced once
    assert len([e for e in events.read(0)[0] if e["type"] == "task.reported"]) == 2


def test_a_wake_ups_snapshot_query_comes_from_the_agent_woken(project):
    from fake_tasks import FakeTasks
    host, store, events = host_with(project, FakeHarness(reply="ok"))
    host._refresh_org(store.runtime()["agent_state"])
    with FakeTasks(project):
        host._wake("coordinator", ["task.attention_needed: T-1 is blocked"])
    q = [e for e in events.read(0)[0] if e["type"] == "task.query_requested"]
    assert [(e["source"], e["op"], e["by"]) for e in q] == [("coordinator", "snapshot", "coordinator")]


def test_a_process_orchestra_gave_up_on_wakes_the_supervisor_with_its_log(project):
    """A collector that crashes at runtime lands, fails, and is given up on by Orchestra's
    launcher; the Supervisor is woken with what to read, so it can file a bug ticket."""
    host, store, events = host_with(project, FakeHarness(reply="ok"))
    host._refresh_org(store.runtime()["agent_state"])
    events.publish("orchestra.process.gave_up", {"name": "repo_stats", "code": 1, "failures": 5}, source="orchestra-host")
    host._wake_on_events()
    [w] = store.runtime()["wakeups"]
    assert w["target"] == "supervisor"
    log = project.orchestra.logs_dir / "machines" / "repo_stats.log"
    assert "process repo_stats" in w["reason"] and "exit code 1" in w["reason"] and str(log) in w["reason"]


def test_an_instance_that_loses_the_race_on_the_bus_starts_no_session(project, tmp_path):
    """Another machine's claim is on the bus before this instance's: it reads that, opens no
    session (nothing spent) and says nothing more; nobody had to tell it."""
    harness = FakeHarness(reply="done")
    host, _store, events = host_with(project, harness)
    (tmp_path / "w").mkdir()
    assigned(events, "a1", "T-1", tmp_path / "w")
    events.publish("task.accepted", {"assignment": "a1", "ticket": "T-1", "role": "worker", "instance": "worker/shell"},
                   source="worker/shell", subject="T-1")
    host.runner.tick()
    host.runner.threads["a1"].join(20)
    evs = events.read(0)[0]
    assert [e["source"] for e in evs if e["type"] == "task.accepted"] == ["worker/shell", "worker/1"]
    assert harness.sessions == [] and not [e for e in evs if e["type"] in ("session.started", "task.reported", "task.released")]
    assert host._slots == {} and host.runner.released == {}


def finishing(host, report):
    """Every turn ends with ``report``, as if the agent had called its exit tool."""
    host.turns.prompt_with_retry = lambda proc, tid, task, nudge, released: (report, "", False)


def test_rejected_work_comes_back_to_the_same_slot_and_session(project, tmp_path):
    """A worker that asked for a review keeps its slot (worker/1) while the work is out, so a new
    ticket takes worker/2; changes requested of worker/1 are taken by worker/1 in the ticket's
    session; changes requested of another instance are not taken here."""
    harness = FakeHarness(reply="done")
    host, _store, events = host_with(project, harness)
    finishing(host, {"state": "READY_FOR_REVIEW", "summary": "wrote it"})
    for n in (1, 2):
        (tmp_path / f"w{n}").mkdir()
    assigned(events, "a1", "T-1", tmp_path / "w1")
    host.runner.tick()
    host.runner.threads["a1"].join(20)
    [req] = [e for e in events.read(0)[0] if e["type"] == "review.requested"]
    assert req["source"] == "worker/1" and req["ends"] == "a1" and req["role"] == "reviewer" and req["review"]["worker"] == "worker/1"
    assert req["review"]["summary"] == "wrote it" and req["review"]["commit"] is None
    assert host._slots == {"worker-T-1": "worker/1"}                         # kept while the work is out
    assigned(events, "a2", "T-2", tmp_path / "w2")
    host.runner.tick()
    host.runner.threads["a2"].join(20)
    assert [e["source"] for e in events.read(0)[0] if e["type"] == "task.accepted" and e["assignment"] == "a2"] == ["worker/2"]
    back = {"role": "worker", "ticket": "T-1", "workspace": str(tmp_path / "w1"), "branch": None, "lease_s": 180, "ends": req["assignment"],
            "brief": {"id": "T-1", "title": "t", "feedback": "1. more", "rejected_by": "reviewer"}, "feedback": "1. more"}
    events.publish("changes.requested", {**back, "assignment": "c9", "to": "worker/9"}, source="reviewer/1", subject="T-1")
    events.publish("changes.requested", {**back, "assignment": "c1", "to": "worker/1"}, source="reviewer/1", subject="T-1")
    host.runner.tick()
    assert "c9" not in host.runner.threads                                    # for another instance: not taken
    host.runner.threads["c1"].join(20)
    claims = [(e["assignment"], e["source"]) for e in events.read(0)[0] if e["type"] == "task.accepted"]
    assert ("c1", "worker/1") in claims and not [c for c in claims if c[0] == "c9"]
    worked = [s.spec.session_id for s in harness.sessions if s.spec.role == "worker"]
    assert worked == ["worker-T-1", "worker-T-2", "worker-T-1"]                # the same session, re-engaged
    assert len([e for e in events.read(0)[0] if e["type"] == "review.requested"]) == 3
    events.publish("gate.landed", {"ticket": "T-1", "gate_run": "g1"}, source="merge_gate", subject="T-1")
    host.runner.tick()
    assert "worker-T-1" not in host._slots                                    # the ticket ended: the slot is free
