"""T44, bootstrap run 8's findings 4 and 5, on the fakes (no model, no Workflows plugin): the adopt
rule kept beside a ticket workflow and compiled into the plugin's own words, the reports folded into
the tickets to adopt, the host's adopter publishing the Supervisor's acts (never a poller's event),
and the chat queue's bot threads left out only on the owner's words about bots. The real plugin's
recorder runs the same in tests/e2e/test_workflows_e2e.py."""

from types import SimpleNamespace

import pytest
from conftest import agents_project
from test_workflow_tools import FakePlugin, git

from orchestra_agents import adopt, bot_threads, tooling, workflow_tools
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore, StoreError

TICKET = {"name": "ticket", "starts": [{"event": "jira.issue.assigned", "data": {"category": {"not": ["done"]}}}],
          "steps": [{"name": "assigned", "who": {"outside": "github"}, "done_when": {"event": "github.pr.opened", "of": "children"},
                     "outcomes": {"done": "review"}},
                    {"name": "review", "who": {"outside": "github"}, "done_when": {"event": "github.pr.merged", "of": "children"},
                     "outcomes": {"done": None}}]}
ADOPT = {"event": ["jira.issue.transitioned"], "data": {"category": {"not": ["new", "done"]}},
         "at": {"status": {"In Review": "review"}}}
QUEUE = {"name": "slack_queue", "items": "request",
         "starts": [{"event": "slack.thread.started", "data": {"bot": {"not": [True]}, "replayed": {"not": [True]}}}],
         "steps": [{"name": "answer", "who": {"outside": "slack"}, "done_when": {"event": "slack.thread.replied"},
                    "outcomes": {"done": None}}]}
POLICIES = "Their response policies are hackriff-help 2 hours, hackriff-dev 4 hours, hackriff-incidents 15 minutes."


def ev(type_, subject, source="person.jira.iruiz", id_=None, **data):
    return {"type": type_, "subject": subject, "source": source, "id": id_ or f"{type_}:{subject}:{len(data)}", "data": data}


def test_the_rule_is_checked_against_the_definition_and_compiled_in_the_plugins_words():
    rule = adopt.rule_of(ADOPT, TICKET)
    assert rule == {**ADOPT, "starts": ["jira.issue.assigned"]}
    out = adopt.compiled(TICKET, rule)
    assert out["starts"] == [*TICKET["starts"], {"request": True}]
    assert out["steps"][0]["on"] == [{"event": "supervisor.adopted", "data": {"workflow": "ticket", "step": "review"}, "to": "review"}]
    assert adopt.compiled(out, rule) == out                                    # declared again: the same file
    assert "on" not in adopt.compiled(out, None)["steps"][0]                    # dropped: its moves go
    assert TICKET["steps"][0].get("on") is None                                 # the caller's definition untouched
    for bad, word in (({"event": "jira"}, "event is"), ({**ADOPT, "at": {"status": {"In Review": "reveiw"}}}, "'reveiw'"),
                      ({**ADOPT, "when": 1}, "'when'"), ({**ADOPT, "data": {"category": {"in": ["x"]}}}, "data maps")):
        with pytest.raises(StoreError, match=word):
            adopt.rule_of(bad, TICKET)


def test_the_newest_report_decides_and_a_refused_start_holds():
    rule = adopt.rule_of(ADOPT, TICKET)
    admit = lambda e: e["data"].get("assignee") == "dan"
    evs = [ev("jira.issue.transitioned", "HK-1", status="In Progress", category="indeterminate"),
           ev("jira.issue.transitioned", "HK-1", id_="b", status="In Review", category="indeterminate"),
           ev("jira.issue.transitioned", "HK-2", status="In Progress", category="indeterminate"),
           ev("jira.issue.transitioned", "HK-2", id_="c", status="Done", category="done"),
           ev("jira.issue.assigned", "HK-3", assignee="kofi", category="indeterminate"),
           ev("jira.issue.transitioned", "HK-3", status="In Progress", category="indeterminate"),
           ev("jira.issue.assigned", "HK-4", assignee="dan", status="To Do", category="indeterminate")]
    seen: dict = {}
    got = adopt.fold(evs, rule, admit, seen)
    assert sorted(got) == ["HK-1", "HK-4"] and got["HK-1"]["id"] == "b"
    assert seen == {"HK-3": "refused", "HK-4": "started"}
    assert adopt.step_of(rule, got["HK-1"]["data"], "assigned") == ("review", "status", "In Review")
    assert adopt.step_of(rule, {"status": "In Progress"}, "assigned")[0] == "assigned"


