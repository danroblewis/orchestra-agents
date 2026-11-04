"""``retry_step``: the Supervisor asks the Workflows floor to offer a blocked or stalled step again
now, once the cause is fixed (T48: the standing demo's SCRUM-56 sat at ``write`` blocked because every
worktree's git push was refused over ssh, and the Supervisor had nothing to do about it). The real
floor and recorder run in process on a fake clock (as orchestra-workflows' own tests run them), the
Supervisor's call goes through ``tooling`` on the dummy harness's project; and the digest names the
items in retry and the stalled ones."""

import subprocess
import time
from datetime import UTC, datetime

import pytest
from conftest import agents_project

from orchestra_agents import ledger, since_digest, tooling
from orchestra_agents.builtins import ROLES
from orchestra_agents.events import Events
from orchestra_agents.harness.tools import BY_NAME
from orchestra_agents.store import AgentStore, StoreError

core = pytest.importorskip("orchestra_workflows.floor.core")
walk_mod = pytest.importorskip("orchestra_workflows.define.walk")
if not hasattr(walk_mod, "RETRY_NOW"):
    pytest.skip("the installed Workflows plugin takes no item.retry_requested (W17)", allow_module_level=True)

from orchestra.events import Events as Bus
from orchestra_workflows.config import Settings
from orchestra_workflows.define import load, walker, write
from orchestra_workflows.floor import record_api
from orchestra_workflows.record import Recorder
from orchestra_workflows.resources.jobs import Jobs

FIXED = "git push was refused over ssh (publickey): the ssh agent's key is loaded again"
STALLED = {"name": "stalled", "who": {"person": "owner"}, "done_when": {"event": "item.resumed"},
           "outcomes": {"done": "write"}}


def delivery(stalled: bool = False) -> dict:
    d = {"name": "delivery", "planner": "supervisor", "starts": [{"request": True}],
         "steps": [{"name": "write", "who": {"role": "worker"}, "outcomes": {"ready": "review"}},
                   {"name": "review", "who": {"role": "reviewer"}, "outcomes": {"approved": None, "rejected": "write"}}]}
    if stalled:
        d["steps"].append(dict(STALLED))
    return d


class Clock:
    def __init__(self):
        self.t = time.time() - 3600

    def __call__(self):
        return self.t


class Run:
    """The floor as ``workflows.floor`` runs it: the recorder ticks, then the floor."""

    def __init__(self, project, definition, **kw):
        o = project.orchestra
        write(o, definition)
        self.defs = load(o)
        assert self.defs.problems == [], self.defs.problems
        self.clock = Clock()
        self.recorder = Recorder.for_project(o, starts=self.defs.starts, walk=walker(self.defs.workflows))
        self.floor = core.Floor(o, Settings(tick=1, heartbeat=5, lease=20, return_after=10), clock=self.clock,
                                jobs=Jobs(), defs=self.defs, recorder=self.recorder, **kw)
        self.floor.start()
        self.o, self.annotated = o, set()

    def tick(self):
        self.recorder.tick()
        self.floor.tick()
        self.annotated = record_api.annotate(self.recorder, self.floor.s.items, self.floor.s.offers, self.annotated,
                                             self.floor.planner)
        self.floor.jobs.wait(timeout=30)

    def publish(self, source, type_, subject, **data):
        return Bus.for_project(self.o, source=source).publish(type_, data, subject=subject)

    def offers(self, subject, step="write"):
        evs, _ = Bus.for_project(self.o).after(0)
        return [e for e in evs if e["type"] == "offer.made" and e.get("subject") == subject
                and e["data"].get("step") == step]

    def fail(self, subject, summary="git push: Permission denied (publickey)"):
        m = self.offers(subject)[-1]["data"]
        self.publish("worker/1", "offer.claimed", subject, offer=m["offer"])
        self.tick()
        self.publish("worker/1", "offer.finished", subject, offer=m["offer"], outcome="blocked", summary=summary)
        self.tick()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def supervisor(project):
    return AgentStore(project, Events.for_project(project, source="supervisor"))


def retry(project, item, step, role="supervisor", reason=FIXED):
    args = {"item": item, "step": step, "reason": reason}
    tooling.check_args("retry_step", args)
    return tooling.handle(project, supervisor(project), "retry_step", args, role=role)


def test_the_tool_is_the_supervisors_in_its_workflows_group():
    assert BY_NAME["retry_step"].group == "workflows" and "workflows" in ROLES["supervisor"].tools
    assert set(BY_NAME["retry_step"].required) == {"item", "step", "reason"}
    assert "retry_step" in ledger.CHANGES


