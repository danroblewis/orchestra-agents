"""``request_work`` against the fake Workflows plugin (``workflow_tools.plugin`` replaced): the
Supervisor's request is the owner's, on their quoted words (source ``owner``, ``via: supervisor``),
and refused without them; the Coordinator's is its own; the fields follow as one ``item.fact``; an
unknown workflow, one a request does not start, a used id and a floor that is not running are
refused with nothing published; only the Supervisor and the Coordinator have it."""

import os
from types import SimpleNamespace

import pytest
from conftest import agents_project, supervisor_knows
from orchestra import runtime
from test_workflow_tools import DEF, MESSAGE, FakePlugin, call, git

from orchestra_agents import request_work, workflow_tools
from orchestra_agents.builtins import ROLES
from orchestra_agents.events import Events
from orchestra_agents.harness.tools import BY_NAME
from orchestra_agents.store import AgentStore, StoreError

ASKED = "Declare a two-step workflow: write then review"
BRIEF = "Add a /version endpoint that returns the app version, with a test"
WHY = "the owner asked for this piece of work"


class Plugin(FakePlugin):
    def __init__(self):
        super().__init__()
        self.held: set[str] = set()

    def for_project(self, project):         # the record API: Record.for_project(...).item(...)
        return self

    def item(self, item):
        return {"item": item} if item in self.held else None

    def ns(self):
        return SimpleNamespace(**vars(super().ns()), Record=self)

    def load(self, project):
        defs = super().load(project)
        for w in defs.workflows:            # the real loader's Workflow.request
            w.request = any(s.get("request") for s in w.raw.get("starts") or [{"request": True}])
        return defs


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    return p


@pytest.fixture
def store(project):
    s = AgentStore(project, Events.for_project(project, source="supervisor"))
    s.set_agent_state("supervisor", turn={"n": 1, "id": "sessions/supervisor/1", "message": MESSAGE, "at": 1.0})
    return s


@pytest.fixture
def fake(monkeypatch, project):
    f = Plugin()
    monkeypatch.setattr(workflow_tools, "plugin", f.ns)
    f.write(project.orchestra, DEF)                       # write_review, started by a request
    f.write(project.orchestra, {**DEF, "name": "nightly", "starts": [{"every": "1d"}]})
    return f


@pytest.fixture
def floor(monkeypatch):
    state = {"down": None}
    monkeypatch.setattr(request_work, "floor_down", lambda project: state["down"])
    return state


def published(store, since):
    return [e for e in store.events.read(since, 100)[0] if e["type"] in ("item.requested", "item.fact")]


def test_the_tool_is_the_supervisors_and_the_coordinators():
    assert BY_NAME["request_work"].group == "work"
    assert {n for n, r in ROLES.items() if "work" in r.tools} == {"supervisor", "coordinator"}
    assert BY_NAME["request_work"].input_schema["required"] == ["workflow", "brief", "reason"]


def test_the_supervisors_request_is_the_owners_with_their_words(project, store, fake, floor):
    end = store.events.end()
    out = call(project, store, "request_work", workflow="write_review", brief=BRIEF, quote=ASKED, reason=WHY,
               parent="EPIC-1", fields={"priority": 2})
    item = out["item"]
    assert out["ok"] and item.startswith("write_review-") and out["source"] == "owner" and out["fields"] == ["priority"]
    assert f"/items#item={item}" in out["shown"] and "explain_item" in out["shown"]
    req, fact = published(store, end)
    assert req["source"] == "owner" and req["subject"] == item
    assert {k: req[k] for k in ("workflow", "brief", "parent", "via")} == {
        "workflow": "write_review", "brief": BRIEF, "parent": "EPIC-1", "via": "supervisor"}
    assert fact["source"] == "owner" and fact["subject"] == item and fact["set"] == {"priority": 2}


def test_the_supervisor_never_starts_work_on_its_own(project, store, fake, floor):
    end = store.events.end()
    with pytest.raises(StoreError, match="owner's to ask"):
        call(project, store, "request_work", workflow="write_review", brief=BRIEF, reason=WHY)
    with pytest.raises(StoreError, match="not the owner's words"):
        call(project, store, "request_work", workflow="write_review", brief=BRIEF, reason=WHY, quote="start the nightly build")
    assert published(store, end) == []
    assert MESSAGE.startswith(ASKED)


