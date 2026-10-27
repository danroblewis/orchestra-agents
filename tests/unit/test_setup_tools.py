"""The Supervisor's configuration tools on a scratch repository: each change is a commit of
orchestra.toml alone, with the reason, announced on the bus, answered with what applies; a change
that cannot be committed, or that Orchestra's readers would refuse, leaves nothing behind."""

import subprocess
import tomllib

import pytest
from conftest import agents_project

from orchestra_agents import tooling
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore, StoreError

TOML_TAIL = """
# the task system
[project]
base_branch = "main"
ticket_prefix = "DEMO"
work = "tasks"           # a scratch folder per ticket

[tasks]
workers = 1
reviewers = 1

# a program worker that only runs commands
[[machines]]
name = "shell_worker"
command = "python machines/shell_worker.py"
"""
WHY = "the owner's measurement needs a worker that can write code"


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    (repo / "README.md").write_text("x")
    p = agents_project(repo)
    toml = repo / "orchestra.toml"
    toml.write_text(toml.read_text().replace('serves = ["worker", "reviewer"]', 'serves = ["reviewer"]') + TOML_TAIL)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    return p


@pytest.fixture
def store(project):
    return AgentStore(project, Events.for_project(project, source="supervisor"))


def call(project, store, tool, role="supervisor", **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role=role)


def toml(project) -> dict:
    return tomllib.loads((project.repo_root / "orchestra.toml").read_text())


def last_commit(project) -> tuple[str, list[str]]:
    msg = git(project.repo_root, "log", "-1", "--format=%B")
    files = git(project.repo_root, "show", "--name-only", "--format=", "HEAD").split()
    return msg, files


def changed_events(store) -> list[dict]:
    evs, _ = store.events.read(0, 1000)
    return [e for e in evs if e["type"] == "config.changed"]


def test_set_agent_roles_commits_the_file_alone_with_the_reason_and_announces_it(project, store):
    (project.repo_root / "notes.txt").write_text("someone's work in progress")      # never swept into the commit
    before = (project.repo_root / "orchestra.toml").read_text()
    out = call(project, store, "set_agent_roles", serves=["worker", "reviewer"], reason=WHY)
    assert out["ok"] and out["commit"] and "Live" in out["applies"]
    assert toml(project)["agents"]["serves"] == ["worker", "reviewer"]
    text = (project.repo_root / "orchestra.toml").read_text()
    assert "# a program worker that only runs commands" in text and "# a scratch folder per ticket" in text   # comments kept
    assert len(text.splitlines()) == len(before.splitlines())                     # only the one line changed
    msg, files = last_commit(project)
    assert files == ["orchestra.toml"] and WHY in msg and msg.startswith("Supervisor: the AI agents fill worker, reviewer")
    assert git(project.repo_root, "status", "--porcelain").strip() == "?? notes.txt"
    assert call(project, store, "check_setup")["roles"][0].startswith("The AI agents here fill: worker, reviewer")   # read afresh
    [ev] = changed_events(store)
    assert ev["source"] == "supervisor" and ev["section"] == "agents" and ev["reason"] == WHY
    assert ev["commit"] == out["commit"] and ev["applies"] == out["applies"]


def test_leaving_a_role_to_nobody_says_offers_will_wait(project, store):
    out = call(project, store, "set_agent_roles", serves=["worker"], reason="the reviewer role goes to a program")
    assert "No other machine has been seen filling reviewer" in out["applies"]


def test_set_work_mode_restarts_the_residents_and_says_the_gate_has_no_test(project, store):
    out = call(project, store, "set_work_mode", work="code", reason="a collector must land in the repository")
    assert toml(project)["project"]["work"] == "code"
    assert "supervisor" in out["applies"] and "coordinator" in out["applies"] and "no [[gate.unit]]" in out["applies"]
    states = store.runtime()["agent_state"]
    assert states["supervisor"]["restart_requested"] and states["coordinator"]["restart_requested"]
    assert "work is code" in last_commit(project)[0]


def test_gate_units_are_added_changed_and_removed(project, store):
    call(project, store, "set_gate_unit", command="python -m pytest -q", timeout="300s", reason="branches must pass their tests")
    assert toml(project)["gate"]["unit"] == [{"name": "unit", "command": "python -m pytest -q", "timeout": "300s"}]
    out = call(project, store, "set_gate_unit", name="unit", command="python -m compileall -q .", reason="no tests exist yet here")
    assert out["changed"].startswith("changed") and toml(project)["gate"]["unit"][0]["command"] == "python -m compileall -q ."
    call(project, store, "remove_entry", section="gate.unit", name="unit", reason="the test can never pass on this project")
    assert toml(project)["gate"]["unit"] == []


