"""``clear_items``: the Supervisor clears the Workflows items, all or one workflow's, when the owner says
clear, reset, delete or start over with them (the owner, 2026-11-04: "I just want to clear the data").
A dry run first; with ``confirm`` it publishes ``items.clear_requested`` as the Supervisor and waits
for the floor's ``items.cleared``. Under ``[agents] ask = "always"`` it needs the owner's quote. The
plugin's record is faked here; the last test runs the real recorder and floor when the installed
plugin clears items (W19)."""

import re
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest
from conftest import agents_project

from orchestra_agents import clear_items, design, ledger, request_work, tooling, workflow_tools
from orchestra_agents.builtins import ROLES
from orchestra_agents.events import Events
from orchestra_agents.floor import CLEAR_REQUESTED, ITEMS_CLEARED
from orchestra_agents.harness import bash_tools
from orchestra_agents.harness.tools import BY_NAME, tools_for
from orchestra_agents.store import AgentStore, StoreError

WHY = "the owner asked to clear the items and start over"
DRY = {"workflow": None, "items": 444, "open": 400, "closed": 44, "workflows": {"delivery": 440, "triage": 4},
       "remain": 0, "without_workflow": 0}


class FakeRecord:
    def __init__(self, dry):
        self.dry, self.asked = dry, []

    def clearing(self, workflow=None):
        self.asked.append(workflow)
        if workflow == "triage":
            return {**self.dry, "workflow": "triage", "items": 4, "open": 4, "closed": 0, "workflows": {"triage": 4},
                    "remain": 440}
        return dict(self.dry)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


@pytest.fixture
def record(monkeypatch):
    rec = FakeRecord(DRY)
    defs = SimpleNamespace(workflows=[SimpleNamespace(name="delivery"), SimpleNamespace(name="triage")])
    fake = SimpleNamespace(Record=SimpleNamespace(for_project=lambda o: rec), define=SimpleNamespace(load=lambda o: defs))
    monkeypatch.setattr(workflow_tools, "plugin", lambda: fake)
    monkeypatch.setattr(request_work, "floor_down", lambda project: None)
    monkeypatch.setattr(clear_items, "WAIT_S", 3.0)
    monkeypatch.setattr(clear_items, "POLL_S", 0.05)
    return rec


def supervisor(project):
    return AgentStore(project, Events.for_project(project, source="supervisor"))


def call(project, role="supervisor", **args):
    args = {"reason": WHY, **args}
    tooling.check_args("clear_items", args)
    return tooling.handle(project, supervisor(project), "clear_items", args, role=role)


def published(project, type_):
    """The bus's events of ``type_``, as this package reads them (``events.flat``: the data's fields)."""
    evs, _ = Events.for_project(project, source="supervisor").after(0)
    return [e for e in evs if e["type"] == type_]


def answer_later(project, record=None, delay=0.3, **data):
    """The floor's answer, as its planner's dispatch, a moment after the request is on the bus (the
    fake record emptied first, as the recorder empties the real one)."""
    def run():
        end = time.time() + 5
        while time.time() < end:
            reqs = published(project, CLEAR_REQUESTED)
            if reqs:
                time.sleep(delay)
                if record is not None:
                    record.dry = {**DRY, "items": 0, "open": 0, "closed": 0, "workflows": {}}
                body = {"request": reqs[-1]["id"], "cleared": 444, "workflows": {"delivery": 440, "triage": 4},
                        "rebuild": False, "rebuilt": None, "by": "supervisor", **data}
                Events.for_project(project, source="supervisor/dispatch").publish(ITEMS_CLEARED, body,
                                                                                  causationid=reqs[-1]["id"])
                return
            time.sleep(0.02)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def set_ask(project, mode):
    toml = project.repo_root / "orchestra.toml"
    text = re.sub(r'\nask = "[a-z]+"', "", toml.read_text())
    toml.write_text(text.replace("[agents]\n", f'[agents]\nask = "{mode}"\n', 1))
    project.reload_config()


def test_the_tool_is_the_supervisors_on_both_routes():
    assert BY_NAME["clear_items"].group == "workflows" and "workflows" in ROLES["supervisor"].tools
    assert set(BY_NAME["clear_items"].required) == {"reason"}
    assert "clear_items" in {t.name for t in tools_for(ROLES["supervisor"].tools)}          # the MCP server's list
    assert "`clear_items`" in bash_tools.prompt(ROLES["supervisor"].tools, path="")        # the shell's
    assert "clear_items" in ledger.CHANGES and CLEAR_REQUESTED == "items.clear_requested"


def test_the_dry_run_counts_and_changes_nothing(project, record):
    out = call(project)
    assert out["dry_run"] and (out["would_clear"], out["open"], out["closed"], out["remain"]) == (444, 400, 44, 0)
    assert out["workflows"] == {"delivery": 440, "triage": 4} and "confirm: true" in out["next"]
    one = call(project, workflow="triage", rebuild=True)
    assert (one["would_clear"], one["remain"], one["rebuild"]) == (4, 440, True) and "rebuild makes them" in one["next"]
    assert "note" in call(project, workflow="gone")
    assert published(project, CLEAR_REQUESTED) == []
    assert ledger.did("clear_items", {"reason": WHY}, out) is None


