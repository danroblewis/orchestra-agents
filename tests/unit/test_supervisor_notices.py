"""The Supervisor's notices (notices.py; the owner's standstill report, 2026-11-04): a flood of the
Workflows floor's attention.needed gathers into one wake turn per [agents] attention_every and none
past the day's cap, kept for the page and handed to the next chat turn; the owner's message gets the
next turn during a flood; a page request still wakes it at once; the owner's pause holds wake turns
and not chat; and a duty may not listen for activity."""

import subprocess
import threading
import time

import pytest
from conftest import FakeHarness, agents_project

from orchestra_agents import budget, duties, notices, protocol, wake_classes
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore, StoreError


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(protocol, "WAIT_S", 0.3)                  # no task system here: the snapshot gives up quickly
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    p = agents_project(repo)
    toml = p.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text().rstrip() + "\nreview_turns_per_day = 2\n")   # [agents] is the last table
    p.reload_config()
    return p


class OneTurnAtATime(FakeHarness):
    """Each turn blocks until ``kill``, which ends that one turn only."""

    def start(self, spec):
        s = super().start(spec)
        base = s.prompt

        def prompt(text, timeout=None, on_event=None, idle_timeout=None):
            out = base(text, timeout, on_event, idle_timeout)
            s._stopped.clear()
            return out
        s.prompt = prompt
        return s


def host_with(project, harness):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    host.harness = harness
    host._refresh_org(store.runtime()["agent_state"])
    return host, store, events


