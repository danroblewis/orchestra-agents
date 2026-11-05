"""T2c steps 1, 10 and 15: the Supervisor told the owner that the machines the Workflows plugin
proposed "don't run" (each was heard on the bus) and that the nudger's program still needed writing
"by the Coordinator" (it ran and sent the day's reminder, and no task system was installed). The
tools now say how each declared machine runs, and without the task system no prompt or tool text
names a Coordinator."""

import json
import re
from types import SimpleNamespace

import pytest
from conftest import supervisor_knows
from design_fixtures import WHY, call, say

from orchestra_agents import (
    installed,
    intended_api,
    machines_read,
    machines_running,
    tasking_text,
    workflow_tools,
)
from orchestra_agents.builtins import ROLES
from orchestra_agents.harness.tools import tools_for

pytest_plugins = ["design_fixtures"]

PROPOSED = {"author": "workflows", "source": "workflow review_nudger", "reason": "the definition of workflow review_nudger"}


@pytest.fixture
def no_tasking(monkeypatch):
    monkeypatch.setattr(installed, "tasking", lambda: False)
    monkeypatch.setattr(installed, "workflows", lambda: False)      # neither: code work not handed on (test_code_work_route)


def fake_plugin(monkeypatch, steps: dict[str, list[tuple[str, str]]]):
    """The Workflows plugin's loaded definitions: workflow -> [(actor kind, actor name)]."""
    wfs = [SimpleNamespace(name=w, steps=[SimpleNamespace(actor=SimpleNamespace(kind=k, name=n)) for k, n in ss])
           for w, ss in steps.items()]
    define = SimpleNamespace(load=lambda o: SimpleNamespace(workflows=wfs, problems=[], notes=[]))
    monkeypatch.setattr(workflow_tools, "plugin", lambda: SimpleNamespace(define=define))


def test_workflow_programs_are_the_steps_program_actors(project, monkeypatch):
    fake_plugin(monkeypatch, {"review_nudger": [("program", "review_nudger"), ("outbox", "owner")],
                              "delivery_live": [("program", "has_ticket"), ("role", "worker"), ("outside", "github")]})
    assert machines_running.workflow_programs(project) == {"review_nudger": ["review_nudger"], "has_ticket": ["delivery_live"]}
    monkeypatch.setattr(workflow_tools, "plugin", lambda: None)
    assert machines_running.workflow_programs(project) == {}


def test_list_machines_says_a_heard_proposal_runs_and_a_workflows_program_runs_as_it(project, store, monkeypatch):
    fake_plugin(monkeypatch, {"review_nudger": [("program", "review_nudger"), ("outbox", "owner")]})
    intent = {"state": "proposed", "author": "workflows", "heard": True}
    answer = {"ok": True, "machines": [
        {"id": "worker", "kind": "agent", "mode": "ad hoc", "active": True, "last_seen": 1000.0, "about": {}, "intent": intent},
        {"id": "review_nudger", "kind": "program", "mode": "ad hoc", "active": False, "last_seen": 1000.0, "about": {},
         "intent": intent},
        {"id": "slack_notify", "kind": "program", "mode": "continuous", "active": True, "last_seen": 1000.0, "about": {}}]}
    monkeypatch.setattr(machines_read, "view", lambda store: answer)
    out = call(project, store, "list_machines")
    by = {m["id"]: m for m in out["machines"]}
    assert re.fullmatch(r"running: heard .+, proposed by the workflows plugin", by["worker"]["running"])
    assert by["worker"]["declared"] == "proposed by the workflows plugin"
    assert by["review_nudger"]["running"].startswith("runs as the workflow's program (review_nudger); heard ")
    assert "running" not in by["slack_notify"] and "declared" not in by["slack_notify"]
    line = next(x for x in out["summary"] if x.startswith("worker"))
    assert "running: heard" in line and "proposed by the workflows plugin" in line
    assert "never a switch that stops it" in out["note"]
    text = " ".join(str(v) for m in out["machines"] for v in m.values())
    assert "not run" not in text and "never runs" not in text


def test_read_design_says_how_each_declared_machine_runs(project, store, monkeypatch):
    fake_plugin(monkeypatch, {"review_nudger": [("program", "review_nudger")]})
    intended_api.layer(project).apply(
        [{"op": "propose_machine", "id": mid, "title": mid, "kind": kind, "description": f"Named by workflow review_nudger: {mid}.",
          "engagement": {"mode": "ad hoc"}} for mid, kind in
         (("review_nudger", "programmatic"), ("sail_triage", "programmatic"), ("lonely", "universal"))], PROPOSED)
    store.events.publish("offer.finished", {"outcome": "local"}, source="sail_triage/1")
    out = call(project, store, "read_design")
    runs = {k: m["running"] for k, m in out["machines"].items()}
    assert runs["review_nudger"].startswith("runs as the workflow's program (review_nudger)")
    assert re.fullmatch(r"running: heard .+, proposed by the workflows plugin", runs["sail_triage"])
    assert runs["lonely"] == machines_running.NOT_HEARD


