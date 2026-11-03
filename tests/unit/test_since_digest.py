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


def t2g(project, store, monkeypatch, **more) -> dict:
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
    return call(project, store, "read_events", since="24h", order="oldest", **more)


def paragraph(digest: str, head: str) -> str:
    return next(p for p in digest.split("\n\n") if p.startswith(head))


def test_the_digest_tells_t2g_s16_as_it_happened(project, store, monkeypatch):
    out = t2g(project, store, monkeypatch, full=True)                  # every list named in full (the owner's "more")
    d = out["digest"]
    assert d.startswith("Since Sun 1 Nov 05:34 (all times PST, America/Los_Angeles):")
    assert d.count("PST") == 1                                          # the zone named once
    tickets = paragraph(d, "Tickets:")
    assert "3 closed (Done): SCRUM-37 (through the Outbox) at 05:17, SCRUM-35 at 05:07, SCRUM-36 at 04:56" in tickets
    assert "3 opened: SCRUM-37 (through the Outbox) at 04:57, SCRUM-36 and SCRUM-35 at 04:11" in tickets
    prs = paragraph(d, "Pull requests:")
    assert "3 opened: #35 (SCRUM-37) at 04:57, #34 (SCRUM-36) and #33 (SCRUM-35) at 04:16;" in prs
    assert "3 merged: #35 (SCRUM-37) at 05:12, #33 (SCRUM-35) at 05:01, #34 (SCRUM-36) at 04:51;" in prs
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
        {"asked_at": NOW - 600, "question": "Shall I write it?", "owner_said_next": {"message": "Remind the reviewer."}},
        {"asked_at": NOW - 60, "question": "Confirm it?", "owner_said_next": {"message": "what changed?", "this_turn": True}}]}
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


def ev(ty: str, at: float, subject: str, **data) -> dict:
    return {"type": ty, "ts": datetime.fromtimestamp(at).astimezone().isoformat(), "subject": subject,
            "source": "x", "data": data}


def board(days: int = 3, tickets: int = 24) -> list[dict]:
    """A sprint board's days: each ticket created, assigned, its pull request opened, labelled,
    checked, merged and labelled again, then Done; no anomaly among them."""
    out, start = [], NOW - days * 86400
    for i in range(tickets):
        t, key, n = start + i * (days * 86400 / tickets), f"SCRUM-{100 + i}", 200 + i
        out += [ev("jira.issue.created", t, key, status="To Do"), ev("jira.issue.assigned", t + 5, key),
                ev("github.pr.opened", t + 300, key, number=n), ev("github.pr.labeled", t + 600, key, number=n, label="SAIL"),
                ev("sail.check.completed", t + 900, key, number=n, outcome="success"),
                ev("github.pr.merged", t + 1200, key, number=n),
                ev("github.pr.labeled", t + 1203, key, number=n, label="SAILED"),
                ev("jira.issue.transitioned", t + 1500, key, to="Done"), ev("offer.finished", t + 1500, key)]
    return out


WAIT = {"machines": [], "outbox": [], "changes": [], "questions": []}


def test_three_days_of_the_board_read_at_a_glance_and_keep_their_counts():
    """R3 (b O6, e O4): s16 ran 3,825 characters for three tickets; "very many jira tickets"."""
    d = since_digest.digest(board(), NOW - 3 * 86400, NOW, "UTC", waiting=WAIT, preds=[], model="M")
    assert len(d) <= since_digest.LIMIT
    assert "Look first" not in d                                     # nothing wrong, nothing flagged
    tickets = paragraph(d, "Tickets:")
    assert "24 opened: SCRUM-123 at 10:34, SCRUM-122 at 07:34" in tickets            # the newest first (T2j)
    assert "24 closed (Done):" in tickets and "… and 21 more." in tickets
    assert "24 merged:" in paragraph(d, "Pull requests:")
    t2g_day = since_digest.digest(DAY, NOW - 86400, NOW, "America/Los_Angeles", waiting=WAIT, preds=[], model="M")
    assert len(t2g_day) <= since_digest.LIMIT and "3 Done: SCRUM-37 (through the Outbox), SCRUM-35, SCRUM-36" in t2g_day
    assert "PT224836 (PSVC003, high)" in paragraph(t2g_day, "Incidents:")                       # step 16 (d) keeps it


