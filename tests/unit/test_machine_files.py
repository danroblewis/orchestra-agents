"""``edit_machine_file`` (T54, the owner, 2026-11-04: the Supervisor setting up its Jira poller
refused to change the poller's script, since without the task system nothing else could): it writes
only Orchestra's own files, the repository's on the Supervisor's branch with the checkout untouched,
the state directory's in place; a secret is refused; a kit's poller has its test run and is
restarted; revert_change puts a file back."""

import os
import subprocess

import pytest
from test_request_work import WHY

from orchestra_agents import integrate_kits, restart, side_branch, tooling
from orchestra_agents.design import MACHINE
from orchestra_agents.design_integrate import KEY
from orchestra_agents.harness.tools import BY_NAME
from orchestra_agents.store import StoreError

pytest_plugins = ["test_request_work"]          # its project and store fixtures

SCRIPT = "print('poll')\n"


def edit(project, store, **a):
    a = {"reason": WHY, **a}
    tooling.check_args("edit_machine_file", a)
    return tooling.handle(project, store, "edit_machine_file", a, role="supervisor")


def git(project, *args):
    return subprocess.run(["git", *args], cwd=project.repo_root, capture_output=True, text=True, check=False).stdout


@pytest.fixture
def ran(monkeypatch):
    calls = {"tests": [], "restarts": []}

    def run_test(target, name, timeout_s=0):
        calls["tests"].append((name, (target / f"{name}.py").read_text()))
        return {"ran": True, "passed": "broken" not in (target / f"{name}.py").read_text(), "summary": "1 passed"}

    def restart_machine(project, store, a, author, role):
        calls["restarts"].append(a["name"])
        return {"restarted": a["name"], "pid": 2}

    monkeypatch.setattr(integrate_kits, "run_test", run_test)
    monkeypatch.setattr(restart, "restart_machine", restart_machine)
    return calls


def poller(project, store, root="state"):
    """A kit's poller, as integrate_system leaves it: its directory and its record."""
    d = (project.orchestra.state_dir / "machines" / "jira_poller" if root == "state"
         else project.repo_root / ".orchestra" / "machines" / "jira_poller")
    d.mkdir(parents=True, exist_ok=True)
    (d / "jira_poller.py").write_text(SCRIPT)
    (d / "test_jira_poller.py").write_text("def test_it():\n    pass\n")
    store.update_agent_state(MACHINE, KEY, lambda x: {**(x or {}), "jira_poller": {"name": "jira_poller", "dir": str(d),
                                                                                 "root": root, "system": "jira"}})
    return d


def test_the_tool_is_the_supervisors_setup_tool():
    assert BY_NAME["edit_machine_file"].group == "setup"
    assert BY_NAME["edit_machine_file"].input_schema["required"] == ["path", "reason"]


@pytest.mark.parametrize("path", ["machines/feed/feed.py", "workflows/jira_tickets/workflow.json", "resources/worktree/kind.json"])
def test_the_state_directorys_places_are_written_in_place(project, store, path, ran):
    out = edit(project, store, path=path, content="{}\n")
    f = project.orchestra.state_dir / path
    assert out["ok"] and out["where"] == "state" and f.read_text() == "{}\n" and out["revert"].startswith("edit-")


@pytest.mark.parametrize("path", [
    "../outside.py", "machines/../../outside.py", ".orchestra/plugins/p.py", ".orchestra/overrides/o.py",
    ".orchestra/machines/supervisor/machine.toml", "machines/supervisor/role.md", "app/main.py", "orchestra.toml",
    ".orchestra/dummy.toml", "workflows/jira_tickets/record.jsonl", "workflows/floor.json", "agents/runtime.json",
    "/etc/hosts"])
def test_every_other_path_is_refused(project, store, path):
    with pytest.raises(StoreError, match="refused"):
        edit(project, store, path=path, content="x\n")


def test_the_projects_own_source_is_refused_by_any_spelling(project, store):
    (project.repo_root / "app").mkdir()
    (project.repo_root / "app" / "main.py").write_text("x = 1\n")
    for path in (str(project.repo_root / "app" / "main.py"), str(project.repo_root / ".orchestra" / ".." / "app" / "main.py")):
        with pytest.raises(StoreError, match="refused"):
            edit(project, store, path=path, content="x = 2\n")
    assert (project.repo_root / "app" / "main.py").read_text() == "x = 1\n"