def flood(events, n=200, items=50):
    """``n`` attention.needed to the Supervisor: ``items`` items, two guards each, over and over."""
    for i in range(n):
        item, guard = f"item-{i % items}", ("unclaimed", "within")[(i // items) % 2]
        events.publish("attention.needed", {"to": "supervisor", "reason": f"{guard} passed", "workflow": "jira", "step": "triage",
                                            "guard": guard}, source="workflows/dispatch", subject=item)


def wake_turns(events):
    return [e for e in events.read(0)[0] if e["type"] == "session.started" and e.get("session") == "supervisor"]


def run_tick(host):
    host.tick()
    th = host.agent_threads.get("supervisor")
    if th is not None:
        th.join(10)


def an_interval_later(store):
    store.set_agent_state("supervisor", notice_turn_at=time.time() - 601)


def test_a_flood_is_one_wake_turn_per_interval_and_none_past_the_cap(project):
    host, store, events = host_with(project, FakeHarness(reply="ok"))
    flood(events)
    run_tick(host)
    assert len(wake_turns(events)) == 1                                   # 200 events, one turn
    prompt = host.residents.procs["supervisor"].prompts[-1]
    assert prompt.count("the Workflows floor asks") == 12 and "and 88 more" in prompt   # each item and guard once: 100
    assert "said 2 times" in prompt
    flood(events)
    for _ in range(3):
        run_tick(host)
    assert len(wake_turns(events)) == 1                                   # within the interval: held
    assert len(notices.held(store)) == 100
    an_interval_later(store)
    run_tick(host)
    assert len(wake_turns(events)) == 2
    s = budget.spent(store)
    assert s["initiative_turns"] == 2 and s["by_reason"]["attention.needed"]["turns"] == 2
    flood(events)                                                         # the cap (2 turns) is spent now
    an_interval_later(store)
    for _ in range(3):
        run_tick(host)
    assert len(wake_turns(events)) == 2                                   # none past the cap, and nothing dropped
    view = wake_classes.view(project, store, host.cfg)
    assert view["held"]["count"] == 100 and view["held"]["cap_spent"] and "review_turns_per_day" in view["held"]["why"]
    out = host.prompt_supervisor("what is going on?", lambda ev: None)    # the next chat turn is handed them
    assert out["text"] == "ok"
    chat = host.residents.procs["supervisor"].prompts[-1]
    assert "[Orchestra: 100 notice(s) held for you" in chat and "and 88 more" in chat and chat.rstrip().endswith("what is going on?")
    assert notices.held(store) == []


def test_a_chat_message_sent_during_a_flood_gets_the_next_turn(project):
    host, store, events = host_with(project, OneTurnAtATime())            # each turn blocks until its session is killed
    flood(events, n=20)
    host.tick()                                                           # the first notices' wake turn starts
    end = time.time() + 10
    while time.time() < end and (store.find_session("supervisor") or {}).get("status") != "running":
        time.sleep(0.05)
    wake = host.agent_threads["supervisor"]
    out: dict = {}
    chat = threading.Thread(target=lambda: out.setdefault("r", host.prompt_supervisor("hello", lambda ev: None)), daemon=True)
    chat.start()
    while time.time() < end and not store.runtime().get("owner_messages"):
        time.sleep(0.05)
    [msg] = store.runtime()["owner_messages"]
    assert msg["status"] == "waiting" and msg["behind"].startswith("a wake-up turn (attention.needed")
    flood(events, n=20)                                                   # more notices, and their interval passed
    an_interval_later(store)
    store.add_wakeup("supervisor", "[coordinator] an escalation meanwhile")
    host.residents.procs["supervisor"].kill()                             # the wake turn ends
    wake.join(10)
    while time.time() < end and store.runtime()["owner_messages"][0]["status"] != "answering":
        time.sleep(0.05)
    sup = host.residents.procs["supervisor"]
    while time.time() < end and len(sup.prompts) < 2:
        host.tick()                                                       # no wake turn starts before the chat's
        time.sleep(0.1)
    for _ in range(3):
        host.tick()
    assert len(sup.prompts) == 2 and sup.prompts[-1].rstrip().endswith("hello")   # the next turn is the chat's
    assert out == {} and chat.is_alive() and "supervisor" not in {n for n, t in host.agent_threads.items() if t is not chat}
    assert store.runtime()["owner_messages"][0]["status"] == "answering"
    assert [w["reason"] for w in store.runtime()["wakeups"]] == ["[coordinator] an escalation meanwhile"]   # queued, not lost
    host.residents.procs["supervisor"].kill()
    chat.join(10)
    assert len(wake_turns(events)) == 2


def test_a_page_request_still_wakes_it_at_once(project):
    host, store, events = host_with(project, FakeHarness(reply="ok"))
    flood(events, n=10)
    host._wake_on_events()
    store.drain_wakeups("supervisor")                                     # the first release used the interval
    flood(events, n=10)
    events.publish("orchestra.intent.requested", {"control": "confirm", "part": "machine", "id": "baker"}, source="owner",
                   subject="machine:baker")
    host._wake_on_events()
    [w] = store.runtime()["wakeups"]
    assert w["reason"].startswith("event orchestra.intent.requested")
    store.drain_wakeups("supervisor")
    host.cfg.review_turns_per_day = 0                                     # nor does the cap hold it
    budget.record(store, None, initiative=True)
    events.publish("orchestra.intent.requested", {"control": "discard", "part": "machine", "id": "baker"}, source="owner",
                   subject="machine:baker")
    host._wake_on_events()
    assert len(store.runtime()["wakeups"]) == 1


def test_the_owner_s_pause_holds_wake_turns_but_not_chat(project):
    host, store, events = host_with(project, FakeHarness(reply="ok"))
    notices.pause(store)
    rec = store.agent_state("supervisor")["initiative_pause"]
    assert rec["by"] == "owner" and rec["via"] == "the Agents page" and rec["day"] == budget.day()
    flood(events, n=10)
    for _ in range(2):
        run_tick(host)
    assert wake_turns(events) == [] and len(notices.held(store)) == 10
    assert "paused its own initiative" in wake_classes.view(project, store, host.cfg)["held"]["why"]
    assert host.prompt_supervisor("are you there?", lambda ev: None)["text"] == "ok"
    assert len(wake_turns(events)) == 1                                   # the chat's turn
    assert notices.paused(store, time.time() + 86_400 * 2) is None        # it ends with the day
    notices.resume(store)
    flood(events, n=10)
    run_tick(host)
    assert len(wake_turns(events)) == 2
    assert [h["act"] for h in store.agent_state("supervisor")["initiative_pauses"]] == ["pause", "resume"]


def test_the_page_control_pauses_and_resumes(project):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from orchestra_agents.web import router
    app = FastAPI()
    app.include_router(router(project), prefix="/api/plugins/agents")
    c = TestClient(app)
    assert c.post("/api/plugins/agents/supervisor/initiative", json={"pause": True}).json()["paused"]["by"] == "owner"
    w = c.get("/api/plugins/agents/supervisor/wakes").json()
    assert w["paused"] and {x["what"] for x in w["immediate"]} >= {"escalate_to_supervisor", "orchestra.intent.requested from owner"}
    assert any(x["what"].startswith("attention.needed") for x in w["coalesced"])
    assert c.post("/api/plugins/agents/supervisor/initiative", json={"pause": False}).json()["paused"] is None


def test_duty_wake_ups_are_gathered_like_the_core_s(project):
    from orchestra_agents.machine_spec import Duty, load, render
    from orchestra_agents.supervisor_spec import files, starting
    host, store, events = host_with(project, FakeHarness(reply="ok"))
    spec = load(project, "supervisor") or starting()
    for rel, body in files(spec).items():
        (project.repo_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (project.repo_root / rel).write_text(body)
    spec.duties.append(Duty(name="watch_drafts", prompt="duties/watch_drafts.md", listens=["outbox.draft.rejected"]))
    (project.repo_root / spec.dir / "machine.toml").write_text(render(spec))
    (project.repo_root / spec.dir / "duties" / "watch_drafts.md").write_text("Say why a draft was rejected.\n")
    host._refresh_org(store.runtime()["agent_state"])
    for i in range(30):
        events.publish("outbox.draft.rejected", {"draft": f"d{i % 3}"}, source="outbox", subject=f"d{i % 3}")
    host._wake_on_events()
    assert len(store.drain_wakeups("supervisor")) == 3                    # the same draft once
    for i in range(30):
        events.publish("outbox.draft.rejected", {"draft": "d9"}, source="outbox", subject="d9")
    host._wake_on_events()
    assert store.runtime()["wakeups"] == [] and len(notices.held(store)) == 1   # within the interval: held


def test_a_duty_may_not_listen_for_activity(project, monkeypatch):
    store = AgentStore(project, Events.for_project(project))
    AgentHost(project, store, store.events)                               # writes the Supervisor's spec
    from orchestra_agents.machine_spec import load
    from orchestra_agents.supervisor_spec import files, starting
    if load(project, "supervisor") is None:
        for rel, body in files(starting()).items():
            (project.repo_root / rel).parent.mkdir(parents=True, exist_ok=True)
            (project.repo_root / rel).write_text(body)
    monkeypatch.setattr(duties, "_commit", lambda *a, **k: {"ok": True})
    add = {"name": "watch_floor", "text": "Keep the floor moving.", "reason": "the owner asked for it today"}
    for busy, instead in (("offer.alive", "offer.withdrawn"), ("item.closed", "attention.needed"),
                          ("outbox.sent", "outbox.draft.rejected"), ("offer.made", "attention.needed")):
        with pytest.raises(StoreError, match=instead):
            duties.handle(project, store, "add_duty", {**add, "listens": [busy]}, "supervisor")
    with pytest.raises(StoreError, match="heartbeat"):
        duties.handle(project, store, "add_duty", {**add, "listens": ["offer.alive:item-1"]}, "supervisor")
    assert duties.handle(project, store, "add_duty", {**add, "listens": ["item.closed:item-1"]}, "supervisor")["ok"]   # narrowed
    for i in range(40):                                                   # a type busy on this bus
        store.events.publish("ci.check.completed", {"n": i}, source="ci")
    with pytest.raises(StoreError, match="40 times in the last hour"):
        duties.handle(project, store, "add_duty", {**add, "listens": ["ci.check.completed"]}, "supervisor")
    assert duties.handle(project, store, "add_duty", {**add, "listens": ["ci.check.completed:main"]}, "supervisor")["ok"]
    assert duties.handle(project, store, "add_duty", {**add, "listens": ["outbox.draft.rejected"]}, "supervisor")["ok"]
