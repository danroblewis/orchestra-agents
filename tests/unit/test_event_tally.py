"""T2c step 16: "what changed since yesterday?" said "two were merged" of three pull requests, said
the idle Outbox "will not send", and left out the triage verdict and the model. read_events with
since now counts each class over the window (tally), and the prompt says to copy it, to call an
idle Outbox "nothing waiting", and to end with read_model's state."""

from datetime import UTC, datetime

from design_fixtures import call

from orchestra_agents import bus_reads, event_tally, prompting
from orchestra_agents.org import built_in_agents

pytest_plugins = ["design_fixtures"]


def at(hm: str) -> str:
    return datetime(2026, 11, 2, int(hm[:2]), int(hm[3:]), tzinfo=UTC).isoformat()


def ev(ty, hm, source, subject=None, **data):
    return {"type": ty, "ts": at(hm), "source": source, **({"subject": subject} if subject else {}), **data}


GH, OUT, DISP = "person.github.danroblewis", "outbox", "supervisor/dispatch"
# the T2c day (events.jsonl), in the bus's flat shape
DAY = [
    ev("jira.issue.created", "04:05", "person.jira.x", "SCRUM-23", status="To Do"),
    ev("jira.issue.created", "04:05", "person.jira.x", "SCRUM-24", status="To Do"),
    ev("item.requested", "04:05", DISP, "review_nudger@2026-11-02", workflow="review_nudger"),
    ev("item.closed", "04:05", DISP, "review_nudger@2026-11-02", outcome="none"),
    ev("outbox.draft.created", "04:10", DISP, "d1", kind="live.pr_open"),
    ev("outbox.draft.approved", "04:10", OUT, "d1", by="policy"),
    ev("outbox.sent", "04:10", OUT, "d1", kind="live.pr_open"),
    ev("github.pr.opened", "04:10", GH, "SCRUM-23", number=21),
    ev("github.pr.opened", "04:10", GH, "SCRUM-24", number=22),
    ev("github.checks.completed", "04:10", "bot.github.github-actions", "SCRUM-24", number=22, outcome="success"),
    ev("github.pr.labeled", "04:20", GH, "SCRUM-24", number=22, label="SAIL"),
    ev("sail.check.completed", "04:25", "sail", "SCRUM-24", number=22, outcome="failure"),
    ev("github.pr.labeled", "04:25", GH, "SCRUM-24", number=22, label="REJECTED"),
    ev("offer.finished", "04:30", "sail_triage/1", "SCRUM-24", offer="SCRUM-24.triage.450d7be5", outcome="local"),
    ev("offer.finished", "04:30", "worker/1", "SCRUM-24", offer="SCRUM-24.write.11112222", outcome="pushed"),
    ev("outbox.draft.created", "04:45", DISP, "d2", kind="live.nudge"),
    ev("outbox.draft.approved", "04:46", "person.owner", "d2"),
    ev("outbox.sent", "04:46", OUT, "d2", kind="live.nudge"),
    ev("github.pr.labeled", "04:45", GH, "SCRUM-24", number=22, label="SAILED"),
    ev("github.pr.merged", "04:45", GH, "SCRUM-24", number=22),
    ev("jira.issue.transitioned", "04:50", "person.jira.x", "SCRUM-24", to="Done"),
    ev("item.requested", "04:51", "owner", "delivery_live-b21163", workflow="delivery_live"),
    ev("github.pr.labeled", "04:55", GH, "SCRUM-23", number=21, label="SAILED"),
    ev("github.pr.merged", "04:55", GH, "SCRUM-23", number=21),
    ev("jira.issue.created", "04:52", "person.jira.x", "SCRUM-25", status="To Do"),
    ev("github.pr.opened", "04:56", GH, "SCRUM-25", number=23),
    ev("jira.issue.transitioned", "05:00", "person.jira.x", "SCRUM-23", to="Done"),
    ev("pagerduty.incident.triggered", "05:03", "bot.pagerduty_fake", "PT295791", service="PSVC003"),
    ev("pagerduty.incident.resolved", "05:04", "bot.pagerduty_fake", "PT295791", service="PSVC003"),
    ev("github.pr.labeled", "05:05", GH, "SCRUM-25", number=23, label="SAILED"),
    ev("github.pr.merged", "05:05", GH, "SCRUM-25", number=23),
    ev("orchestra.model.changed", "05:10", "orchestra-host"),
    ev("jira.issue.transitioned", "05:10", "person.jira.x", "SCRUM-25", to="Done"),
    ev("item.closed", "05:10", DISP, "delivery_live-b21163", outcome="sent"),
]


def test_read_events_since_tallies_every_class_over_the_whole_window(project, store, monkeypatch):
    monkeypatch.setattr(bus_reads, "recent", lambda store, n=bus_reads.SCAN: DAY)
    out = call(project, store, "read_events", since="2026-11-02T04:00:00Z", order="oldest", limit=5)
    assert out["shown"] == 5 and out["count"] == len(DAY)                   # the tally is not the five shown
    t = out["tally"]
    assert t["pull_requests"]["opened"] == {"count": 3, "which": ["#21 (SCRUM-23)", "#22 (SCRUM-24)", "#23 (SCRUM-25)"]}
    assert t["pull_requests"]["merged"]["count"] == 3                      # T2c said "two were merged"
    assert t["tickets"]["opened"]["count"] == 3 and t["tickets"]["closed"]["count"] == 3
    assert t["labels"]["SAILED"]["count"] == 3 and t["labels"]["REJECTED"] == {"count": 1, "which": ["#22 (SCRUM-24)"]}
    assert t["labels"]["SAIL"]["count"] == 1
    assert t["triage"] == {"local": {"count": 1, "which": ["SCRUM-24"]}}   # the write step's finish is no verdict
    assert t["checks"] == {"github.checks success": {"count": 1}, "sail.check failure": {"count": 1}}
    assert t["incidents"]["opened"]["which"] == ["PT295791"] and t["incidents"]["resolved"]["count"] == 1
    assert t["outbox"] == {"drafted": {"count": 2}, "approved by configuration": {"count": 1},
                           "approved by the owner": {"count": 1}, "sent": {"count": 2}}
    assert t["items"]["started"]["count"] == 2 and t["items"]["started by the owner"]["which"] == ["delivery_live-b21163"]
    assert t["items"]["closed"]["count"] == 2 and t["model"] == {"changed": {"count": 1}}
    assert "never count the events yourself" in out["tally_note"]
    assert "tally" not in call(project, store, "read_events", minutes=60)       # only a window from since


def test_the_tally_reads_the_cloudevents_shape_too():
    e = {"type": "github.pr.merged", "source": GH, "subject": "SCRUM-24", "data": {"number": 22}}
    assert event_tally.tally([e]) == {"pull_requests": {"merged": {"count": 1, "which": ["#22 (SCRUM-24)"]}}}


def test_the_prompt_copies_the_tally_says_an_idle_outbox_waits_and_ends_with_the_model(project, monkeypatch):
    from orchestra_agents import installed
    for on in (False, True):
        monkeypatch.setattr(installed, "tasking", lambda on=on: on)
        text = " ".join(prompting.agent_prompt(project, built_in_agents()["supervisor"]).split())
        assert "Every number and name comes from the answer's `tally`" in text and "never count the events yourself" in text
        assert ('An Outbox with no drafts waiting is idle: say "nothing waiting", never that it will not send or is down'
                in text)
        assert "End the answer with the model's state from `read_model`, called in this turn: its `state`" in text
        assert "its triage verdict (`tally`'s `triage`)" in text
