"""T2f s16: "what changed since yesterday?" left out what waited on the owner: the Slack token the
Machines card asked for, and the go-ahead the Supervisor itself had asked for on the 09:00
schedule. read_events with since now carries ``waiting_on_owner`` and the prompt says to name it
before the model line."""

import json

from design_fixtures import call

from orchestra_agents import bus_reads, machines_read, outbox_read, owner_waits, prompting
from orchestra_agents.org import built_in_agents

pytest_plugins = ["design_fixtures"]

T0 = 1_762_080_000.0
SEAM = prompting.CHAT_SEAM
ASKED = ("The Review Nudger is declared to run at 09:00 America/Los_Angeles (your machine's clock). It is not running at "
         "that time yet. Do you want me to write the 09:00 schedule into the repository? I won't change the repository "
         "without your go-ahead.")


def u(text, t):
    return {"role": "user", "text": text, "tool_calls": [], "ts": T0 + t}


def a(text, t):
    return {"role": "assistant", "text": text, "tool_calls": [], "ts": T0 + t}


def transcript(project, store, msgs, status="running"):
    f = project.state_dir / "supervisor.jsonl"
    f.write_text("".join(json.dumps(m) + "\n" for m in msgs), encoding="utf-8")
    store.set_session("supervisor", kind="supervisor", harness="dummy", session_file=str(f), status=status, started_at=T0)


def test_what_changed_since_names_what_waits_on_the_owner(project, store, monkeypatch):
    monkeypatch.setattr(bus_reads, "recent", lambda store, n=bus_reads.SCAN: [])
    monkeypatch.setattr(machines_read, "view", lambda store: {"ok": True, "machines": [
        {"id": "slack_notify", "kind": "program", "mode": "continuous", "active": True,
         "about": {"disabled": "needs a bot token (xoxb-…) with channels:read, channels:history, chat:write"}},
        {"id": "sail", "kind": "program", "mode": "ad hoc", "about": {"fake": "FAKE"}}]})
    monkeypatch.setattr(outbox_read, "fetch", lambda project: {"ok": True, "running": True, "recent": [], "waiting": [
        {"id": "d7", "state": "drafted", "kind": "live.nudge", "target": {"pr": 29}, "drafter": "review_nudger/1"}]})
    transcript(project, store, [
        u(f"{SEAM}What is your zone? Set 09:00.", 0), a("Declared at 09:00. What is your zone?", 5),
        u(f"{SEAM}This machine's clock.", 60), a(ASKED, 70),
        u(f"{SEAM}Remind the reviewer on pull request #29, SCRUM-31's.", 300), a("I started the reminder.", 310),
        u(f"{SEAM}What changed since yesterday?", 2000)])
    out = call(project, store, "read_events", since="1d", order="oldest")
    w = out["waiting_on_owner"]
    assert w["machines"] == ["slack_notify: needs a bot token (xoxb-…) with channels:read, channels:history, chat:write"]
    assert [d["id"] for d in w["outbox"]] == ["d7"] and w["outbox"][0]["how"] == "waiting for the owner"
    assert w["questions"] == [
        {"asked_at": T0, "questions": ["What is your zone?"], "owner_said_next": {"message": "This machine's clock."}},
        {"asked_at": T0 + 60, "questions": ["Do you want me to write the 09:00 schedule into the repository?"],
         "owner_said_next": {"message": "Remind the reviewer on pull request #29, SCRUM-31's."}}]
    assert list(out).index("waiting_on_owner") < list(out).index("model")
    assert "waiting_on_owner" not in call(project, store, "read_events", minutes=60)


def test_a_question_asked_last_turn_is_followed_by_this_turns_message(project, store):
    transcript(project, store, [u(f"{SEAM}Set it.", 0), a("Shall I write it to the repository?", 5),
                                u(f"{SEAM}What changed since yesterday?", 900)])
    [q] = owner_waits.questions(store)
    assert q["owner_said_next"] == {"message": "What changed since yesterday?", "this_turn": True}


def test_a_part_that_was_not_read_says_so(project, store, monkeypatch):
    def down(store):
        raise RuntimeError("down")
    monkeypatch.setattr(machines_read, "view", down)
    w = owner_waits.waiting_on_owner(project, store)
    assert w["machines"] == "not read (RuntimeError)"
    assert w["outbox"].startswith("not read: ") and w["questions"] == []


def test_the_prompt_names_what_waits_on_the_owner_before_the_model_line(project):
    text = " ".join(prompting.agent_prompt(project, built_in_agents()["supervisor"]).split())
    said = text.index("before the model line, name what waits on the owner now, from the answer's `waiting_on_owner`")
    assert "a go-ahead you asked for and did not get still waits" in text
    assert said < text.index("End the answer with the model's state")
