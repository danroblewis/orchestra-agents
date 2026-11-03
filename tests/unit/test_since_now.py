"""T2j s16, the morning after on the standing demo: the reply to "What changed since yesterday?" led
with the stale events replayed at the previous day's resume, never named SCRUM-47 (12 hours at
``sail_check`` for a SAIL result that never came), named each list's oldest entry and hid the
night's tickets and pull requests, and ran about 4,000 characters. These replay the night's bus
(raw/events-all-pre.json, the window of the reply: long fields and the Jira account cut) with the
Workflows plugin's open items as its Items API had them at the ask."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from orchestra_agents import item_state, since_digest

pytest_plugins = ["design_fixtures"]

NIGHT = [json.loads(x) for x in (Path(__file__).parent / "data" / "t2j_s16_events.jsonl").read_text().splitlines()]
ASKED = datetime.fromisoformat("2026-11-03T15:54:51Z").timestamp()
ATTENTION = {"reason": "attention raised", "why": "not past step ci of delivery_live within 30 min", "by": "supervisor"}


def row(item, step, since, wait, acts=None):
    return {"item": item, "workflow": "delivery_live", "step": step, "pile": f"{step}.waiting", "wait": wait,
            "age_s": ASKED - datetime.fromisoformat(since).timestamp(), "acts": acts}


OPEN = [row("SCRUM-34", "ci", "2026-11-02T13:38:59Z", ATTENTION, {"who": "owner"}),
        row("SCRUM-41", "ci", "2026-11-02T15:49:33Z", ATTENTION, {"who": "owner"}),
        row("SCRUM-39", "ci", "2026-11-02T15:49:44Z", ATTENTION, {"who": "owner"}),
        row("SCRUM-47", "sail_check", "2026-11-03T03:59:46Z", {"reason": "awaiting", "by": "github"}),
        row("SCRUM-50", "review", "2026-11-03T15:49:57Z", {"reason": "awaiting", "by": "github"}, {"who": "owner"})]
STATE = {"rows": OPEN, "typical": {("delivery_live", "sail_check.waiting"): 380.0}, "within": {("delivery_live", "ci"): 1800.0},
         "outside": {("delivery_live", "sail_check"): "github", ("delivery_live", "review"): "github"}}
SLACK = ("slack_notify: the token at ~/.config/orchestra-bridges/slack-token is an app-configuration or rotating token "
         "(xoxe.…), which cannot post: replace it with a Slack bot token (xoxb-…: api.slack.com/apps → your app → OAuth & "
         "Permissions → Bot User OAuth Token) with the bot scopes channels:read, channels:history, chat:write")
MODEL = "There is no model yet: the last discovery found no stocks among the 283 series it looked at (nothing balances yet)."
OWN = 'The Supervisor\'s pull request: #53 open on orchestra/supervisor, "Supervisor: outbox.kinds set".'
KW = {"outbox_state": "running; 22 sent since then, 0 waiting", "preds": [], "model": MODEL, "own_pr": OWN,
      "waiting": {"machines": [SLACK], "outbox": [], "changes": [], "questions": []}, "open_items": 5,
      "ages": "Machines by age: 1 agent, 3 programs in .orchestra/, 4 graduated programs, 3 unknown; due to graduate: none."}


def morning(**more) -> str:
    return since_digest.digest(NIGHT, ASKED - 86400, ASKED, "America/Los_Angeles", **{**KW, "state": STATE, **more})


def paragraph(d: str, head: str) -> str:
    return next(p for p in d.split("\n\n") if p.startswith(head))


def test_the_morning_leads_with_what_is_wrong_now():
    d = morning()
    look = d.split("\n\n")[1].splitlines()
    assert look[0] == "Look first:"
    assert look[1] == ("- An outside result that never came: SCRUM-47 (#51) at sail_check 12 h, waiting on github (usually "
                       "6 min), last heard: SAIL put on #51 at Mon 2 Nov 19:59, nothing since.")
    assert look[2].startswith("- Attention raised, unanswered (not past step ci of delivery_live within 30 min): "
                              "SCRUM-34 at ci 26 h")
    assert look[-1].startswith("- Also today: new work on a ticket already Done (SCRUM-42, SCRUM-43)")   # the replays, last
    assert "the host restarted 15 times" in look[-1]
    assert "SCRUM-50" not in "\n".join(look)                      # five minutes at review: not stuck


def test_a_stuck_item_is_found_by_its_steps_policy_and_its_typical_time():
    clock = since_digest.Clock("UTC", ASKED)
    d = since_digest.Day([], clock)
    state = {"rows": [row("A-1", "review", "2026-11-03T13:00:00Z", {"reason": "awaiting", "by": "owner"}, {"who": "owner"}),
                      row("A-2", "write", "2026-11-03T12:00:00Z", {"reason": "awaiting", "by": "worker"}, {"who": "worker"}),
                      row("A-3", "write", "2026-11-03T15:30:00Z", {"reason": "awaiting", "by": "worker"}, {"who": "worker"}),
                      row("A-4", "sail_check", "2026-11-03T04:00:00Z", {"reason": "awaiting", "by": "sail"})],
             "typical": {("delivery_live", "write.waiting"): 1200.0}, "within": {("delivery_live", "review"): 3600.0},
             "outside": {}}
    found = dict(since_digest.since_state.stuck(d, state))
    assert found["past its step's limit"] == ["A-1 at review 2.9 h (its limit 60 min)"]
    assert found["longer than its step usually takes"] == ["A-2 at write 3.9 h (usually 20 min)"]   # A-3: 25 min, not yet
    assert found["an outside result that never came"] == ["A-4 at sail_check 12 h, waiting on sail"]  # no typical: 6 h stale


def test_typical_times_are_the_stays_between_moves():
    M = SimpleNamespace
    moves = [M(workflow="w", source="<start>", to="a.waiting", time="2026-11-03T00:00:00Z"),
             M(workflow="w", source="a.waiting", to="b.waiting", time="2026-11-03T00:05:00Z"),
             M(workflow="w", source="b.waiting", to="a.waiting", time="2026-11-03T01:05:00Z"),
             M(workflow="w", source="a.waiting", to="<end>", time="2026-11-03T01:15:00Z")]
    assert item_state.stays(moves) == {("w", "a.waiting"): [300.0, 600.0], ("w", "b.waiting"): [3600.0]}
    assert item_state.typical({("w", "a"): [1.0, 2.0], ("w", "b"): [1.0, 5.0, 3.0]}) == {("w", "b"): 3.0}


def test_replayed_events_are_counted_apart_and_never_anomalies():
    def ev(ty, ts, subject, **data):
        return {"type": ty, "ts": ts, "subject": subject, "source": "x", "data": data}
    replay = [ev("jira.issue.transitioned", "2026-11-03T10:00:00Z", "SCRUM-42", to="Done", replayed=True),
              ev("github.pr.opened", "2026-11-03T10:00:05Z", "SCRUM-42", number=47, replayed=True),
              ev("github.pr.merged", "2026-11-03T09:00:00Z", "SCRUM-43", number=45),
              ev("github.pr.labeled", "2026-11-03T10:00:06Z", "SCRUM-43", number=45, label="REJECTED", replayed=True),
              ev("github.pr.closed", "2026-11-03T10:00:07Z", "SCRUM-44", number=48, merged=False, replayed=True),
              ev("github.pr.opened", "2026-11-03T09:30:00Z", "SCRUM-44", number=48)]
    d = since_digest.digest(replay, ASKED - 86400, ASKED, "UTC", waiting=KW["waiting"], preds=[], model="M")
    assert d.splitlines()[0].endswith("6 events on the bus, 4 of them replayed at a resume (read again by a poller: counted "
                                      "apart, never flagged).")
    assert "Look first" not in d and "SCRUM-42" not in d
    assert "1 opened: #48 (SCRUM-44)" in paragraph(d, "Pull requests:")       # opened for real; its replayed close still shuts it


def test_lists_name_the_newest_and_the_open_ones_first():
    d = morning()
    assert paragraph(d, "Tickets:").startswith("Tickets: 6 opened: SCRUM-50, SCRUM-47, … and 4 more; 6 Done: SCRUM-49,")
    prs = paragraph(d, "Pull requests:")
    assert prs.startswith("Pull requests: 11 opened: #55 (SCRUM-50), #51 (SCRUM-47)") and "#44" not in prs
    assert "7 merged: #54, #52, … and 5 more" in prs and "2 triage verdicts: local twice (SCRUM-49, SCRUM-48)" in prs
    assert paragraph(d, "Items:").startswith("Items: 5 open now: SCRUM-50 (#55) at review 5 min and the 4 above;")
    full = paragraph(morning(full=True), "Items:")
    assert full.startswith("Items: 5 open now: SCRUM-50 (#55) at review 5 min, SCRUM-47 (#51) at sail_check 12 h, "
                           "SCRUM-39 at ci 24 h, SCRUM-41 at ci 24 h, SCRUM-34 at ci 26 h;")


def test_the_reply_is_read_in_a_minute_and_the_rest_is_on_more():
    d = morning()
    assert len(d) <= since_digest.LIMIT                          # 1,500 (the reply T2j read: about 4,000)
    assert d.endswith(f'Say "more" for the full list.\n\n{MODEL}')              # the model line last, as ever
    assert OWN in d and "Waiting on you: slack_notify is disabled (the token at" in d
    assert "Machines by age" not in d and "Also on the bus" not in d and "Labels:" not in d     # left to "more"
    full = morning(full=True)
    assert len(full) > 3 * len(d) and "more" not in full.split("\n\n")[-2]
    assert "Machines by age" in full and "Labels: SAIL put on 11 times" in full and SLACK.split(": ", 1)[1] in full
    assert "6 assigned: SCRUM-50 at 07:47, SCRUM-49 at 03:47" in full                # every entry, with its time
    small = since_digest.digest([], ASKED - 3600, ASKED, "UTC", waiting=KW["waiting"], preds=[], model="M")
    assert "more" not in small                                    # nothing left out, nothing offered


def test_the_plugins_open_items_and_step_times_reach_the_digest(project, monkeypatch):
    from orchestra_agents import workflow_tools
    M = SimpleNamespace
    step = lambda name, within=None: M(name=name, guards=M(within_s=within) if within else None,
                                       actor=M(kind="outside", name="github") if name == "sail_check" else M(kind="machine"))
    defs = M(workflows=[M(name="delivery_live", steps=[step("ci", 1800.0), step("sail_check")])])
    logs = {f"S-{i}": [M(workflow="delivery_live", source="ci.waiting", to="sail_check.waiting", time=f"2026-11-03T0{i}:00:00Z"),
                       M(workflow="delivery_live", source="sail_check.waiting", to="<end>", time=f"2026-11-03T0{i}:06:00Z")]
            for i in range(3)}

    class Record:
        @classmethod
        def for_project(cls, o):
            return cls()

        def log(self, item):
            return logs.get(item, [])

    plugin = M(define=M(load=lambda o: defs), Record=Record,
               item_list=lambda record, d, now, **kw: {"open": 1, "workflows": [{"items": [OPEN[3], {"item": "S-0", "closed": True}]}]})
    monkeypatch.setattr(workflow_tools, "plugin", lambda: plugin)
    monkeypatch.setattr(item_state, "walker", lambda: lambda workflows, facts: ({}, facts))
    got = item_state.read(project, ["S-0", "S-1", "S-2"])
    assert got["rows"] == [OPEN[3]] and got["within"] == {("delivery_live", "ci"): 1800.0}
    assert got["typical"] == {("delivery_live", "sail_check.waiting"): 360.0}
    assert got["outside"] == {("delivery_live", "sail_check"): "github"}
    monkeypatch.setattr(workflow_tools, "plugin", lambda: None)
    assert item_state.read(project, []) is None                     # no plugin: the bus alone


def test_an_item_a_person_must_act_on_is_never_an_outside_result():
    d = since_digest.Day([], since_digest.Clock("UTC", ASKED))
    late = {**OPEN[4], "age_s": 7200.0}                               # SCRUM-50 two hours at review, the owner to approve
    found = dict(since_digest.since_state.stuck(d, {**STATE, "rows": [late],
                                                    "typical": {("delivery_live", "review.waiting"): 600.0}}))
    assert found == {"longer than its step usually takes": ["SCRUM-50 at review 2 h (usually 10 min)"]}