def test_confirming_a_workflows_program_hands_nothing_on(project, store, monkeypatch, no_tasking):
    """T2c s10: confirm said "the program is a worker's to write (hand_to_coordinator)" of the
    nudger, which the workflow's program host ran."""
    fake_plugin(monkeypatch, {"review_nudger": [("program", "review_nudger")]})
    intended_api.layer(project).apply(
        [{"op": "propose_machine", "id": "review_nudger", "title": "review_nudger", "kind": "programmatic",
          "description": "Named by workflow review_nudger: the program review_nudger.", "receives": ["offer.made"],
          "engagement": {"mode": "ad hoc"}}], PROPOSED)
    d = project.orchestra.state_dir / "machines" / "review_nudger"
    d.mkdir(parents=True)
    (d / "machine.json").write_text(json.dumps({"id": "review_nudger", "kind": "programmatic", "managed": "orchestra",
                                                "spec": {"command": "true", "role": "review_nudger"}}))
    say(store, "Then confirm it", 1)
    out = call(project, store, "confirm", element="review_nudger", quote="Then confirm it", reason=WHY)
    said = " ".join(out["said"])
    assert "runs as the workflow's program (review_nudger)" in said and "its machine.json is declared" in said
    assert "Coordinator" not in said and "hand_to_coordinator" not in said and "never heard" not in said


def test_confirming_a_workflows_program_with_no_machine_json_says_the_step_waits_for_it(project, store, monkeypatch,
                                                                                       no_tasking):
    """Bootstrap run 7, finding 1: confirm said "nothing is to be written" of relate_prs, whose
    machine.json no root held, so every pull request waited at its first step."""
    fake_plugin(monkeypatch, {"pull_request": [("program", "relate_prs")]})
    intended_api.layer(project).apply(
        [{"op": "propose_machine", "id": "relate_prs", "title": "relate_prs", "kind": "programmatic",
          "description": "Named by workflow pull_request: the program relate_prs.", "engagement": {"mode": "ad hoc"}}],
        PROPOSED)
    say(store, "Then confirm it", 1)
    out = call(project, store, "confirm", element="relate_prs", quote="Then confirm it", reason=WHY)
    said = " ".join(out["said"])
    assert "nothing is to be written" not in said and out["waits_for_program"] == "relate_prs"
    assert "waits for its program" in said and "machines/relate_prs/machine.json" in said and "declare_program" in said
    assert "not running" in said


def test_the_prompt_says_a_proposal_may_run(project):
    text = " ".join(supervisor_knows(project).split())
    assert "nothing runs only for a machine never heard on the bus and with no workflow behind it" in text
    assert "say a machine does not run only when `running` says it is not heard and no workflow runs it" in text
    assert "its program still needs writing" in text


def test_without_the_task_system_no_text_names_a_coordinator(project, store, no_tasking):
    """T2d s13: after request_work failed, "I did not send the request another way, such as through
    the Coordinator": the agents guide's Workflows section (its "start the task loop" example) still
    sent a request no workflow fits to the Coordinator."""
    prompt = supervisor_knows(project)
    tools = " ".join(str(t.to_dict()) for t in tools_for(ROLES["supervisor"].tools))
    flows = call(project, store, "read_guide", name="agents", section="Workflows")["text"]
    start = call(project, store, "read_guide", name="agents", section="Starting a piece of work")["text"]
    assert "request_work" in start and "start the task loop" in start and "a workflow to declare" in " ".join(start.split())
    whole = call(project, store, "read_guide", name="agents")["text"]
    assert "[[" not in whole.replace("[[machines]]", "").replace("[[collect]]", "").replace("[[scrape]]", "").replace(
        "[[sources]]", "")
    for text in (prompt, tools, flows):
        assert "coordinator" not in text.lower()
        assert "[[tasking]]" not in text and "[[no-tasking]]" not in text and "[[/" not in text
    assert "not handed on here: no task system or Workflows plugin is installed; say so" in " ".join(prompt.split())


def test_with_the_task_system_the_coordinator_is_named(project, monkeypatch):
    monkeypatch.setattr(installed, "tasking", lambda: True)
    prompt = supervisor_knows(project)
    tools = {t.name: t for t in tools_for(ROLES["supervisor"].tools)}
    assert "hand it to the Coordinator" in " ".join(prompt.split())
    assert "code work is not handed on here" not in prompt and not re.search(r"\[\[/?(no-)?tasking\]\]", prompt)
    assert "the Coordinator is" in tools["propose_patch"].description and "[[" not in tools["propose_patch"].description


def test_with_the_task_system_the_guide_sends_unfitting_work_to_the_coordinator(project, store, monkeypatch):
    monkeypatch.setattr(installed, "tasking", lambda: True)
    start = " ".join(call(project, store, "read_guide", name="agents", section="Starting a piece of work")["text"].split())
    assert "it is a ticket for the Coordinator (`hand_to_coordinator`), or a workflow to declare." in start
    assert "not handed on here" not in start


def test_gate_keeps_one_side():
    text = "a [[tasking]]with[[/tasking]][[no-tasking]]without[[/no-tasking]] b [[collect]]"
    assert tasking_text.gate(text, True) == "a with b [[collect]]"
    assert tasking_text.gate(text, False) == "a without b [[collect]]"
    assert tasking_text.gate_schema({"x": {"description": text, "enum": ["[[tasking]]"]}}, False) == \
        {"x": {"description": "a without b [[collect]]", "enum": ["[[tasking]]"]}}