class FakeBus:
    def __init__(self, evs):
        self.evs, self.out = list(evs), []

    def end(self):
        return len(self.evs)

    def page(self, since, limit):
        return self.evs[since:since + limit], min(since + limit, len(self.evs))

    def emit(self, type_, *, src, about, **data):
        self.out.append({"type": type_, "source": src, "subject": about, "data": data})


class FakeStore:
    def __init__(self, bus):
        self.events, self.state = bus, {}

    def agent_state(self, name):
        return dict(self.state.get(name) or {})

    def update_agent_state(self, name, key, fn):
        self.state.setdefault(name, {})[key] = fn(self.state.get(name, {}).get(key))


def fake_plugin(known=()):
    wf = SimpleNamespace(name="ticket")
    return SimpleNamespace(define=SimpleNamespace(load=lambda o: SimpleNamespace(get=lambda n: wf if n == "ticket" else None)),
                           admits=lambda w, e, s: e["data"].get("assignee") == "dan", settings=lambda o: {},
                           Record=SimpleNamespace(for_project=lambda o: SimpleNamespace(item=lambda k: {} if k in known else None)))


def test_declared_adopts_what_the_bus_reports_and_the_host_adopts_what_comes_later_once():
    bus = FakeBus([ev("jira.issue.transitioned", "HK-101", id_="j1", status="In Review", category="indeterminate"),
                   ev("jira.issue.transitioned", "HK-102", id_="j2", status="In Progress", category="indeterminate"),
                   ev("jira.issue.transitioned", "HK-103", id_="j3", status="In Progress", category="indeterminate")])
    store, rule = FakeStore(bus), adopt.rule_of(ADOPT, TICKET)
    p = fake_plugin(known={"HK-103"})                                    # the record has HK-103 already
    took = adopt.declared(p, None, store, "ticket", "assigned", rule)
    assert [(t["item"], t["step"]) for t in took] == [("HK-101", "review"), ("HK-102", "assigned")]
    types = [(e["type"], e["subject"]) for e in bus.out]
    assert types == [("item.requested", "HK-101"), ("item.fact", "HK-101"), ("supervisor.adopted", "HK-101"),
                     ("item.requested", "HK-102"), ("item.fact", "HK-102")]
    assert all(e["source"] == "supervisor" for e in bus.out) and not any(t.startswith("jira.") for t, _ in types)
    req = bus.out[0]["data"]
    assert req["workflow"] == "ticket" and req["adopted"] is True and req["from"] == "j1" and "not a new assignment" in req["brief"]
    assert bus.out[1]["data"]["set"] == {"adopted": "at tracking start", "status": "In Review"}
    assert bus.out[2]["data"] == {"workflow": "ticket", "step": "review", "adopted": True, "from": "j1", "status": "In Review"}
    assert adopt.listened(store) == ["jira.issue.assigned", "jira.issue.transitioned"]
    bus.out.clear()
    bus.evs += [ev("jira.issue.transitioned", "HK-101", id_="j4", status="Done", category="done"),
                ev("jira.issue.transitioned", "HK-111", id_="j5", status="In Review", category="indeterminate"),
                ev("jira.issue.assigned", "HK-112", id_="j6", assignee="dan", category="new"),
                ev("jira.issue.transitioned", "HK-112", id_="j7", status="In Progress", category="indeterminate"),
                ev("jira.issue.transitioned", "HK-102", id_="j8", status="In Review", category="indeterminate")]
    host = adopt.Adopter(store, lambda: p)
    assert [(d["item"], d["step"]) for d in host.tick(SimpleNamespace(orchestra=None))] == [("HK-111", "review")]
    assert host.tick(SimpleNamespace(orchestra=None)) == []                           # read once
    assert [e["subject"] for e in bus.out if e["type"] == "item.requested"] == ["HK-111"]