def test_set_capacity_limit(project, store):
    out = call(project, store, "set_capacity_limit", workers=3, reason="three tickets can run side by side")
    assert toml(project)["tasks"] == {"workers": 3, "reviewers": 1} and "operator" in out["applies"]
    with pytest.raises(StoreError, match="whole numbers"):
        call(project, store, "set_capacity_limit", workers=-1, reason="three tickets can run side by side")


def test_machines_are_added_changed_and_removed_with_what_the_launcher_does(project, store):
    out = call(project, store, "set_machine", name="feed", command="python machines/feed.py", reason="the feed must keep running")
    assert "starts it" in out["applies"] and "No `orchestra run`" in out["applies"]      # nothing launches in the test
    out = call(project, store, "set_machine", name="feed", command="python machines/feed.py", stop_grace=20,
               reason="the feed needs time to flush")
    assert "Nothing restarts" in out["applies"]
    out = call(project, store, "set_machine", name="feed", command="python machines/feed.py --fast", reason="the feed polls faster")
    assert "restarts it" in out["applies"]
    assert {"name": "feed", "command": "python machines/feed.py --fast", "stop_grace": 20} in toml(project)["machines"]
    out = call(project, store, "remove_entry", section="machines", name="feed", reason="the feed is not needed any more")
    assert "stops it" in out["applies"] and [m["name"] for m in toml(project)["machines"]] == ["shell_worker"]
    assert len(changed_events(store)) == 4


def test_collectors_and_scrapes(project, store):
    out = call(project, store, "set_collector", name="disk", command="machines/disk.sh", every="60s", reason="the owner wants disk usage")
    assert toml(project)["collect"] == [{"name": "disk", "command": "machines/disk.sh", "every": "60s"}]
    assert "logs/machines/disk.log" in out["applies"]
    call(project, store, "set_collector", name="app", url="http://127.0.0.1:9100/metrics", reason="the app serves its own metrics")
    assert toml(project)["scrape"][0]["url"] == "http://127.0.0.1:9100/metrics"
    with pytest.raises(StoreError, match="not both"):
        call(project, store, "set_collector", name="x", command="a.sh", url="http://x/metrics", reason="the owner wants disk usage")
    with pytest.raises(StoreError, match="duration"):
        call(project, store, "set_collector", name="y", command="a.sh", every="often", reason="the owner wants disk usage")


def test_what_orchestra_would_refuse_is_not_written(project, store):
    before = (project.repo_root / "orchestra.toml").read_text()
    head = git(project.repo_root, "rev-parse", "HEAD")
    with pytest.raises(StoreError, match="the change was not made"):        # a [[collect]] may not take a machine's name
        call(project, store, "set_collector", name="shell_worker", command="machines/x.sh", reason="the owner wants disk usage")
    with pytest.raises(StoreError, match="restart must be one of"):
        call(project, store, "set_machine", name="feed", command="python feed.py", restart="sometimes", reason="the feed must keep running")
    with pytest.raises(StoreError, match="roles the AI agents can fill"):
        call(project, store, "set_agent_roles", serves=["coordinator"], reason="the owner wants disk usage")
    assert (project.repo_root / "orchestra.toml").read_text() == before and git(project.repo_root, "rev-parse", "HEAD") == head
    assert changed_events(store) == []


@pytest.mark.parametrize("tool,args,what", [
    ("set_collector", {"name": "app", "url": "https://admin:hunter2@host/metrics"}, "a user and password in a URL"),
    ("set_collector", {"name": "app", "url": "https://host/metrics?access_token=abc123"}, "a secret in a URL's query"),
    ("set_collector", {"name": "app", "url": "https://host/metrics?page=2&api_key=zzz"}, "a secret in a URL's query"),
    ("set_machine", {"name": "feed", "command": "python feed.py --token ghp_abcdefghijklmnopqrstuvwxyz0123"}, "a value after a secret's option"),
    ("set_machine", {"name": "feed", "command": "python feed.py --password=s3cret"}, "a value after a secret's option"),
    ("set_machine", {"name": "feed", "command": "python feed.py --api-key s3cret"}, "a value after a secret's option"),
    ("set_machine", {"name": "feed", "command": "python feed.py github_pat_abcdefghijklmnopqrstuvwxyz_0123"}, "a GitHub token"),
    ("set_machine", {"name": "feed", "command": "python feed.py gho_abcdefghijklmnopqrstuvwxyz0123"}, "a GitHub token"),
    ("set_machine", {"name": "feed", "command": "python slack.py xoxb-1234567890-abcdefghij"}, "a Slack token"),
    ("set_machine", {"name": "feed", "command": "aws-feed AKIAABCDEFGHIJKLMNOP"}, "an AWS access key"),
    ("set_machine", {"name": "feed", "command": "python llm.py sk-ant-api03-abcdefghijklmnop"}, "an API key"),
    ("set_collector", {"name": "app", "command": "curl -sH 'Authorization: Bearer abcdefgh12345678' http://x/metrics"}, "a bearer token"),
])
def test_a_change_that_holds_a_secret_is_refused_before_anything_is_written(project, store, tool, args, what):
    before, head = (project.repo_root / "orchestra.toml").read_text(), git(project.repo_root, "rev-parse", "HEAD")
    with pytest.raises(StoreError, match="looks like a secret") as e:
        call(project, store, tool, reason="the owner wants this measured", **args)
    assert what in str(e.value) and "environment variable" in str(e.value)
    assert "hunter2" not in str(e.value) and "s3cret" not in str(e.value)
    assert (project.repo_root / "orchestra.toml").read_text() == before and git(project.repo_root, "rev-parse", "HEAD") == head
    assert changed_events(store) == []


