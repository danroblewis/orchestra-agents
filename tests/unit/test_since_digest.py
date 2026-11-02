"""T2g s16: given every fact in one answer, the Supervisor still said "#33 and #34 opened at 04:57"
(04:16), "all three merged at 05:12" (one was), "2 opened, 2 closed" (three tickets went Done), and
left out the triage verdict and the prediction's state. read_events with since now writes the
answer itself (``digest``); these replay T2g's events (raw/snap-s16, the bus's own records, with
the long fields and the Jira account cut) and read the digest's lines."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from design_fixtures import call

from orchestra_agents import (
    bus_reads,
    clock,
    intended_api,
    machines_read,
    modeling,
    outbox_read,
    since_digest,
)

pytest_plugins = ["design_fixtures"]

DAY = [json.loads(x) for x in (Path(__file__).parent / "data" / "t2g_s16_events.jsonl").read_text().splitlines()]
NOW = datetime.fromisoformat("2026-11-02T13:34:12Z").timestamp()       # when the owner asked
MODEL = ("model v3, changed by discovery (discovered: the structure changed (kalman_lna)) at 2026-11-02 13:23:40Z; "
         "it has 4 stocks and 9 flows")
PREDICTION = {"id": "4eee08a55e174447b1e34477651caabf", "source": "supervisor", "subject": "machine:review_nudger",
              "expect": [{"kind": "factor", "flow": "SAILED", "factor": 1.2}], "horizon_s": 604800.0,
              "status": "waiting", "era": None}
C8 = {"change": "c8.0", "set": {"engagement": {"mode": "scheduled", "at": "09:00", "tz": "America/Los_Angeles"}},
      "add": {}, "remove": {}}


class Reads:
    def predictions(self, pid=None):
        return {"ok": True, "predictions": [PREDICTION]}


def t2g(project, store, monkeypatch) -> dict:
    monkeypatch.setattr(bus_reads, "recent", lambda store, n=bus_reads.SCAN: DAY)
    monkeypatch.setattr(bus_reads, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.setattr(clock, "host_zone", lambda: "America/Los_Angeles")
    monkeypatch.setattr(modeling, "reads", lambda project: Reads())
    monkeypatch.setattr(modeling, "model_line", lambda project, start: MODEL)
    monkeypatch.setattr(outbox_read, "fetch", lambda project: {
        "ok": True, "running": True, "waiting": [],
        "recent": [{"id": f"d{i}", "state": "sent", "sent_at": NOW - 3600} for i in range(12)]})
    monkeypatch.setattr(machines_read, "view", lambda store: {"ok": True, "machines": [
        {"id": "slack_notify", "kind": "program", "mode": "continuous", "active": True,
         "about": {"disabled": "needs a bot token (xoxb-…) with chat:write"}}]})
    monkeypatch.setattr(intended_api, "current", lambda project: {"machines": {
        "review_nudger": {"pending": [C8]}, "worker": {"pending": []}}})
    return call(project, store, "read_events", since="24h", order="oldest")


def paragraph(digest: str, head: str) -> str:
    return next(p for p in digest.split("\n\n") if p.startswith(head))


def test_the_digest_tells_t2g_s16_as_it_happened(project, store, monkeypatch):
    out = t2g(project, store, monkeypatch)
    d = out["digest"]
    assert d.startswith("Since Sun 1 Nov 05:34 (all times PST, America/Los_Angeles):")
    assert d.count("PST") == 1                                          # the zone named once
    tickets = paragraph(d, "Tickets:")
    assert "3 closed (Done): SCRUM-36 at 04:56, SCRUM-35 at 05:07, SCRUM-37 (through the Outbox) at 05:17" in tickets
    assert "3 opened: SCRUM-35 and SCRUM-36 at 04:11, SCRUM-37 (through the Outbox) at 04:57" in tickets
    prs = paragraph(d, "Pull requests:")
    assert "3 opened: #33 (SCRUM-35) and #34 (SCRUM-36) at 04:16, #35 (SCRUM-37) at 04:57;" in prs
    assert "3 merged: #34 (SCRUM-36) at 04:51, #33 (SCRUM-35) at 05:01, #35 (SCRUM-37) at 05:12;" in prs
    assert prs.count("at 05:12") == 1                                   # one merge at 05:12, not three
    assert "1 rejected (labelled REJECTED): #34 (SCRUM-36) at 04:31" in prs
    assert ("1 triage verdict: SCRUM-36 (#34) at 04:36: local (SCRUM-36: no incident open on PSVC003: the failure is "
            "the item's own; back to the work)") in prs
    assert "sail.check failed once: #34 (SCRUM-36) at 04:31 (the fake SAIL's forced failure (--reject-first))" in d
    assert "Incidents: 1 opened, 1 resolved: PT224836 (PSVC003, high) opened at 05:07, resolved at 05:12." in d
    outbox = paragraph(d, "The Outbox:")
    assert outbox.startswith("The Outbox: 12 sent:") and "11 approved by configuration, 1 by you (live.nudge at 04:54)" in outbox
    assert outbox.endswith("Its state now: running; 12 sent since then, 0 waiting.")
    items = paragraph(d, "Items:")
    assert "3 started:" in items and "delivery_live-cfdbd4 (SCRUM-37, #35) by you at 04:57" in items
    assert "5 closed:" in items and "delivery_live-cfdbd4 (SCRUM-37, #35) at 05:17 (close_ticket: sent)" in items
    waits = paragraph(d, "Waiting on you:")
    assert "slack_notify is disabled (needs a bot token (xoxb-…) with chat:write)" in waits
    assert ("review_nudger's change c8.0 (set engagement (mode scheduled, at 09:00, tz America/Los_Angeles)) waits for "
            "your confirm on the Machines page") in waits
    assert paragraph(d, "Predictions") == (
        "Predictions (1): 4eee08a55e174447b1e34477651caabf by supervisor about machine:review_nudger (SAILED x1.2): "
        "waiting for its change to begin an era, judged 168 h after it begins.")
    assert d.splitlines()[-1] == out["closing_line"] == MODEL           # the model line last, verbatim
    order = [d.index(h) for h in ("Tickets:", "Pull requests:", "Checks:", "Labels:", "Incidents:", "The Outbox:",
                                  "Items:", "Waiting on you:", "Predictions", MODEL)]
    assert order == sorted(order)
    assert "digest" not in call(project, store, "read_events", minutes=60)


def test_nothing_waiting_and_failed_reads_are_said_so():
    d = since_digest.digest([], NOW - 3600, NOW, "UTC", waiting={"machines": [], "outbox": [], "changes": [], "questions": []},
                            preds="not read (500)", model="the model could not be read (500)")
    assert d.splitlines()[0] == "Since Mon 2 Nov 12:34 (all times UTC): 0 events on the bus."
    assert "Waiting on you: nothing." in d and "Predictions: not read (500)." in d
    w = since_digest.owner({"machines": "not read (OSError)", "outbox": [], "changes": [], "questions": []},
                           since_digest.Clock("UTC", NOW))
    assert w == "Waiting on you: the machines were not read (OSError)." and "nothing" not in w


def test_a_question_asked_is_told_with_what_the_owner_said_next():
    w = {"machines": [], "outbox": [], "changes": [], "questions": [
        {"asked_at": NOW - 600, "questions": ["Shall I write it?"], "owner_said_next": {"message": "Remind the reviewer."}},
        {"asked_at": NOW - 60, "questions": ["Confirm it?"], "owner_said_next": {"message": "what changed?", "this_turn": True}}]}
    text = since_digest.owner(w, since_digest.Clock("UTC", NOW))
    assert "I asked at 13:24: \"Shall I write it?\"; what you said next: \"Remind the reviewer.\"" in text
    assert "I asked at 13:33: \"Confirm it?\"; your message now is the first since" in text


def test_the_prompt_replies_with_the_digest_as_returned_and_never_guesses_a_cause(project, monkeypatch):
    """T2g s16 (the digest), and s08 and s15: "the stand-in may have stopped", "the incident may be
    holding SAIL", each a hedged wrong guess."""
    from orchestra_agents import installed, prompting
    from orchestra_agents.org import built_in_agents
    for on in (False, True):
        monkeypatch.setattr(installed, "tasking", lambda on=on: on)
        text = " ".join(prompting.agent_prompt(project, built_in_agents()["supervisor"]).split())
        assert ("call `read_events` once, with `since` (the time, or `24h`), and reply with its `digest` exactly as "
                "returned: no rewording, no additions, no omissions, then nothing") in text
        assert "its last line is the read's `closing_line`, the model line" in text
        assert "copy them, never count the events yourself" not in text          # the old rule is gone
        assert ("Never guess at a cause: when the tools do not say why something happened or waits, say \"the tools "
                "do not say why\", never a likely cause, a hypothesis or what something \"may\" be doing") in text
