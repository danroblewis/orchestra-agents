"""T2f s16: "what changed since yesterday?" left out what waited on the owner: the Slack token the
Machines card asked for, and the go-ahead the Supervisor itself had asked for on the 09:00
schedule. read_events with since now carries ``waiting_on_owner`` and the prompt says to name it
before the model line."""

import json
from pathlib import Path

import pytest
from design_fixtures import call

from orchestra_agents import bus_reads, machines_read, outbox_read, owner_waits, prompting

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
        u(f"{SEAM}This machine's clock is my zone.", 60), a(ASKED, 70),
        u(f"{SEAM}Remind the reviewer on pull request #29, SCRUM-31's.", 300), a("I started the reminder.", 310),
        u(f"{SEAM}What changed since yesterday?", 2000)])
    out = call(project, store, "read_events", since="1d", order="oldest")
    w = out["waiting_on_owner"]
    assert w["machines"] == ["slack_notify: needs a bot token (xoxb-…) with channels:read, channels:history, chat:write"]
    assert [d["id"] for d in w["outbox"]] == ["d7"] and w["outbox"][0]["how"] == "waiting for the owner"
    assert w["questions"] == [                    # the zone question was answered: the message names its subject
        {"asked_at": T0 + 60, "question": "Do you want me to write the 09:00 schedule into the repository?",
         "owner_said_next": {"message": "Remind the reviewer on pull request #29, SCRUM-31's."}}]
    assert list(out).index("waiting_on_owner") < list(out).index("model")
    assert "waiting_on_owner" not in call(project, store, "read_events", minutes=60)


def test_a_question_asked_last_turn_is_followed_by_this_turns_message(project, store):
    transcript(project, store, [u(f"{SEAM}Set it.", 0), a("Shall I write it to the repository?", 5),
                                u(f"{SEAM}What changed since yesterday?", 900)])
    [q] = owner_waits.questions(store)
    assert q["question"] == "Shall I write it to the repository?"
    assert q["owner_said_next"] == {"message": "What changed since yesterday?", "this_turn": True}


def test_a_part_that_was_not_read_says_so(project, store, monkeypatch):
    def down(store):
        raise RuntimeError("down")
    monkeypatch.setattr(machines_read, "view", down)
    w = owner_waits.waiting_on_owner(project, store)
    assert w["machines"] == "not read (RuntimeError)"
    assert w["outbox"].startswith("not read: ") and w["questions"] == []


def replay(project, store, steps, until):
    """T2h's turns (tests/unit/data, from the walkthrough's raw/answer-*.json): each owner message
    and the Supervisor's reply, up to and including the owner's message at step ``until``."""
    msgs = []
    for s in steps:
        msgs.append(u(f"{SEAM}{s['message']}", s["sent_at"] - T0))
        if s["step"] == until:
            break
        msgs.append(a(s["reply"], s["sent_at"] - T0 + s["seconds"]))
    transcript(project, store, msgs)


T2H = json.loads((Path(__file__).parent / "data" / "t2h_supervisor_turns.json").read_text(encoding="utf-8"))


def test_t2h_the_copy_question_asked_as_choices_still_waits_at_step_16(project, store):
    """T2h s16: waiting_on_owner listed only the Slack token. The Supervisor asked at s10 (and again
    at s10b) which copy to write, as a numbered list of choices with no question mark; the owner's
    next messages ("This machine's clock.", "Remind the reviewer on pull request #38") named none."""
    replay(project, store, T2H, "s16")
    [q] = owner_waits.questions(store)
    s10b = next(s for s in T2H if s["step"] == "s10b")
    assert q["asked_at"] == s10b["sent_at"] and q["question"] == "To unblock it, you need to pick one"
    assert [c.split(",")[0] for c in q["choices"]] == ["Commit the repository file", "Discard its changes",
                                                      'Say "write it to the repository"']
    assert q["owner_said_next"] == {"message": "Remind the reviewer on pull request #38, SCRUM-38's."}


@pytest.mark.parametrize("said", ["Write it to the repository.", "Commit it.", "2", "Option 3"])
def test_naming_a_choice_answers_it(project, store, said):
    steps = [s for s in T2H if s["step"] in ("s10", "s10b")] + [
        {"step": "s11", "message": said, "sent_at": T2H[4]["sent_at"], "seconds": 5, "reply": "Done."}]
    replay(project, store, steps, "never")
    assert owner_waits.questions(store) == []


def test_a_message_on_another_subject_leaves_a_question_open():
    ask = {"question": "Do you want me to write the 09:00 schedule into the repository?"}
    assert not owner_waits.answers("What needs me before I go?", ask)
    assert owner_waits.answers("Yes, put the schedule in.", ask)