def test_a_secret_passed_through_the_environment_or_a_file_is_accepted(project, store):
    call(project, store, "set_collector", name="app", url="https://host.example/metrics?page=2", reason="the app serves its own metrics")
    call(project, store, "set_machine", name="feed", command="python feed.py --token $FEED_TOKEN --token-file /run/feed.tok",
         reason="the feed must keep running")
    call(project, store, "set_machine", name="clone", command="git clone ssh://git@github.com/x/y.git", reason="the mirror must keep running")
    assert {m["name"] for m in toml(project)["machines"]} >= {"feed", "clone"}


def test_a_change_that_cannot_be_its_own_commit_on_the_base_branch_is_refused(project, store):
    with pytest.raises(StoreError, match="reason"):
        call(project, store, "set_work_mode", work="code", reason="why")
    toml_path = project.repo_root / "orchestra.toml"
    toml_path.write_text(toml_path.read_text() + "\n# someone typing\n")
    with pytest.raises(StoreError, match="uncommitted change"):
        call(project, store, "set_work_mode", work="code", reason="a collector must land in the repository")
    git(project.repo_root, "checkout", "-q", "orchestra.toml")
    git(project.repo_root, "checkout", "-q", "-b", "feature")
    with pytest.raises(StoreError, match="checked out"):
        call(project, store, "set_work_mode", work="code", reason="a collector must land in the repository")
    git(project.repo_root, "checkout", "-q", "main")
    with pytest.raises(StoreError, match="only the supervisor"):
        call(project, store, "set_work_mode", role="coordinator", work="code", reason="a collector must land in the repository")
    assert toml(project)["project"]["work"] == "tasks"


def test_a_failed_commit_puts_the_file_back(project, store):
    hook = project.repo_root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'refused by hook' >&2\nexit 1\n")
    hook.chmod(0o755)
    before = (project.repo_root / "orchestra.toml").read_text()
    with pytest.raises(StoreError, match="refused by hook"):
        call(project, store, "set_work_mode", work="code", reason="a collector must land in the repository")
    assert (project.repo_root / "orchestra.toml").read_text() == before and changed_events(store) == []


def test_no_change_is_no_commit(project, store):
    head = git(project.repo_root, "rev-parse", "HEAD")
    out = call(project, store, "set_work_mode", work="tasks", reason="the project stays on tasks for now")
    assert out["commit"] is None and out["changed"].startswith("nothing")
    assert git(project.repo_root, "rev-parse", "HEAD") == head and changed_events(store) == []


def test_only_the_supervisor_is_given_the_setup_tools():
    from orchestra_agents.builtins import ROLES
    from orchestra_agents.harness.tools import tools_for
    from orchestra_agents.roles import RoleDef, RoleError
    names = lambda role: {t.name for t in tools_for(ROLES[role].tools)}
    assert {"set_work_mode", "restart_machine", "check_setup", "get_model"} <= names("supervisor")
    assert not names("coordinator") & {"set_work_mode", "restart_machine", "check_setup"}
    with pytest.raises(RoleError):
        RoleDef(name="ops", tools=["setup"]).validate()
    RoleDef(name="ops", tools=["observe"]).validate()                               # reading may be granted


@pytest.mark.parametrize("work", ["tasks", "code"])
def test_the_supervisor_is_told_to_look_fix_the_setup_and_name_only_real_paths(project, work):
    from orchestra_agents.prompting import role_prompt
    project.config.work = work
    text = role_prompt(project, "supervisor")
    assert "{operator}" not in text and "## When the system cannot do what is asked" in text
    for tool in ("check_setup", "set_agent_roles", "set_work_mode", "set_gate_unit", "restart_machine", "list_metrics"):
        assert f"`{tool}`" in text
    assert "You never change code" in text and "Never suggest any other control on a page" in text
    assert "only the human can set" not in text and "Never restart or run the program yourself" not in text