def test_a_blocked_step_is_offered_again_on_the_tick_after_the_act(project):
    run = Run(project, delivery())
    run.publish("owner", "item.requested", "SCRUM-56", workflow="delivery", brief="Fix the login page")
    run.tick()
    run.fail("SCRUM-56")
    assert run.floor.s.items["SCRUM-56"]["rest_until"] > run.floor.now          # resting: the back-off's first minute
    out = retry(project, "SCRUM-56", "write")
    assert out["ok"] and out["workflow"] == "delivery" and "Tell the owner what you fixed" in out["said"]
    run.tick()                                                                    # the same clock: no back-off passed
    assert len(run.offers("SCRUM-56")) == 2
    evs, _ = Bus.for_project(run.o).after(0)
    [act] = [e for e in evs if e["type"] == "item.retry_requested"]
    assert (act["source"], act["subject"], act["data"]["reason"], act["data"]["step"]) == ("supervisor", "SCRUM-56", FIXED, "write")
    assert ledger.did("retry_step", {"item": "SCRUM-56", "step": "write", "reason": FIXED}, out) == \
        f"asked the floor to offer SCRUM-56's write again now: {FIXED}"


def test_a_stalled_item_goes_back_to_the_step_it_was_moved_from(project):
    run = Run(project, delivery(stalled=True), retry_limit=1)
    run.publish("owner", "item.requested", "SCRUM-57", workflow="delivery", brief="Fix the logout page")
    run.tick()
    run.fail("SCRUM-57")
    run.tick()
    assert run.floor.positions["SCRUM-57"]["delivery"]["step"] == "stalled"
    retry(project, "SCRUM-57", "write")
    run.tick()
    assert run.floor.positions["SCRUM-57"]["delivery"]["step"] == "write" and len(run.offers("SCRUM-57")) == 2


def test_refused_for_an_item_not_blocked_or_stalled(project):
    run = Run(project, delivery())
    run.publish("owner", "item.requested", "SCRUM-58", workflow="delivery", brief="Fix the help page")
    run.tick()
    with pytest.raises(StoreError, match="neither blocked nor stalled at write"):
        retry(project, "SCRUM-58", "write")                                       # offered, never failed
    run.fail("SCRUM-58")
    with pytest.raises(StoreError, match="neither blocked nor stalled at review"):
        retry(project, "SCRUM-58", "review")
    with pytest.raises(StoreError, match="no open item"):
        retry(project, "SCRUM-99", "write")
    with pytest.raises(StoreError, match="refused retry_step"):
        retry(project, "SCRUM-58", "write", role="worker")
    with pytest.raises(StoreError, match="what you fixed"):
        retry(project, "SCRUM-58", "write", reason="again")
    evs, _ = Bus.for_project(run.o).after(0)
    assert not [e for e in evs if e["type"] == "item.retry_requested"]           # nothing published


NOW = datetime(2026, 11, 4, 13, 50, tzinfo=UTC).timestamp()


def test_the_digest_names_items_in_retry_and_the_stalled_ones_first():
    clock = since_digest.Clock("UTC", NOW)
    gave_up = {"type": "offer.finished", "subject": "SCRUM-60", "source": "worker/1", "ts": "2026-11-04T11:50:00+00:00",
               "offer": "SCRUM-60.write.1a2b3c4d", "outcome": "blocked", "summary": "git fetch refused (publickey)"}
    d = since_digest.Day([gave_up], clock)
    until = datetime(2026, 11, 4, 14, 5, tzinfo=UTC).isoformat()
    last = {"outcome": "blocked", "summary": "git push refused (publickey)", "at": "2026-11-04T13:05:00+00:00"}
    rows = [{"item": "SCRUM-56", "workflow": "delivery", "step": "write", "pile": "write.waiting", "age_s": 3 * 3600,
             "wait": {"reason": "attention raised", "by": "worker", "until": until}, "attempts": 3, "last_try": last,
             "retry": {**last, "failed": 3}},
            {"item": "SCRUM-60", "workflow": "delivery", "step": "stalled", "pile": "stalled.waiting", "age_s": 7200,
             "wait": {"reason": "awaiting", "by": "owner"}, "came_from": "write", "acts": {"who": "owner"}},
            {"item": "SCRUM-61", "workflow": "delivery", "step": "ci", "pile": "ci.waiting", "age_s": 9 * 3600,
             "wait": {"reason": "attention raised", "by": "supervisor"}}]
    found = since_digest.since_state.stuck(d, {"rows": rows})
    assert [k for k, _ in found][:2] == ["stalled", "in retry"]
    assert dict(found)["in retry"] == ["SCRUM-56 at write: 3 tries, next at 14:05, last: git push refused (publickey)"]
    assert dict(found)["stalled"] == ["SCRUM-60 at stalled 2 h, from write, last: git fetch refused (publickey)"]
    text = since_digest.first(d, {"rows": rows})
    lines = text.splitlines()
    assert lines[1].startswith("- Stalled: SCRUM-60 at stalled") and lines[2].startswith("- In retry: SCRUM-56 at write: 3 tries")
