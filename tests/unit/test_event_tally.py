"""T2c step 16: "what changed since yesterday?" said "two were merged" of three pull requests, said
the idle Outbox "will not send", and left out the triage verdict and the model. read_events with
since now counts each class over the window (tally), and the prompt says to copy it, to call an
idle Outbox "nothing waiting", and to end with the model's state. T2d step 16: the answer skipped
read_model, so read_events with since carries the model line itself."""

from datetime import UTC, datetime

from design_fixtures import call

from orchestra_agents import bus_reads, event_tally, modeling, prompting
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
        assert "The Outbox's state is the answer's `outbox` line" in text
        assert "never that it is down, needs starting or will not send unless that line says so" in text
        assert "Every prediction in the answer's `predictions` is told, by its id" in text
        assert "Never describe your own tool use (which tools ran, or that none did)" in text
        assert "End the answer with the model's state: the answer's last line is that read's `model` line" in text
        assert "call is needed), and nothing after it" in text
        assert "its triage verdict (`tally`'s `triage`)" in text


class Reads:
    def __init__(self, got, versions=()):
        self.got, self.saved = got, list(versions)

    def versions(self):
        return {"ok": True, "versions": self.saved}

    def model(self, version=None, raw=False):
        if isinstance(self.got, Exception):
            raise self.got
        return {"model": self.got}


def test_read_events_since_carries_the_model_line_read_model_gives(project, store, monkeypatch):
    from orchestra.sd.reads import ReadError
    monkeypatch.setattr(bus_reads, "recent", lambda store, n=bus_reads.SCAN: DAY)
    v4 = {"version": "v4", "stocks": [{"id": "a", "hidden": True}, {"id": "b", "hidden": True}], "flows": [{"id": f"f{i}"} for i in range(5)]}
    monkeypatch.setattr(modeling, "reads", lambda project: Reads(v4))
    out = call(project, store, "read_events", since="2026-11-02T04:00:00Z", order="oldest")
    assert out["model"] == "model v4: its version's record was not read; it has 2 stocks and 5 flows"
    assert call(project, store, "read_model")["state"] == "the model (v4) has 2 stocks and 5 flows"
    gone = ReadError("no model", 404)
    gone.text = "No model yet: discovery looked at 12 series and kept none. It left out 12 of them: too short."
    monkeypatch.setattr(modeling, "reads", lambda project: Reads(gone))
    assert call(project, store, "read_events", since="1d")["model"] == "No model yet: discovery looked at 12 series and kept none."
    monkeypatch.setattr(modeling, "reads", lambda project: Reads(ReadError("boom", 500)))
    assert call(project, store, "read_events", since="1d")["model"] == "the model could not be read (500)"
    monkeypatch.setattr(modeling, "reads", lambda project: Reads(RuntimeError("down")))
    assert call(project, store, "read_events", since="1d")["model"] == "the model could not be read (RuntimeError)"
    assert "model" not in call(project, store, "read_events", minutes=60)       # only with since


def test_the_model_line_says_who_changed_the_model_and_when(project, store, monkeypatch):
    """T2f s16: "Model: one change, from the Review Nudger declaration" while discovery changed it
    (11:14:57Z); and the tool's line printed the label's dict."""
    monkeypatch.setattr(bus_reads, "recent", lambda store, n=bus_reads.SCAN: DAY)
    changed = datetime(2026, 11, 2, 11, 14, 57, tzinfo=UTC).timestamp()
    m = {"label": {"version": None, "latest_discovery_at": changed + 60, "current_version": 5},
         "stocks": [{"id": s} for s in "abc"], "flows": [{"id": f"f{i}"} for i in range(6)]}
    saved = [{"version": 4, "saved_at": changed - 3600, "note": "discovered: the structure changed (net-flow)"},
             {"version": 5, "saved_at": changed, "note": "discovered: the structure changed (net-flow)"}]
    monkeypatch.setattr(modeling, "reads", lambda project: Reads(m, saved))
    out = call(project, store, "read_events", since="2026-11-02T10:15:00Z", order="oldest")
    assert out["model"] == ("model v5, changed by discovery (discovered: the structure changed (net-flow)) at "
                            "2026-11-02 11:14:57Z; it has 3 stocks and 6 flows")
    assert "{" not in out["model"] and "{" not in call(project, store, "read_model")["state"]
    assert call(project, store, "read_model")["state"] == "the model (v5) has 3 stocks and 6 flows"
    later = call(project, store, "read_events", since="2026-11-02T12:00:00Z")["model"]
    assert later.startswith("model v5, unchanged since then; last changed by discovery (")
    m["label"]["current_version"] = None
    assert call(project, store, "read_events", since="1d")["model"] == (
        "model: the latest discovery, at 2026-11-02 11:15:57Z, not saved as a version yet; it has 3 stocks and 6 flows")
    assert modeling.origin("era e2 began: its prior") == "discovery (era e2 began: its prior)"


