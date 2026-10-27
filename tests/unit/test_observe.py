"""The Supervisor sees the whole system: orchestra.toml, the launcher's processes and the
collectors, any machine's log (and nothing outside the project), the metrics catalogue, the
model, who fills the roles, and the task system's snapshot."""

import subprocess
import time

import pytest
from conftest import agents_project
from fake_tasks import FakeTasks
from orchestra.metrics import Point, for_project
from orchestra.sd.model import Flow, Model, SeriesRef, Stock
from orchestra.sd.store import ModelStore

from orchestra_agents import tooling
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore, StoreError

EXTRA = """
[project]
work = "tasks"

[[collect]]
name = "disk"
command = "machines/disk.sh"
every = "30s"

[[collect]]
name = "bad name"
command = "x.sh"
"""


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    (repo / "orchestra.toml").write_text((repo / "orchestra.toml").read_text() + EXTRA)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "the project")
    p.reload_config()
    return p


@pytest.fixture
def store(project):
    return AgentStore(project, Events.for_project(project, source="supervisor"))


def see(project, store, tool, **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role="supervisor")


def test_read_config(project, store):
    out = see(project, store, "read_config")
    assert '[[collect]]\nname = "disk"' in out["text"] and out["can_change"].startswith("yes")
    assert any("the project" in c for c in out["recent_commits"])


def test_read_config_hides_what_looks_like_a_secret(project, store):
    toml_path = project.repo_root / "orchestra.toml"
    toml_path.write_text(toml_path.read_text() + """
[[scrape]]
name = "app"
url = "https://admin:hunter2@host/metrics?token=abc123&page=2"
auth = { bearer = "tok-1", bearer_file = "/run/app.tok" }

[[machines]]
name = "feed"
command = "python feed.py --api-key s3cret ghp_abcdefghijklmnopqrstuvwxyz0123"
""")
    git(project.repo_root, "commit", "-qam", "secrets someone wrote by hand")
    text = see(project, store, "read_config")["text"]
    for secret in ("admin", "hunter2", "abc123", "tok-1", "s3cret", "ghp_"):
        assert secret not in text, secret
    assert "https://<hidden>@host/metrics?token=<hidden>&page=2" in text and 'bearer_file = "/run/app.tok"' in text
    assert '[[collect]]\nname = "disk"' in text


def test_processes_and_collectors_with_their_last_round(project, store):
    out = see(project, store, "list_processes")
    assert out["launcher"].startswith("no orchestra run") and out["processes"] == []
    assert out["collectors"][0]["name"] == "disk" and "has not run" in out["collectors"][0]["last_log_line"]
    assert any("bad name" in e for e in out["entries_left_out"])
    log = project.orchestra.logs_dir / "machines" / "disk.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("round ok\nround failed: exit 127: machines/disk.sh: not found\n")
    assert see(project, store, "list_processes")["collectors"][0]["last_log_line"].endswith("not found")
    assert see(project, store, "read_machine_log", name="disk", lines=1)["lines"] == ["round failed: exit 127: machines/disk.sh: not found"]


def test_a_log_is_read_only_inside_the_project(project, store):
    store.events.publish("orchestra.machine.started", {"mode": "continuous", "log": str(project.repo_root / "feed.log")},
                         source="feed/1")
    (project.repo_root / "feed.log").write_text("feed up\n")
    assert see(project, store, "read_machine_log", name="feed")["lines"] == ["feed up"]
    store.events.publish("orchestra.machine.started", {"mode": "continuous", "log": "/etc/hosts"}, source="spy")
    with pytest.raises(StoreError, match="outside the project"):
        see(project, store, "read_machine_log", name="spy")
    (project.orchestra.logs_dir / "orchestra-host.log").write_text("host up\n")
    assert see(project, store, "read_machine_log", name="orchestra-host")["lines"] == ["host up"]


