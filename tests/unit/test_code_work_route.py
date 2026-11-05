"""T54, the owner's report of 2026-11-04 (Claude Code, Workflows and the bridges installed, no task
system): the Supervisor set up the Jira scripts, then said it could not do coding work, and the
owner was led into designing a new agent for it. With Workflows installed and no task system,
code work goes to the built-in worker as a Workflows item on a branch; only with neither installed
is it not handed on, and then the Supervisor names what is missing and the one step to get it.
A connected system with items is followed by the proposal of a workflow that makes them items."""

import re

import pytest
from conftest import supervisor_knows
from test_request_work import BRIEF, WHY, Plugin, published
from test_workflow_tools import DEF, MESSAGE

from orchestra_agents import (
    design_integrate,
    installed,
    prompting,
    skills,
    tasking_text,
    tooling,
    workflow_tools,
)
from orchestra_agents.builtins import ROLES
from orchestra_agents.harness.tools import tools_for
from orchestra_agents.org import built_in_agents

pytest_plugins = ["test_request_work"]          # its project, store and floor fixtures

QUOTE = MESSAGE.split()[0] + " " + " ".join(MESSAGE.split()[1:4])
MARKERS = re.compile(r"\[\[/?(no-)?(tasking|workflows)\]\]")


def installs(monkeypatch, tasking: bool, workflows: bool):
    monkeypatch.setattr(installed, "tasking", lambda: tasking)
    monkeypatch.setattr(installed, "workflows", lambda: workflows)


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_gate_has_a_third_condition_workflows_without_the_task_system():
    text = "[[tasking]]T[[/tasking]][[no-tasking]]N[[/no-tasking]][[workflows]]W[[/workflows]][[no-workflows]]X[[/no-workflows]]"
    assert tasking_text.gate(text, True, True) == "T"
    assert tasking_text.gate(text, True, False) == "T"
    assert tasking_text.gate(text, False, True) == "NW"
    assert tasking_text.gate(text, False, False) == "NX"


def test_with_workflows_and_no_task_system_code_work_goes_to_a_worker(project, monkeypatch):
    installs(monkeypatch, tasking=False, workflows=True)
    knows = supervisor_knows(project)
    prompt = flat(prompting.agent_prompt(project, built_in_agents()["supervisor"]))
    tools = " ".join(str(t.to_dict()) for t in tools_for(ROLES["supervisor"].tools))
    for text in (knows, tools):
        assert "not handed on" not in text and not MARKERS.search(text)
    assert "as a Workflows item on a branch in a worktree (skill `code-work`)" in prompt
    assert "never say you cannot do it" in prompt and "never design a new agent for it" in prompt
    assert "code-work" in skills.indexed()
    route = flat(skills.text(project, "code-work"))
    assert "`set_agent_roles`" in route and "same model" in route                    # the role filled, on the owner's yes
    assert '"person": "owner"' in route and '"needs": ["worktree"]' in route        # the minimal workflow
    assert '`role: "worker"`' in route and "One-off work" in route                  # else a one-off item
    assert "allow_push" in route and "branch" in route and "nothing is pushed" in route
    assert "the worker role already does this and offer to fill it instead" in route
    for name in ("setup", "handle-request", "cannot-do", "gave-up"):
        assert "Workflows item" in skills.text(project, name), name
    assert "`edit_machine_file`" in skills.text(project, "connect-system")       # a poller's script is setup


def test_with_neither_installed_the_supervisor_names_what_is_missing(project, monkeypatch):
    installs(monkeypatch, tasking=False, workflows=False)
    knows = supervisor_knows(project)
    assert not MARKERS.search(knows) and "Workflows item" not in knows
    prompt = flat(prompting.agent_prompt(project, built_in_agents()["supervisor"]))
    assert "installing Workflows is the one step; never that you are not capable" in prompt
    route = flat(skills.text(project, "code-work"))
    assert "what is missing (the Workflows plugin" in route and "the one step that gets it" in route
    assert "Never say you are not capable" in route
    for name in ("setup", "handle-request", "cannot-do", "gave-up"):
        assert "installing Workflows is the one step" in flat(skills.text(project, name)) or \
            "the one step that gets it" in flat(skills.text(project, name)), name


def test_with_the_task_system_the_route_skill_is_not_indexed(project, monkeypatch):
    installs(monkeypatch, tasking=True, workflows=True)
    assert "code-work" not in skills.indexed()
    assert "Workflows item" not in prompting.agent_prompt(project, built_in_agents()["supervisor"])