def test_a_redeclaration_keeps_the_rule_and_false_drops_it(monkeypatch):
    store = FakeStore(FakeBus([]))
    adopt.declared(fake_plugin(), None, store, "ticket", "assigned", adopt.rule_of(ADOPT, TICKET))
    d, rule, adopting = workflow_tools._adopt_rule(store, {}, TICKET)
    assert adopting and rule["at"] == ADOPT["at"] and d["steps"][0]["on"][0]["event"] == adopt.MOVE
    d, rule, adopting = workflow_tools._adopt_rule(store, {"adopt": False}, TICKET)
    assert adopting and rule is None and "on" not in d["steps"][0]
    d, rule, _ = workflow_tools._adopt_rule(FakeStore(FakeBus([])), {}, {**TICKET, "adopt": ADOPT})
    assert "adopt" not in d and rule["event"] == ADOPT["event"]                  # inside the definition: taken out


def test_bot_threads_are_left_out_only_on_the_owners_words_about_bots():
    owner = {"author": "owner", "quote": POLICIES}
    with pytest.raises(StoreError) as e:
        bot_threads.check(QUEUE, owner, "owner")
    msg = str(e.value)
    assert "never asked for" in msg and "incidents or alerts" in msg and "items are alert" in msg and "nothing written" in msg
    bot_threads.check(QUEUE, {"author": "owner", "quote": "only people's threads, never the bots"}, "owner")
    with pytest.raises(StoreError):
        bot_threads.check(QUEUE, {"author": "supervisor", "quote": "never the bots"}, "owner")   # not the owner's
    both = {**QUEUE, "starts": [*QUEUE["starts"], {"event": "slack.thread.started",
                                                   "data": {"bot": True, "channel": {"not": ["C0HKINC"]}}}]}
    bot_threads.check(both, owner, "owner")                                    # the bots' threads taken elsewhere
    alerts = {"name": "slack_alerts", "items": "alert",
              "starts": [{"event": "slack.thread.started", "data": {"bot": True, "channel": "C0HKINC"}}], "steps": []}
    bot_threads.check(alerts, owner, "owner")
    assert bot_threads.said(alerts)["starts"] == ["bot threads in C0HKINC start alerts (only a bot's)"]
    assert bot_threads.said(both)["starts"] == ["bot threads in every channel start no request",
                                                "bot threads in every channel but C0HKINC start requests (only a bot's)"]
    assert "which channels count bot threads" in bot_threads.said(both)["say"]
    assert bot_threads.said(TICKET) is None


@pytest.fixture
def declaring(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    project = agents_project(repo)
    fake = FakePlugin()
    monkeypatch.setattr(workflow_tools, "plugin", fake.ns)
    store = AgentStore(project, Events.for_project(project, source="supervisor"))
    store.set_agent_state("supervisor", turn={"n": 1, "id": "sessions/supervisor/1", "message": POLICIES, "at": 1.0})
    return project, store, fake


def test_declare_workflow_refuses_a_queue_that_drops_the_alert_bots_threads(declaring):
    project, store, fake = declaring

    def call(definition, quote=None):
        args = {"definition": definition, "reason": "the owner's channels and their response policies",
                **({"quote": quote} if quote else {})}
        tooling.check_args("declare_workflow", args)
        return tooling.handle(project, store, "declare_workflow", args, role="supervisor")
    with pytest.raises(StoreError, match="leaves out the threads a bot starts"):
        call(QUEUE, quote="hackriff-incidents 15 minutes")
    assert fake.calls == [] and not (project.orchestra.state_dir / "workflows" / "slack_queue").exists()
    alerts = {"name": "slack_alerts", "items": "alert",
              "starts": [{"event": "slack.thread.started", "data": {"bot": True, "channel": "C0HKINC"}}],
              "steps": QUEUE["steps"]}
    got = call(alerts, quote="hackriff-incidents 15 minutes")
    assert got["ok"] and got["bot_threads"]["starts"] == ["bot threads in C0HKINC start alerts (only a bot's)"]
