"""T54 addendum (the owner, 2026-11-04, no task system): "The Supervisor says it sent a change to
the Coordinator, and the Coordinator shows up in the Agents page, but it says it's down." Without
the task system no Coordinator runs, so nothing hands work to it: every hand-off refuses before
publishing and names the routes that exist; the built-in hand_to_planner duty is not in force; and
the Agents page says the Coordinator is not used, never down."""

import json
import re
import shutil
import subprocess

import pytest
from design_fixtures import WHY, call, say
from test_integrate import asked, connect, scaffolded

from orchestra_agents import installed, integrate_kits, org_views, prompting, supervisor_spec
from orchestra_agents.org import built_in_agents
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

ROUTES = ("no Coordinator runs here", "edit_machine_file", "request_work with role worker")


@pytest.fixture
def no_tasking(monkeypatch):
    monkeypatch.setattr(installed, "tasking", lambda: False)
    monkeypatch.setattr(installed, "workflows", lambda: True)


def test_a_hand_off_refuses_before_publishing_and_names_the_routes(project, store, no_tasking):
    end = store.events.end()
    with pytest.raises(StoreError) as e:
        call(project, store, "hand_to_coordinator", requirements="Change the poller to read every five minutes, tested.",
             context="the owner asked")
    assert all(r in str(e.value) for r in ROUTES)
    assert not [x for x in store.events.read(end, 100)[0] if x["type"] == "work.requested"]


def test_propose_patch_refuses_before_any_branch(project, store, no_tasking):
    project.config.work = "code"
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
    with pytest.raises(StoreError, match="no Coordinator runs here"):
        call(project, store, "propose_patch", name="fix_x", diff=diff, why="the total is off by one on every call",
             reason=WHY)
    branches = subprocess.run(["git", "branch", "--list", "supervisor/*"], cwd=project.repo_root, capture_output=True,
                              text=True, check=False).stdout
    assert branches.strip() == ""


def test_a_failed_kit_test_is_the_supervisors_to_fix_never_handed_on(project, store, no_tasking, monkeypatch):
    monkeypatch.setattr(integrate_kits, "run_test", lambda target, name, **_: {
        "ran": True, "passed": False, "summary": "1 failed", "command": "pytest", "tail": ["E assert"]})
    got = connect(project, store)
    assert got["integrated"] is False and "request" not in got and asked(store) == []
    assert scaffolded(project).is_dir()                              # left where it is, for edit_machine_file
    said = " ".join(got["said"]) + got["next"]
    assert "edit_machine_file" in said and "Coordinator has it" not in said


def test_without_tasking_hand_to_planner_is_not_in_force(project, store, no_tasking, monkeypatch):
    supervisor_spec.ensure(project)
    prompt = prompting.agent_prompt(project, built_in_agents()["supervisor"])
    assert "hand_to_planner" not in prompt and "Coordinator" not in prompt
    say(store, "list your duties please")
    duties = {d["name"]: d for d in call(project, store, "list_duties")["duties"]}
    assert "not in force" in duties["hand_to_planner"]["inactive"]
    monkeypatch.setattr(installed, "tasking", lambda: True)
    assert "hand_to_planner" in prompting.agent_prompt(project, built_in_agents()["supervisor"])


def test_the_agents_page_shows_the_coordinator_as_not_used_never_down(project, store, no_tasking):
    agents = {a["name"]: a for a in org_views.agents_status(project, store)["agents"]}
    assert agents["coordinator"]["not_used"] == "needs the task system" and "not_used" not in agents["supervisor"]
    view = org_views.agent_view(project, store, built_in_agents()["coordinator"])
    assert view["not_used"] == "needs the task system"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the page's status function")
def test_the_pages_status_text_for_an_agent_not_used(tmp_path):
    from pathlib import Path

    from orchestra_agents import web
    text = (Path(web.__file__).parent / "static" / "agents.js").read_text(encoding="utf-8")
    fn = re.search(r"function agentStatus\(s, now, a\) \{.*?\n\}", text, re.DOTALL).group(0)
    script = tmp_path / "t.js"
    script.write_text(fn + "\nconsole.log(JSON.stringify(agentStatus(null, 0, {resident: true, host_alive: false, "
                      "not_used: 'needs the task system'})));\n")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, check=True).stdout)
    assert out["text"] == "not used: needs the task system" and out["cls"] != "down"
