"""T2k s16, the overnight check on the standing demo: after a restart the Outbox sender crashed five
times and the launcher stopped trying it at 01:17; SCRUM-47 then waited at close_ticket on it. The
digest led with three day-old attentions (oldest first, the night's one hidden under "… and 1
more"), said only "Now: not running" in the Outbox line, and offered for approval a reminder about
#51, which had merged at 01:16. These replay that night in small: the dead process first and what
it blocks, newest first everywhere, the stale nudge withdrawn."""

from datetime import datetime

from orchestra_agents import since_dead, since_digest, stale_nudges

ASKED = datetime.fromisoformat("2026-11-04T01:27:51Z").timestamp()
ATTENTION = {"reason": "attention raised", "why": "not past step ci of delivery_live within 30 min", "by": "supervisor"}
OUTBOX_DOWN = {"reason": "attention raised", "by": "supervisor",
               "why": "the outbox could not take the draft at step close_ticket (URLError [Errno 61] Connection refused)"}


def at(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def ev(ty, iso, subject=None, source="orchestra-host", **data):
    return {"type": ty, "ts": iso, "source": source, **({"subject": subject} if subject else {}), "data": data}


def row(item, step, since, wait, acts=None):
    return {"item": item, "workflow": "delivery_live", "step": step, "pile": f"{step}.waiting", "wait": wait,
            "age_s": ASKED - at(since), "acts": acts or {"who": "owner"}}


NIGHT = [ev("github.pr.opened", "2026-11-03T03:50:00Z", "SCRUM-47", source="person.github.x", number=51),
         ev("attention.needed", "2026-11-03T13:00:00Z", "SCRUM-34", source="workflows", reason="overdue"),
         *[ev(t, f"2026-11-04T01:16:{s}Z", name="outbox.sender", code=2, restart_in=2.0)
           for s in ("33", "38", "43", "52") for t in ("orchestra.process.exited",)],
         ev("github.pr.merged", "2026-11-04T01:16:32Z", "SCRUM-47", source="person.github.x",
            repository="danroblewis/orchestra-live-delivery", number=51),
         ev("orchestra.process.exited", "2026-11-04T01:17:09Z", name="outbox.sender", code=2, restart_in=None),
         ev("orchestra.process.gave_up", "2026-11-04T01:17:09Z", name="outbox.sender", code=2, failures=5)]
OPEN = [row("SCRUM-34", "ci", "2026-11-02T13:38:59Z", ATTENTION),
        row("SCRUM-41", "ci", "2026-11-02T15:49:33Z", ATTENTION),
        row("SCRUM-39", "ci", "2026-11-02T15:49:44Z", ATTENTION),
        row("SCRUM-33", "ci", "2026-11-02T12:00:00Z", ATTENTION),
        row("SCRUM-47", "close_ticket", "2026-11-04T01:16:40Z", OUTBOX_DOWN, {"who": "live.close_ticket"})]
STATE = {"rows": OPEN, "typical": {}, "within": {}, "outside": {},
         "actors": {("delivery_live", "close_ticket"): ("outbox", "live.close_ticket"), ("delivery_live", "ci"): ("outside", "github")}}
RECORD = {"processes": {"outbox.sender": {"pid": None, "state": "gave_up", "code": 2, "owner": "outbox", "restarts": 4},
                        "github_live": {"pid": 52461, "state": "running", "code": None, "owner": "project"},
                        "jira_live": {"pid": None, "state": "exited", "code": 0, "owner": "project"}},
          "instruments": {"github_prs": {"target": "https://api.github.com/repos/x/pulls", "last_error": "HTTP Error 401: Bad credentials",
                                         "last_error_at": ASKED - 60, "last_write": at("2026-11-04T00:10:00Z")},
                          "disk": {"target": "machines/disk.sh", "last_write": ASKED - 30, "last_error_at": ASKED - 900}}}
NUDGE = {"id": "d71d91f1de7954d2", "kind": "live.nudge", "state": "drafted",
         "target": {"repo": "danroblewis/orchestra-live-delivery", "number": 51}}
KW = {"outbox_state": "not running, 1 waiting", "preds": [], "model": "There is no model yet.", "open_items": 5}


def digest(**more) -> str:
    waiting = stale_nudges.withdraw_stale({"machines": [], "outbox": [NUDGE], "changes": [], "questions": []}, NIGHT)
    return since_digest.digest(NIGHT, ASKED - 86400, ASKED, "UTC", waiting=waiting, state=STATE, record=RECORD, **{**KW, **more})


def test_the_stopped_sender_comes_first_with_what_it_blocks():
    look = digest().split("\n\n")[1].splitlines()
    assert look[0] == "Look first:"
    assert look[1] == ("- A process the launcher stopped retrying: outbox.sender (outbox plugin's) stopped at 01:17 (exit code 2, "
                       "5 crashes in a row), not tried again; SCRUM-47 (#51) waits at close_ticket on the stopped outbox.sender "
                       "since 01:16.")
    assert look[2] == ("- An instrument that cannot fetch its target: github_prs (https://api.github.com/repos/x/pulls): HTTP Error "
                       "401: Bad credentials; nothing written since 00:10.")
    assert "SCRUM-47" not in "\n".join(look[3:])                 # said once, where its cause is
    assert "jira_live" not in "\n".join(look) and "disk" not in "\n".join(look)   # a clean exit, a collector that writes


def test_the_attention_list_is_newest_first_and_a_cut_list_says_how_many_more():
    look = digest().split("\n\n")[1].splitlines()
    assert look[3] == ("- Attention raised, unanswered (not past step ci of delivery_live within 30 min): SCRUM-39 at ci 34 h; "
                       "SCRUM-41 at ci 34 h; SCRUM-34 at ci 36 h; … and 1 more.")
    items = next(p for p in digest().split("\n\n") if p.startswith("Items:"))
    assert items.startswith("Items: 5 open now: SCRUM-47 (#51) at close_ticket 11 min, SCRUM-39 at ci 34 h, SCRUM-41 at ci 34 h, "
                            "… and 2 more;")


def test_a_stale_nudge_is_withdrawn_never_offered():
    d = digest()
    waits = next(p for p in d.split("\n\n") if p.startswith("Waiting on you:"))
    assert "waits for your approval" not in waits
    assert waits == ("Waiting on you: nothing. Withdrawn, never offered (the pull request it reminds about is done): Outbox draft "
                     "d71d91f1de7954d2 (live.nudge) about #51, merged at 01:16; reject it on the Outbox page to clear it.")
    assert len(d) <= since_digest.LIMIT


def test_only_a_nudge_about_a_done_pull_request_is_withdrawn():
    other = {**NUDGE, "id": "open", "target": {"repo": "danroblewis/orchestra-live-delivery", "number": 52}}
    closed = {**NUDGE, "id": "closed", "target": {"pull": "https://github.com/x/y/pull/60"}}
    elsewhere = {**NUDGE, "id": "elsewhere", "target": {"repo": "someone/else", "number": 51}}
    ticket = {"id": "t", "kind": "live.close_ticket", "target": {"number": 51}}
    events = [*NIGHT, ev("github.pr.closed", "2026-11-04T00:00:00Z", source="x", number=60, merged=False),
              ev("github.pr.closed", "2026-11-04T00:00:00Z", source="x", number=52, merged=False),
              ev("github.pr.reopened", "2026-11-04T00:30:00Z", source="x", number=52)]
    keep, gone = stale_nudges.split([NUDGE, other, closed, elsewhere, ticket], events)
    assert [d["id"] for d in keep] == ["open", "elsewhere", "t"]         # reopened, another repository, not a nudge
    assert [(g["id"], g["state"]) for g in gone] == [("d71d91f1de7954d2", "merged"), ("closed", "closed")]


def test_a_stopped_process_is_known_by_its_record_today_and_after_the_launcher_keeps_trying():
    assert since_dead.stopped({"pid": None, "code": 2, "state": "exited"})            # an exit code and no pid
    assert not since_dead.stopped({"pid": None, "code": 2, "state": "waiting"})       # it starts again in a moment
    assert not since_dead.stopped({"pid": None, "code": 0, "state": "exited"})        # it finished
    c12 = {"pid": None, "code": 2, "state": "waiting", "owner": "outbox",
           "note": "stopped after 5 crashes; next try at 01:47"}
    assert since_dead.stopped(c12)
    d = since_digest.Day(NIGHT, since_digest.Clock("UTC", ASKED))
    [(what, [line])], named = since_dead.dead(d, {"processes": {"outbox.sender": c12}}, STATE)
    assert line.startswith('outbox.sender (outbox plugin\'s) stopped at 01:17 (exit code 2): "stopped after 5 crashes; next try '
                           'at 01:47"; SCRUM-47 (#51) waits at close_ticket') and named == {"SCRUM-47"} and what.startswith("a process")


def test_which_steps_a_process_serves_is_read_from_the_steps_actor():
    serves = since_dead.serves
    assert serves("outbox.sender", {"owner": "outbox"}, ("outbox", "live.close_ticket"))
    assert not serves("sail", {"owner": "project"}, ("outbox", "live.close_ticket"))
    assert serves("github_live", {}, ("outside", "github")) and not serves("githubber", {}, ("outside", "github"))
    assert serves("review_nudger", {}, ("program", "review_nudger")) and serves("workflows.programs", {}, ("program", "x"))
    assert serves("agents.host", {"owner": "agents"}, ("role", "worker"))
    assert serves("workflows.floor", {"owner": "workflows"}, None)                     # it moves every item
    assert not serves("slack_notify", {}, ("person", "owner"))


def test_in_the_bounded_reply_the_items_line_counts_the_blocked_one_above():
    d = since_digest.Day(NIGHT, since_digest.Clock("UTC", ASKED), 3, brief=True)
    since_digest.first(d, STATE, since_digest.LOOK, RECORD)
    assert "SCRUM-47" in d.flagged
    assert "the 4 above" in since_digest.items(d, 5, OPEN)