def test_a_connected_system_with_items_is_followed_by_an_observing_workflow(project, monkeypatch):
    for tasking in (True, False):
        installs(monkeypatch, tasking=tasking, workflows=True)
        text = flat(skills.text(project, "connect-system"))
        assert "propose in one sentence a workflow that makes them items" in text
        assert "each step's count and the moves between steps" in text and "discovery finds" in text
        assert "writes nothing to Jira" in text and '{"outside": "<system>"}' in text and "no `outbox` step" in text
    line = design_integrate.items_follow_up("Jira")
    assert "observe only, writing nothing to Jira" in line and "each step's count and the moves between steps" in line


def handle(project, store, a):
    tooling.check_args("request_work", a)
    return tooling.handle(project, store, "request_work", a, role="supervisor")


@pytest.fixture
def fake(monkeypatch, project):
    f = Plugin()
    monkeypatch.setattr(workflow_tools, "plugin", f.ns)
    f.write(project.orchestra, DEF)
    return f


def test_one_off_code_work_is_a_worker_item_needing_a_worktree(project, store, fake, floor):
    end = store.events.end()
    out = handle(project, store, {"role": "worker", "needs": ["worktree"], "brief": BRIEF, "quote": QUOTE, "reason": WHY})
    item = out["item"]
    assert out["ok"] and item.startswith("worker-") and out["role"] == "worker" and out["source"] == "owner"
    assert f"workflows/{item}-" in out["branch"] and "nothing is pushed" in out["branch"] and "unfilled" not in out
    assert out["say"].endswith("Name the branch and say that nothing is pushed.")
    (req,) = published(store, end)
    assert {k: req[k] for k in ("role", "brief", "needs", "planner", "via")} == {
        "role": "worker", "brief": BRIEF, "needs": ["worktree"], "planner": "supervisor", "via": "supervisor"}
    assert "workflow" not in req and req["source"] == "owner"


def test_one_off_work_says_when_no_agent_fills_the_role_and_when_it_may_push(project, store, fake, floor, monkeypatch):
    monkeypatch.setattr(project, "reload_config", lambda: None)            # the settings below, as the owner's would be
    project.config.serves = []
    project.config.allow_push = ["worker"]
    out = handle(project, store, {"role": "worker", "needs": ["worktree"], "brief": BRIEF, "quote": QUOTE, "reason": WHY})
    assert "set_agent_roles" in out["unfilled"] and "may push" in out["branch"]


def test_one_off_work_takes_a_role_or_a_workflow_never_both(project, store, fake, floor):
    end = store.events.end()
    with pytest.raises(Exception, match="not both"):
        handle(project, store, {"role": "worker", "workflow": "write_review", "brief": BRIEF, "quote": QUOTE, "reason": WHY})
    with pytest.raises(Exception, match="role \\(worker"):
        handle(project, store, {"brief": BRIEF, "quote": QUOTE, "reason": WHY})
    with pytest.raises(Exception, match="owner's to ask"):
        handle(project, store, {"role": "worker", "needs": ["worktree"], "brief": BRIEF, "reason": WHY})
    assert published(store, end) == []


def test_the_scripted_supervisor_hands_a_chat_to_a_worker_not_a_refusal(project, store, fake, floor, tmp_path):
    """The dummy harness's own rule for a chat with Workflows and no task system, played as its turn
    plays it, against the tool: a one-off worker item on the owner's words, never a refusal."""
    import json

    from orchestra_agents.harness import dummy_agent, dummy_script
    from orchestra_agents.harness.dummy_script import Turn
    end = store.events.end()
    turn = Turn(role="supervisor", prompt=f"[Orchestra: ...]\n\n[The human's message]\n{MESSAGE}", tasking=False, workflows=True)
    rule = dummy_script.load(None).pick(turn)
    agent = dummy_agent.Agent(tmp_path / "t.jsonl", tmp_path)
    agent.tool = lambda name, args: (json.dumps(handle(project, store, args)), False)
    said = json.loads(agent.play(rule, turn))
    (req,) = published(store, end)
    assert said["ok"] and said["role"] == "worker" and req["role"] == "worker" and req["needs"] == ["worktree"]
    assert req["source"] == "owner" and MESSAGE in req["brief"]
