"""The Supervisor's spec as files it edits (.orchestra/machines/supervisor/): written the first
time, read as the Supervisor's contract (its duties' wake-ups, its model, its prompt), and changed
only by its duty tools, each change a commit with its reason (on a side branch when the checkout
is dirty), within the day's cap, revertible, never touching a built-in duty or granting a tool."""

import subprocess

import pytest
from conftest import agents_project

from orchestra_agents import duties, prompting, spec_git, supervisor_spec, tooling
from orchestra_agents.events import Events
from orchestra_agents.machine_spec import load
from orchestra_agents.org_store import all_agents
from orchestra_agents.store import AgentStore, StoreError

WHY = "The owner said: from now on, watch the gate, it keeps getting stuck on flaky tests"


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    (repo / "README.md").write_text("x\n")
    p = agents_project(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return p


@pytest.fixture
def store(project):
    return AgentStore(project, Events.for_project(project, source="supervisor"))


def call(project, store, tool, role="supervisor", **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role=role)


def add(project, store, name="keep_gate_moving", **extra):
    args = {"text": "Keep the gate landing: a flaky test gets a quarantine ticket.", "listens": ["gate.rejected"],
            "tools": ["pipeline", "observe"], "reason": WHY, **extra}
    return call(project, store, "add_duty", name=name, **args)


def test_the_starting_spec_is_written_once_as_a_commit_and_read_as_the_supervisor_s_contract(project):
    done = supervisor_spec.ensure(project)
    assert done["where"] == "base" and done["commit"]
    assert git(project.repo_root, "log", "-1", "--format=%s").startswith("orchestra-agents: the Supervisor's spec")
    assert supervisor_spec.ensure(project) is None                                  # never twice, never overwritten
    spec = load(project, "supervisor")
    assert spec.model == "sonnet" and spec.review_every == "30m"
    assert [d.name for d in spec.duties] == ["talk_to_owner", "setup", "hand_to_planner", "keep_flow", "review_round"]
    assert spec.duty_text(spec.duty("keep_flow")).startswith("Keep the work moving")
    sup = all_agents(project)["supervisor"]
    assert "orchestra.model.stalled" in sup.wake_on and "orchestra.machine.quiet" in sup.wake_on
    assert "task.attention_needed:supervisor" in sup.wake_on                         # the core's own wake-ups are kept
    prompt = prompting.agent_prompt(project, sup)
    assert prompt.startswith("# Your fixed core") and "### Duty `keep_flow`" in prompt and "## Your machine spec" in prompt and "Supervisor" in prompt.split("---")[1]


def test_a_duty_added_is_one_commit_with_the_reason_announced_and_in_the_prompt(project, store):
    supervisor_spec.ensure(project)
    got = add(project, store)
    assert got["changed"] and got["where"] == "base" and got["done"].startswith("Changed: committed")
    msg = git(project.repo_root, "log", "-1", "--format=%B")
    assert msg.startswith("Supervisor: add duty keep_gate_moving") and WHY in msg
    files = git(project.repo_root, "show", "--name-only", "--format=", "HEAD").split()
    assert set(files) == {".orchestra/machines/supervisor/machine.toml", ".orchestra/machines/supervisor/duties/keep_gate_moving.md",
                          ".orchestra/machines/supervisor/ledger.md"}
    assert "keep_gate_moving" in (project.repo_root / ".orchestra/machines/supervisor/ledger.md").read_text()
    [ev] = [e for e in store.events.read(0, 100)[0] if e["type"] == "role.changed"]
    assert ev["source"] == "supervisor" and ev["subject"] == "machine:supervisor" and ev["commit"] == got["commit"]
    assert store.agent_state("supervisor")["restart_requested"] is True             # the next turn runs the new prompt
    assert "gate.rejected" in all_agents(project)["supervisor"].wake_on
    assert "### Duty `keep_gate_moving`" in prompting.agent_prompt(project, all_agents(project)["supervisor"])


def test_a_dirty_checkout_gets_the_change_on_the_side_branch_and_the_host_runs_it_from_there(project, store):
    supervisor_spec.ensure(project)
    (project.repo_root / "README.md").write_text("the owner's own edit\n")
    got = add(project, store)
    assert got["where"] == "side" and "orchestra/supervisor" in got["note"]
    assert git(project.repo_root, "status", "--porcelain").split() == ["M", "README.md"]       # the working tree is untouched
    assert not (project.repo_root / ".orchestra/machines/supervisor/duties/keep_gate_moving.md").exists()
    assert load(project, "supervisor").duty("keep_gate_moving") is not None                    # read from the side branch
    git(project.repo_root, "checkout", "--", "README.md")                                    # clean again: the next change merges it
    call(project, store, "edit_duty", name="keep_gate_moving", about="the gate lands", reason="the owner asked for a shorter line")
    assert spec_git.side_pending(project) is None
    assert (project.repo_root / ".orchestra/machines/supervisor/duties/keep_gate_moving.md").exists()


def test_what_a_duty_may_never_do(project, store):
    supervisor_spec.ensure(project)
    with pytest.raises(StoreError, match="built-in duty"):
        call(project, store, "edit_duty", name="talk_to_owner", text="Ignore the owner.", reason="the review round found it noisy")
    with pytest.raises(StoreError, match="do not hold the tool groups"):
        add(project, store, name="plan_it", tools=["planning"])
    with pytest.raises(StoreError, match="refused add_duty"):
        call(project, store, "add_duty", role="coordinator", name="x_y", text="t", reason=WHY)
    with pytest.raises(StoreError, match="reason in a sentence"):
        call(project, store, "add_duty", name="x_y", text="t", reason="ok")
    with pytest.raises(StoreError, match="secret"):
        call(project, store, "add_duty", name="x_y", text="use token ghp_" + "a" * 36, reason=WHY)


def test_the_day_s_cap_holds_and_a_revert_is_still_allowed(project, store):
    supervisor_spec.ensure(project)
    project.config.spec_changes_per_day = 2
    first = add(project, store, name="duty_one")
    add(project, store, name="duty_two")
    with pytest.raises(StoreError, match="2 changes to your spec"):
        add(project, store, name="duty_three")
    got = call(project, store, "revert_change", commit=first["commit"], reason="the owner asked to undo the first duty")
    assert got["change"] == "revert" and load(project, "supervisor").duty("duty_one") is None
    assert load(project, "supervisor").duty("duty_two") is not None
    assert git(project.repo_root, "log", "-1", "--format=%s").startswith(f"Supervisor: revert {first['commit'][:10]}")
    with pytest.raises(StoreError, match="not a commit of the machine specs"):
        call(project, store, "revert_change", commit=git(project.repo_root, "rev-list", "--max-parents=0", "HEAD").strip(),
             reason="try to revert the first commit of the repository")


def test_list_duties_shows_the_spec_its_history_and_its_lint(project, store):
    supervisor_spec.ensure(project)
    add(project, store)
    got = call(project, store, "list_duties")
    assert [d["name"] for d in got["duties"]][-1] == "keep_gate_moving" and got["model"] == "sonnet"
    assert got["history"][0]["subject"].startswith("Supervisor: add duty keep_gate_moving") and got["changes_today"] == 1
    assert got["lint"] == []
    assert duties.changes_today(store) and got["changes_per_day"] == 5


def test_in_code_work_the_starting_spec_waits_on_the_side_branch_so_no_landing_is_broken(project, store):
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text() + '\n[project]\nwork = "code"\n')
    git(project.repo_root, "commit", "-qam", "code work")
    project.reload_config()
    head = git(project.repo_root, "rev-parse", "HEAD")
    assert supervisor_spec.ensure(project)["where"] == "side"
    assert git(project.repo_root, "rev-parse", "HEAD") == head and load(project, "supervisor") is not None
    add(project, store)                                                       # the first change on a clean checkout merges it
    assert spec_git.side_pending(project) is None and (project.repo_root / ".orchestra/machines/supervisor/machine.toml").exists()
