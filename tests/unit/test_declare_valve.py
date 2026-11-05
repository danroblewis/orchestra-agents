"""``declare_valve`` (valve_tools.py) on the real Workflows plugin's reader and writer: the valve's
declaration and its program are written to the state directory and read back by the plugin, the
step is inserted into the workflow with each label routed and the fallback to the owner's step, the
answer's line states the daily cap, and a call without a cap, with a token's value or by another
role writes nothing."""

import json

import pytest
from conftest import ask_always
from design_fixtures import WHY, call, say

from orchestra_agents.store import StoreError
from orchestra_agents.valve.declaration import parse

pytest_plugins = ["design_fixtures"]
pytest.importorskip("orchestra_workflows.define")

ONCALL = {"name": "oncall", "items": "ticket", "planner": "supervisor",
          "starts": [{"event": "jira.issue.created", "fields": {"summary": {"data": "summary"}}}],
          "steps": [{"name": "respond", "who": {"outside": "jira"},
                     "done_when": {"event": "jira.issue.transitioned"}, "outcomes": {"done": None}},
                    {"name": "decide", "who": {"person": "owner"}, "done_when": {"event": "jira.issue.assigned"},
                     "outcomes": {"done": "respond"}}]}
VALVE = {"id": "oncall_filter", "question": "Does this ticket need the on-call engineer now?",
         "labels": ["page", "queue", "ignore"], "inputs": {"fields": ["summary"]}, "api": "anthropic",
         "base_url": "https://api.example.com", "model": "a-model", "token_file": "/secrets/model.token",
         "min_confidence": 0.7, "max_calls_per_day": 200, "reason": WHY}


def workflow(project, d=ONCALL):
    f = project.orchestra.state_dir / "workflows" / d["name"] / "workflow.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d))
    return f


def folder(project):
    return project.orchestra.state_dir / "machines" / "oncall_filter"


def test_the_valve_is_declared_and_inserted_before_the_first_step(project, store):
    f = workflow(project)
    out = call(project, store, "declare_valve", **VALVE, workflow="oncall", routes={"ignore": None})
    assert out["ok"] and out["author"] == "supervisor" and out["committed"] is False
    assert "at most 200 calls a day" in out["say"] and "a-model" in out["say"] and "ask" in out["say"]
    assert "not there yet" in out["token"]
    decl = json.loads((folder(project) / "valve.json").read_text())
    assert parse(decl)[0].labels == ("page", "queue", "ignore") and decl["fallback"] == "ask"
    body = json.loads((folder(project) / "machine.json").read_text())
    spec = body["spec"]
    assert spec["program"] == "valve" and spec["role"] == "oncall_filter"
    assert spec["args"] == ["--declaration", str(folder(project) / "valve.json")]
    assert spec["exit"] == {"1": "error", "2": "error", "10": "page", "11": "queue", "12": "ignore", "13": "ask"}
    assert "HTTPS_PROXY" in spec["env"]
    from orchestra_workflows.programs import machines
    progs, problems = machines.load(project.orchestra)
    assert [p.id for p in progs] == ["oncall_filter"] and not problems
    assert progs[0].command[1:4] == ("-I", "-m", "orchestra_agents.valve")
    steps = json.loads(f.read_text())["steps"]
    assert [s["name"] for s in steps] == ["oncall_filter", "respond", "decide"]
    assert steps[0] == {"name": "oncall_filter", "who": {"program": "oncall_filter"},
                        "outcomes": {"page": "respond", "queue": "respond", "ignore": None, "ask": "decide"}}
    assert out["workflow"]["status"].startswith("running")
    again = call(project, store, "declare_valve", **VALVE, workflow="oncall")
    assert again["replaced"] and "names the valve oncall_filter already" in again["workflow"]["unchanged"]


def test_a_workflow_without_an_owners_step_leaves_the_fallback_to_the_planner(project, store):
    workflow(project, {**ONCALL, "steps": ONCALL["steps"][:1]})
    out = call(project, store, "declare_valve", **VALVE, workflow="oncall", routes={"queue": None})
    assert "no person: owner step" in out["workflow"]["fallback"]
    assert "ask" not in out["workflow"]["step"]["outcomes"]


@pytest.mark.parametrize(("over", "match"), [
    ({"max_calls_per_day": None}, "missing max_calls_per_day"),
    ({"base_url": "https://api.example.com/?key=sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"}, "looks like a secret"),
    ({"labels": ["page"]}, "labels are the allowed answers"),
    ({"token_file": "model.token"}, "full path"),
    ({"api": "grpc"}, "api is one of"),
])
def test_what_the_declaration_refuses_writes_nothing(project, store, over, match):
    args = {k: v for k, v in {**VALVE, **over}.items() if v is not None}
    with pytest.raises(StoreError, match=match):
        call(project, store, "declare_valve", **args)
    assert not folder(project).exists()


def test_only_the_supervisor_declares_and_under_always_on_the_owners_yes(project, store):
    with pytest.raises(StoreError, match="refused declare_valve"):
        call(project, store, "declare_valve", role="coordinator", **VALVE)
    ask_always(project)
    with pytest.raises(StoreError, match="quote their yes"):
        call(project, store, "declare_valve", **VALVE)
    yes = "Yes, filter the tickets that don't need on-call, 200 calls a day"
    say(store, yes)
    out = call(project, store, "declare_valve", **VALVE, quote=yes)
    assert out["ok"] and out["author"] == "owner"