def test_the_coordinators_request_is_its_own(project, store, fake, floor):
    end = store.events.end()
    out = call(project, store, "request_work", role="coordinator", workflow="write_review", brief=BRIEF,
               reason="the plan's first piece", item="ORC-7")
    assert out["item"] == "ORC-7" and out["source"] == "coordinator"
    (req,) = published(store, end)
    assert req["source"] == "coordinator" and "via" not in req
    with pytest.raises(StoreError, match="needs the 'work' tool group"):
        call(project, store, "request_work", role="worker", workflow="write_review", brief=BRIEF, reason=WHY)


@pytest.mark.parametrize("args, says", [
    ({"workflow": "nope"}, "no workflow 'nope' is loaded .*nightly, write_review"),
    ({"workflow": "nightly"}, "not started by a request"),
    ({"item": "ORC-1"}, "already holds an item 'ORC-1'"),
    ({"item": "bad id!"}, "item is the new item's id"),
    ({"brief": "do it"}, "brief is the work asked for"),
    ({"fields": ["x"]}, "fields is an object"),
])
def test_refusals_publish_nothing(project, store, fake, floor, args, says):
    fake.held.add("ORC-1")
    end = store.events.end()
    with pytest.raises(StoreError, match=says):
        call(project, store, "request_work", **{"workflow": "write_review", "brief": BRIEF, "quote": ASKED, "reason": WHY,
                                                 **args})
    assert published(store, end) == []


def test_a_floor_that_is_not_running_is_said_and_nothing_published(project, store, fake, floor):
    floor["down"] = "the Workflows floor (workflows.floor) is stopped"
    end = store.events.end()
    with pytest.raises(StoreError, match="refused, nothing published: the Workflows floor"):
        call(project, store, "request_work", workflow="write_review", brief=BRIEF, quote=ASKED, reason=WHY)
    assert published(store, end) == []


def test_the_floor_is_running_only_with_a_launcher_and_a_live_floor_process(project):
    o = project.orchestra
    assert "no orchestra run launches this project" in request_work.floor_down(project)
    runtime.update(o, lambda r: r.update(launcher=runtime.me()))
    assert "workflows.floor) is not launched" in request_work.floor_down(project)
    runtime.update(o, lambda r: r.update(processes={"workflows.floor": {"state": "stopped", "pid": None}}))
    assert "workflows.floor) is stopped" in request_work.floor_down(project)
    runtime.update(o, lambda r: r.update(processes={"workflows.floor": {"state": "running", "pid": os.getpid()}}))
    assert request_work.floor_down(project) is None


def test_without_the_plugin_it_says_so(project, store, monkeypatch):
    monkeypatch.setattr(workflow_tools, "plugin", lambda: None)
    with pytest.raises(StoreError, match="not installed"):
        call(project, store, "request_work", workflow="write_review", brief=BRIEF, quote=ASKED, reason=WHY)


def test_the_answer_says_the_item_starts_now_not_on_the_schedule(project, store, fake, floor):
    """T2e s11: "Its step runs on the workflow's schedule, not right away", while the reminder's
    draft waited on the Outbox page 4 s later."""
    fake.write(project.orchestra, {**DEF, "name": "nudger", "starts": [{"request": True},
                                                                     {"every": "24h", "at": "09:00", "tz": "UTC"}]})
    out = call(project, store, "request_work", workflow="write_review", brief=BRIEF, quote=ASKED, reason=WHY)
    assert out["runs"] == f"started now as item {out['item']}; its first step write runs at once"
    out = call(project, store, "request_work", workflow="nudger", brief=BRIEF, quote=ASKED, reason=WHY)
    assert out["runs"] == (f"started now as item {out['item']}; its first step write runs at once; the workflow's "
                           "every 24h at 09:00 UTC start is a separate run, not this one")
    assert "never on the workflow's schedule" in out["say"]


def test_the_prompt_says_a_requested_reminder_starts_at_once(project):
    text = " ".join(supervisor_knows(project).split())
    assert "A requested item starts at once: say what the answer's `runs` says" in text
    assert "never that it waits for the workflow's schedule" in text