def test_confirm_publishes_the_request_as_the_supervisor_and_reports_the_answer(project, record):
    answer_later(project, record)
    out = call(project, confirm=True)
    [req] = published(project, CLEAR_REQUESTED)
    assert req["source"] == "supervisor" and not req.get("subject")
    assert {k: req.get(k) for k in ("rebuild", "reason", "by", "workflow")} == \
        {"rebuild": False, "reason": WHY, "by": "supervisor", "workflow": None}
    assert out["requested"] == req["id"] and out["cleared"] == 444 and out["remain"] == 0
    assert "Cleared 444 items (every)" in out["said"] and "Dynamics page" in out["said"]
    assert ledger.did("clear_items", {"reason": WHY}, out) == f"cleared 444 items (every): {WHY}"


def test_one_workflow_with_rebuild_is_named_in_the_request(project, record):
    answer_later(project, cleared=4, workflows={"triage": 4}, rebuild=True, rebuilt=4)
    out = call(project, workflow="triage", rebuild=True, confirm=True)
    [req] = published(project, CLEAR_REQUESTED)
    assert (req["workflow"], req["rebuild"]) == ("triage", True)
    assert out["rebuilt"] == 4 and "made 4 again" in out["said"]


def test_without_an_answer_in_time_it_says_the_request_is_on_the_bus(project, record, monkeypatch):
    monkeypatch.setattr(clear_items, "WAIT_S", 0.3)
    out = call(project, confirm=True)
    assert out["answer"] is None and out["requested"] and "did not answer within 0.3 s" in out["said"]
    assert "no answer yet" in ledger.did("clear_items", {"reason": WHY}, out)
    monkeypatch.setattr(request_work, "floor_down", lambda project: "the Workflows floor is stopped")
    assert "it clears when the floor runs" in call(project, confirm=True)["said"]


def test_under_always_it_needs_the_owners_quote(project, record):
    set_ask(project, "always")
    with pytest.raises(StoreError, match="quote their words"):
        call(project, confirm=True)
    assert published(project, CLEAR_REQUESTED) == []
    store = supervisor(project)
    design.begin_turn(store, "please clear all the items, I want to start over")
    answer_later(project)
    args = {"reason": WHY, "confirm": True, "quote": "clear all the items"}
    out = tooling.handle(project, store, "clear_items", args, role="supervisor")
    [req] = published(project, CLEAR_REQUESTED)
    assert req["by"] == "owner" and req["quote"] == "clear all the items" and out["cleared"] == 444


def test_refused_for_another_role_and_for_a_bad_rebuild(project, record):
    with pytest.raises(StoreError, match="clear_items"):
        call(project, role="worker")
    with pytest.raises(StoreError, match="rebuild is true"):
        tooling.handle(project, supervisor(project), "clear_items", {"reason": WHY, "rebuild": "yes"}, role="supervisor")


def test_the_real_recorder_and_floor_clear_and_answer(project, monkeypatch):
    contract = pytest.importorskip("orchestra_workflows.contract")
    if not hasattr(contract, "CLEAR_REQUESTED"):
        pytest.skip("the installed Workflows plugin does not clear items (W19)")
    from orchestra.events import Events as Bus
    from orchestra_workflows.config import Settings
    from orchestra_workflows.define import load, walker, write
    from orchestra_workflows.floor import core, record_api
    from orchestra_workflows.record import Record, Recorder
    from orchestra_workflows.resources.jobs import Jobs

    monkeypatch.setattr(request_work, "floor_down", lambda project: None)
    o = project.orchestra
    write(o, {"name": "delivery", "planner": "supervisor", "starts": [{"request": True}],
              "steps": [{"name": "write", "who": {"role": "worker"}, "outcomes": {"ready": None}}]})
    defs = load(o)
    rec = Recorder.for_project(o, starts=defs.starts, walk=walker(defs.workflows))
    floor = core.Floor(o, Settings(tick=1, heartbeat=5, lease=20, return_after=10), jobs=Jobs(), defs=defs, recorder=rec)
    floor.start()
    for i in range(3):
        Bus.for_project(o, source="owner").publish("item.requested", {"workflow": "delivery", "brief": "x"}, subject=f"W-{i}")
    stop, lock = threading.Event(), threading.Lock()

    def tick():
        with lock:
            rec.tick()
            floor.tick()
            record_api.annotate(rec, floor.s.items, floor.s.offers, set(), floor.planner)

    tick()
    assert len(Record.for_project(o).items()) == 3
    dry = call(project)
    assert dry["would_clear"] == 3 and dry["workflows"] == {"delivery": 3}

    def ticking():
        while not stop.wait(0.1):
            tick()
    t = threading.Thread(target=ticking, daemon=True)
    t.start()
    try:
        out = call(project, confirm=True)
    finally:
        stop.set()
        t.join(5)
    assert out["cleared"] == 3 and out["remain"] == 0 and Record.for_project(o).items() == []
    assert floor.s.offers == {} and floor.s.items == {}
