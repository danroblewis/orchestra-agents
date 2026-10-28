"""A transcript read as turns, and the ticket drawer's agent sessions: every worker turn, every
review round, and the other agents' turns about the ticket, each with its outcome, live or
archived (session_turns.py; GET /sessions?ticket= and /sessions/<id>/messages)."""

import json
import subprocess

import pytest
from conftest import agents_project
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestra_agents import web
from orchestra_agents.session_turns import outcome, split, ticket_sessions
from orchestra_agents.store import AgentStore

T0 = 1_700_000_000.0


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo, harness="dummy")


def u(text, t):
    return {"role": "user", "text": text, "tool_calls": [], "ts": T0 + t}


def a(text, t, *calls):
    return {"role": "assistant", "text": text, "tool_calls": [{"name": n, "args": args} for n, args in calls], "ts": T0 + t}


def r(text, t, name="tool"):
    return {"role": "toolResult", "text": text, "tool_calls": [], "ts": T0 + t, "tool_name": name, "is_error": False}


def write(project, store, sid, msgs, **rec):
    f = project.state_dir / f"{sid}.jsonl"
    f.write_text("".join(json.dumps(m) + "\n" for m in msgs), encoding="utf-8")
    store.set_session(sid, harness="dummy", session_file=str(f), **rec)


WORKER = [u("# Your assignment\n\nTicket: T-1", 0), a("Writing it.", 1), a("", 2, ("shell", {"command": "make"})), r("ok", 3, "shell"),
          a("", 4, ("report_ticket", {"state": "READY_FOR_REVIEW", "summary": "done"})), r("recorded", 5, "report_ticket"), a("Reported.", 6),
          u("# Your assignment\n\nTicket: T-1 (again, after a rejection)", 20), a("Stopping.", 21),
          u("You stopped without reporting. Call `report_ticket` now", 22), a("", 23, ("report_ticket", {"state": "BLOCKED"}))]
REVIEWER = [u("# Review request\n\nTicket: T-1", 10), a("", 11, ("report_review", {"verdict": "REJECTED"})), a("Rejected.", 12),
            u("# Review request\n\nTicket: T-1", 30), a("Reading.", 31)]
SNAPSHOT = "## Project snapshot\nTickets:\n- T-1 OPEN p5 M1 one\n- T-2 OPEN p5 M1 two\n"
COORDINATOR = [u(f"# Wake-up for the coordinator\n\n## Why you were woken\n- task.attention_needed: T-1 BLOCKED by worker\n\n{SNAPSHOT}", 25),
               a("", 26, ("unblock_ticket", {"id": "T-1", "note": "try again"})), a("Unblocked T-1.", 27),
               u(f"# Wake-up for the coordinator\n\n## Why you were woken\n- scheduled check-in\n\n{SNAPSHOT}", 40), a("Nothing to do.", 41),
               u(f"# Wake-up for the coordinator\n\n## Why you were woken\n- scheduled check-in\n\n{SNAPSHOT}", 50), a("T-10 looks stuck.", 51)]


def test_turns_start_at_prompts_and_a_nudge_stays_on_its_turn():
    turns = split(WORKER)
    assert [(t["start"], t["end"], t["kind"]) for t in turns] == [(0, 7, "assignment"), (7, 11, "assignment")]
    assert turns[0]["reply"] == "Reported." and turns[0]["reply_index"] == 6
    assert outcome(turns[0], [], False) == "READY_FOR_REVIEW" and outcome(turns[1], [], False) == "BLOCKED"


def test_a_turn_the_host_stopped_says_so():
    t = split([u("# Review request\n\nTicket: T-1", 0), a("Reading the diff.", 5)])[0]
    assert outcome(t, [{"type": "session.timed_out", "ts": T0 + 6}], False) == "cut off"
    assert outcome(t, [], False) == "no verdict" and outcome(t, [], True) == "running"


def test_the_ticket_lists_its_worker_turns_review_rounds_and_the_turns_about_it(project):
    store = AgentStore(project)
    write(project, store, "worker-T-1", WORKER, kind="worker", instance="worker/1", ticket_id="T-1", status="idle")
    write(project, store, "reviewer-T-1", REVIEWER, kind="reviewer", instance="reviewer/2", ticket_id="T-1", status="running",
          started_at=T0 + 29)
    write(project, store, "coordinator", COORDINATOR, kind="coordinator", status="idle")
    write(project, store, "worker-T-2", [u("# Your assignment\n\nTicket: T-2", 1)], kind="worker", ticket_id="T-2", status="idle")
    store.purge_ticket_sessions("T-1")          # a finished ticket's sessions are archived; they stay listed
    got = ticket_sessions(store, "T-1")
    assert [(x["session"], x["outcome"]) for x in got] == [
        ("worker-T-1", "READY_FOR_REVIEW"), ("reviewer-T-1", "REJECTED"), ("worker-T-1", "BLOCKED"),
        ("coordinator", "replied"), ("reviewer-T-1", "no verdict")]
    coord = got[3]
    assert coord["touched"] == ["unblock_ticket"] and coord["kind"] == "wake" and not coord["own"] and coord["reply"] == "Unblocked T-1."
    assert got[0]["instance"] == "worker/1" and got[0]["archived"] and (got[0]["start"], got[0]["end"]) == (0, 7)
    # T-10 and a snapshot that lists every ticket do not make a turn about T-1
    assert [x["session"] for x in ticket_sessions(store, "T-2")] == ["worker-T-2"]


def test_a_running_turn_is_live_until_it_ends(project):
    store = AgentStore(project)
    write(project, store, "reviewer-T-1", REVIEWER, kind="reviewer", ticket_id="T-1", status="running", started_at=T0 + 29)
    last = ticket_sessions(store, "T-1")[-1]
    assert last["running"] and last["outcome"] == "running" and last["ended"] is None
    store.set_session("reviewer-T-1", status="running", started_at=T0 + 60)   # the next round began; its prompt is not written yet
    got = ticket_sessions(store, "T-1")
    assert not got[1]["running"] and got[-1]["running"] and got[-1]["turn"] == 1 and got[-1]["end"] == 0


def test_the_web_lists_them_and_serves_one_turns_messages(project):
    store = AgentStore(project)
    write(project, store, "worker-T-1", WORKER, kind="worker", instance="worker/1", ticket_id="T-1", status="idle",
          markers=[{"type": "session.started", "ts": "2027-01-15T08:00:00+00:00"}])
    app = FastAPI()
    app.include_router(web.router(project), prefix="/api/plugins/agents")
    c = TestClient(app)
    d = c.get("/api/plugins/agents/sessions", params={"ticket": "T-1"}).json()
    assert [t["turn"] for t in d["turns"]] == [1, 2]
    m = c.get("/api/plugins/agents/sessions/worker-T-1/messages", params={"start": 7, "end": 11}).json()
    assert [x["i"] for x in m["messages"]] == [7, 8, 9, 10] and m["messages"][0]["text"].startswith("# Your assignment")
    assert c.get("/api/plugins/agents/sessions/worker-T-1/messages", params={"start": 9}).json()["messages"][-1]["i"] == 10
    assert c.get("/api/plugins/agents/sessions/nobody/messages").status_code == 404
    # the paged transcript numbers its messages too, so a page can point at one
    page = c.get("/api/plugins/agents/sessions/worker-T-1/transcript", params={"limit": 3}).json()["messages"]
    assert [x["i"] for x in page] == [10, 9, 8]