def test_a_small_window_names_everything():
    few = board(days=1, tickets=2)
    d = since_digest.digest(few, NOW - 86400, NOW, "UTC", waiting=WAIT, preds=[], model="M")
    assert "more" not in d and "SCRUM-100" in d and "SCRUM-101" in d


def test_the_anomalies_come_first():
    """R3 b (O1-O3): a Done ticket's new pull request, REJECTED on a merged one, attention hours
    old, an item still waiting on a pull request closed unmerged: flagged before the counts."""
    t = NOW - 4 * 3600
    events = board(days=1, tickets=2) + [
        ev("jira.issue.created", t, "SCRUM-42", status="Done"),                 # a replay of a Done ticket
        ev("github.pr.opened", t + 60, "SCRUM-42", number=47),
        ev("offer.made", t + 61, "SCRUM-42", step="write"),
        ev("github.pr.labeled", NOW - 1800, "SCRUM-100", number=200, label="REJECTED"),   # merged a day ago
        ev("attention.needed", t, "SCRUM-7", reason="not past step ci within 30 min"),
        ev("attention.needed", NOW - 600, "SCRUM-8", reason="fresh"),
        ev("attention.needed", t, "SCRUM-9", reason="closed since"), ev("item.closed", t + 60, "SCRUM-9"),
        ev("github.pr.opened", t, "SCRUM-7", number=43), ev("github.pr.closed", t + 120, "SCRUM-7", number=43),
    ]
    d = since_digest.digest(events, NOW - 86400, NOW, "UTC", waiting=WAIT, preds=[], model="M")
    paras = d.split("\n\n")
    look = paras[1]
    assert look.startswith("Look first:\n- New work on a ticket already Done: SCRUM-42 (Done at ")
    assert "#47 opened at" in look and "1 offer" in look
    assert "- A label on a merged pull request: REJECTED on #200 (SCRUM-100) at" in look
    assert "SAILED" not in look                                    # put on seconds after the merge: not this
    assert "- Attention over an hour old, the item open: SCRUM-7 (#43) since" in look
    assert "SCRUM-8" not in look and "SCRUM-9" not in look
    assert "- An open item whose pull request closed unmerged: SCRUM-7 (#43) on #43 (closed at" in look
    assert paras[2].startswith("Tickets:")


def test_items_open_from_a_start_event_are_never_none_started():
    """Bootstrap run 3: five tickets opened items by their assignment (a start event, no item event)
    and the digest said "Items: none started". The Items line counts the plugin's open items, and an
    ``item.opened`` on the bus counts as started."""
    t = NOW - 3600
    jira = [ev("jira.issue.assigned", t + i, f"HK-{102 + i}") for i in range(5)]
    d = since_digest.digest(jira, NOW - 86400, NOW, "UTC", waiting=WAIT, preds=[], model="M", open_items=5)
    line = paragraph(d, "Items:")
    assert "none started" not in line and "5 open now" in line
    d = since_digest.digest([*jira, ev("item.opened", t + 9, "HK-102")], NOW - 86400, NOW, "UTC", waiting=WAIT, preds=[],
                            model="M", open_items=5)
    assert "1 started: HK-102" in paragraph(d, "Items:") and "5 open now" in paragraph(d, "Items:")
    d = since_digest.digest(jira, NOW - 86400, NOW, "UTC", waiting=WAIT, preds=[], model="M", open_items=0)
    assert "Items:" not in d                                         # nothing open, nothing said