def test_the_metrics_catalogue(project, store):
    now = time.time()
    lab = {"machine": "disk", "project": project.key}
    for_project(project.orchestra).write([Point("disk_free_bytes", 1e11, now - 30, {**lab, "mount": "/"}),
                                          Point("disk_free_bytes", 2e11, now - 7200, {**lab, "mount": "/data"}),
                                          Point("orchestra_host_up", 1, now - 5, {"machine": "orchestra-host", "project": project.key}),
                                          Point("disk_free_bytes", 5, now, {"machine": "disk", "project": "another"})])
    out = see(project, store, "list_metrics")
    rows = {r["name"]: r for r in out["metrics"]}
    disk = rows["disk_free_bytes"]
    assert disk["series"] == 2 and disk["labels"] == {"machine": ["disk"], "mount": ["/", "/data"]}
    assert disk["points_last_hour"] == 1 and 25 <= disk["last_written_s_ago"] <= 60
    assert "Orchestra's own" in rows["orchestra_host_up"]["note"]
    assert [r["name"] for r in see(project, store, "list_metrics", prefix="disk")["metrics"]] == ["disk_free_bytes"]


def test_the_model(project, store):
    out = see(project, store, "get_model")
    assert out["model"] is None and "no model yet" in out["why"]
    model = Model("m", "m", stocks=[Stock("queue", "queue", series=SeriesRef("queue_depth", {"machine": "board"}))],
                  flows=[Flow("in", "in", None, "queue")], discovery={"left_out": {"disk_free_bytes": "no counters balance it"}})
    ModelStore.for_state(project.orchestra.state_dir).save(model, note="test")
    out = see(project, store, "get_model")
    assert out["stocks"] == [{"id": "queue", "name": "queue", "series": 'queue_depth{machine="board"}'}]
    assert out["flows"][0]["to"] == "queue" and out["left_out"] == {"disk_free_bytes": "no counters balance it"}


def test_check_setup_says_who_fills_the_roles_and_what_runs(project, store):
    store.events.publish("orchestra.machine.started", {"kind": "program", "listens_for": ["task.assigned"]}, source="worker/shell")
    out = see(project, store, "check_setup")
    assert out["work"].startswith("tasks (a scratch folder")
    assert out["roles"][0].startswith("The AI agents here fill: worker, reviewer")
    assert any("worker/shell (a program)" in line for line in out["roles"])
    assert out["gate_tests"] == [] and out["launcher"].startswith("no orchestra run") and out["config_can_change"] == "yes"
    assert out["collectors"] == ["disk: (no log yet: it has not run)"]


def test_check_setup_names_a_program_filling_a_role_that_orchestra_does_not_run(project, store):
    from orchestra import runtime

    launched = project.orchestra.logs_dir / "machines" / "shell_worker.log"
    runtime.update(project.orchestra, lambda d: d.setdefault("processes", {}).__setitem__(
        "shell_worker", {"owner": "project", "state": "running", "pid": 1, "log": str(launched)}))
    store.events.publish("orchestra.machine.started", {"kind": "program", "listens_for": ["task.assigned"], "log": str(launched)},
                         source="worker/shell")
    store.events.publish("orchestra.machine.started", {"kind": "program", "listens_for": ["task.assigned"],
                                                       "log": "run/worker.log"}, source="worker/1")
    store.events.publish("orchestra.machine.started", {"kind": "agent", "listens_for": ["task.assigned"]}, source="worker/2")
    [line] = see(project, store, "check_setup")["not_launched"]
    assert line.startswith("worker/1 fills the worker role but is not a [[machines]] entry") and "run/worker.log" in line
    assert "cannot restart or change it" in line


def test_the_task_systems_snapshot_is_a_query_on_the_bus(project, store):
    with FakeTasks(project) as tasks:
        see(project, store, "create_milestone", name="M")
        see(project, store, "create_ticket", title="t", objective="o", description="d", acceptance="a", verification="v",
            milestone_id="M1")
        out = see(project, store, "snapshot")
    assert out["snapshot"]["counts"] == {"OPEN": 1} and tasks.seen[-1] == {"op": "snapshot", "by": "supervisor", "args": {}}