def test_the_prompt_explains_a_model_change_only_by_its_versions_origin(project):
    text = " ".join(prompting.agent_prompt(project, built_in_agents()["supervisor"]).split())
    assert "A model change is explained only by the version's own origin as that line gives it" in text
    assert "never by a declaration, a machine or anything else that happened near it" in text


class Predicting(Reads):
    def __init__(self, got, preds):
        super().__init__(got)
        self.preds = preds

    def predictions(self, pid=None):
        if isinstance(self.preds, Exception):
            raise self.preds
        return {"ok": True, "predictions": self.preds}


def test_read_events_since_carries_the_outbox_and_each_predictions_state(project, store, monkeypatch):
    """T2e s16: "the Outbox ... needs starting" while it ran the whole time; the waiting prediction
    left out."""
    from orchestra_agents import outbox_read
    from orchestra_agents.store import StoreError
    monkeypatch.setattr(bus_reads, "recent", lambda store, n=bus_reads.SCAN: DAY)
    start = bus_reads.since_of("2026-11-02T04:00:00Z")
    state = {"ok": True, "running": True, "waiting": [{"id": "d9", "state": "drafted"}],
             "recent": [{"id": "d1", "state": "sent", "sent_at": start + 60}, {"id": "d2", "state": "sent", "sent_at": start + 99},
                        {"id": "d0", "state": "sent", "sent_at": start - 60}, {"id": "d3", "state": "rejected"}]}
    monkeypatch.setattr(outbox_read, "fetch", lambda project: state)
    preds = [{"id": "f19ff580", "source": "supervisor", "subject": "review_nudger", "status": "waiting", "horizon_s": 259200,
              "era": None},
             {"id": "a1", "source": "supervisor", "subject": "x", "status": "waiting", "horizon_s": 3600, "era": "e2"},
             {"id": "b2", "source": "worker", "subject": "y", "status": "judged", "verdict": {"verdict": "met"}}]
    monkeypatch.setattr(modeling, "reads", lambda project: Predicting({"version": "v5", "stocks": [], "flows": []}, preds))
    out = call(project, store, "read_events", since="2026-11-02T04:00:00Z", order="oldest")
    assert out["outbox"] == "running; 2 sent since then, 1 waiting"
    assert out["predictions"] == [
        {"id": "f19ff580", "by": "supervisor", "about": "review_nudger",
         "state": "waiting for its change to begin an era, judged 72 h after it begins"},
        {"id": "a1", "by": "supervisor", "about": "x", "state": "waiting: its era e2 has begun, judged 1 h after it begins"},
        {"id": "b2", "by": "worker", "about": "y", "state": "judged: met"}]
    assert list(out).index("model") > list(out).index("predictions")
    state["running"] = False
    assert call(project, store, "read_events", since="1d")["outbox"].startswith("not running; ")

    def missing(project):
        raise StoreError("the outbox plugin is not installed beside Orchestra's web server: there is no Outbox to read")
    monkeypatch.setattr(outbox_read, "fetch", missing)
    monkeypatch.setattr(modeling, "reads", lambda project: Predicting({}, RuntimeError("down")))
    out = call(project, store, "read_events", since="1d")
    assert out["outbox"] == "not installed" and out["predictions"] == "not read (RuntimeError)"
    assert "outbox" not in call(project, store, "read_events", minutes=60) and "tools_used" not in out