def test_a_symlink_out_is_refused(project, store, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (project.repo_root / ".orchestra" / "machines").mkdir(parents=True, exist_ok=True)
    os.symlink(tmp_path / "elsewhere", project.repo_root / ".orchestra" / "machines" / "out")
    os.symlink(project.repo_root / "src", project.orchestra.state_dir / "machines_link")
    with pytest.raises(StoreError, match="refused"):
        edit(project, store, path=".orchestra/machines/out/x.py", content="x\n")
    with pytest.raises(StoreError, match="refused"):
        edit(project, store, path="machines_link/x.py", content="x\n")
    assert not (tmp_path / "elsewhere" / "x.py").exists()


def test_a_repository_file_lands_on_the_branch_and_the_checkout_is_untouched(project, store):
    head, status = git(project, "rev-parse", "HEAD"), git(project, "status", "--porcelain")
    out = edit(project, store, path=".orchestra/workflows/jira_tickets/workflow.json", content='{"name": "jira_tickets"}\n')
    rel = ".orchestra/workflows/jira_tickets/workflow.json"
    assert out["commit"] and out["branch"] == side_branch.SIDE and out["live"] is False
    assert git(project, "show", f"{side_branch.SIDE}:{rel}") == '{"name": "jira_tickets"}\n'
    assert not (project.repo_root / rel).exists()
    assert git(project, "rev-parse", "HEAD") == head and git(project, "status", "--porcelain") == status


def test_a_token_is_refused_and_nothing_written(project, store):
    with pytest.raises(StoreError, match="secret"):
        edit(project, store, path="machines/feed/feed.py", content="TOKEN = 'Bearer abcdefghijklmnop1234'\n")
    assert not (project.orchestra.state_dir / "machines" / "feed" / "feed.py").exists()


def test_a_kit_poller_is_tested_then_restarted_and_reverted(project, store, ran):
    d = poller(project, store)
    out = edit(project, store, path="machines/jira_poller/jira_poller.py", old="print('poll')", new="print('poll v2')")
    assert (d / "jira_poller.py").read_text() == "print('poll v2')\n"
    assert ran["tests"] == [("jira_poller", "print('poll v2')\n")] and out["test"]["passed"]
    assert ran["restarts"] == ["jira_poller"] and out["restart"]["ok"]
    back = tooling.handle(project, store, "revert_change", {"commit": out["revert"], "reason": WHY}, role="supervisor")
    assert back["reverted"] == out["revert"] and (d / "jira_poller.py").read_text() == SCRIPT


def test_a_failed_kit_test_is_reported_and_the_machine_not_restarted(project, store, ran):
    poller(project, store)
    out = edit(project, store, path="machines/jira_poller/jira_poller.py", content="broken(\n")
    assert out["test"]["passed"] is False and ran["restarts"] == [] and "test failed" in out["restart"]["skipped"]


def test_a_repository_pollers_live_copy_is_written_with_its_commit(project, store, ran):
    d = poller(project, store, root="repository")
    exclude = project.repo_root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("/.orchestra/machines/jira_poller/\n")
    out = edit(project, store, path=".orchestra/machines/jira_poller/jira_poller.py", content="print('v3')\n")
    assert out["live"] and (d / "jira_poller.py").read_text() == "print('v3')\n"
    assert git(project, "show", f"{side_branch.SIDE}:.orchestra/machines/jira_poller/jira_poller.py") == "print('v3')\n"
    assert ran["restarts"] == ["jira_poller"]


def test_old_must_occur_once(project, store):
    edit(project, store, path="machines/feed/feed.py", content="a\na\n")
    with pytest.raises(StoreError, match="exactly once"):
        edit(project, store, path="machines/feed/feed.py", old="a", new="b")


def test_a_scripted_supervisor_without_the_task_system_changes_its_jira_pollers_script(project, store, ran, tmp_path,
                                                                                        monkeypatch):
    """The dummy harness's turn, its script calling the tool as a Supervisor would, with no task system."""
    import json

    from orchestra_agents import installed
    from orchestra_agents.harness import dummy_agent, dummy_script
    from orchestra_agents.harness.dummy_script import Turn
    monkeypatch.setattr(installed, "tasking", lambda: False)
    d = poller(project, store)
    script = tmp_path / "dummy.toml"
    script.write_text('''
[[rule]]
role = "supervisor"
prompt = "change the jira poller"
tasking = false
steps = [{ tool = "edit_machine_file", args = { path = "machines/jira_poller/jira_poller.py", old = "print('poll')", new = "print('poll every 5m')", reason = "the owner asked for a five-minute poll" }, keep = "e" }]
reply = "Changed it ({kept.e.revert}); its test passed: {kept.e.test.passed}; restarted: {kept.e.restart.ok}."
''')
    turn = Turn(role="supervisor", prompt="[The human's message]\nPlease change the jira poller to poll every 5m", tasking=False)
    rule = dummy_script.load(script).pick(turn)
    agent = dummy_agent.Agent(tmp_path / "t.jsonl", tmp_path)
    agent.tool = lambda name, args: (json.dumps(tooling.handle(project, store, name, args, role="supervisor")), False)
    reply = agent.play(rule, turn)
    assert (d / "jira_poller.py").read_text() == "print('poll every 5m')\n"
    assert "its test passed: True; restarted: True" in reply and ran["restarts"] == ["jira_poller"]
