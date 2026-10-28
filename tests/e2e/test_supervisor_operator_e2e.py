"""The Supervisor fixes a setup that cannot do what the human asks, through a real Orchestra.

The demo starts in the setup the owner's metrics request failed in (2026-10-26, a0953ac's
demo/run.py): `work = "tasks"` (nothing a worker makes lands), `[agents] serves = ["reviewer"]`,
and the shell worker started by hand, outside `[[machines]]` and without `--only-commands`, so it
takes any ticket and blocks the ones it cannot do. The human asks for a measurement; the
Supervisor, played by the fake Claude Code from a script, looks (`check_setup`, which names the
shell worker as a program Orchestra does not run), makes the AI agents fill the worker role and
switches to code work (each a commit of orchestra.toml with its reason, announced on the bus), and
tells the human which process to stop, as its prompt says for a program it cannot change. The
human stops it and says so; the Supervisor files the collector ticket. An AI worker writes the
collector, the reviewer approves, the gate lands it, and `orchestra run` starts it: its series
reach the project's store."""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import free_port
from orchestra import runtime
from orchestra.config import load_project

from demo.run import PLAN, Web, make_project, start, stop, wait_for, wait_until, web_token

AGENTS, TASKS = "/api/plugins/agents", "/api/plugins/tasks"
FAKE = Path(__file__).parent / "fake_claude.py"
COLLECTOR = PLAN["tickets"][3]                    # "Measure the repository": the fake worker writes a real collector
LOOK = [  # the first chat turn: see, fix what the tools can, and name what the human must stop
    {"tool": "check_setup", "args": {}},
    {"tool": "set_agent_roles", "args": {"serves": ["worker", "reviewer"],
                                         "reason": "a collector is code: only an AI worker can write it, and none filled the role"}},
    {"tool": "set_work_mode", "args": {"work": "code", "reason": "a collector must land in the repository for Orchestra to run it"}},
]
FILE = [  # the second, once the human stopped the shell worker
    {"tool": "create_ticket", "args": {**{k: COLLECTOR[k] for k in ("title", "objective", "description", "acceptance")},
                                       "verification": "The reviewer reads the diff and runs the script once.", "milestone_id": "M1"}},
]


def git(target: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=target, capture_output=True, text=True, check=True).stdout


def all_events(web: Web) -> list[dict]:
    out, since = [], 0
    while True:
        page = web.get(f"/api/events?since={since}")
        if not page["events"]:
            return out
        out += page["events"]
        since = page["next"]


def chat(web: Web, message: str) -> list[dict]:
    with web.open(f"{AGENTS}/supervisor", {"message": message}, timeout=180) as r:
        return [json.loads(line) for line in r.read().decode().splitlines() if line.strip()]


@pytest.fixture
def broken(tmp_path, monkeypatch):
    """The demo in last night's setup, with the scripted Supervisor; every process stopped after."""
    fake = tmp_path / "claude"
    shutil.copy(FAKE, fake)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "supervisor.json"
    script.write_text(json.dumps(LOOK))
    env = {"ORCHESTRATOR_HOME": str(tmp_path / "home"), "CLAUDE_BINARY": str(fake),
           "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-home"), "FAKE_CLAUDE_SUPERVISOR": str(script)}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    target = make_project(tmp_path / "project")
    toml = target / "orchestra.toml"
    text = toml.read_text()
    assert 'work = "code"' in text and 'serves = ["worker", "reviewer"]' in text
    entry = text[text.index('[[machines]]\nname = "shell_worker"'):text.index("# ---- the task system")]
    toml.write_text(text.replace('work = "code"', 'work = "tasks"').replace('serves = ["worker", "reviewer"]', 'serves = ["reviewer"]')
                    .replace(entry, ""))
    git(target, "-c", "user.name=owner", "-c", "user.email=owner@example.invalid", "commit", "-qam", "last night's setup")
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    procs = start(target, port, env=env)
    # the shell worker started by hand beside Orchestra, as a0953ac's run.py did: no [[machines]]
    # entry and no --only-commands (instance "shell": the AI workers are worker/1, worker/2)
    with (target / "run" / "shell_worker.log").open("ab") as log:
        procs["shell_worker"] = subprocess.Popen([sys.executable, str(target / "machines" / "shell_worker.py"), "shell"], cwd=target,
                                                 env={**os.environ, "ORCHESTRA_LOG": str(target / "run" / "shell_worker.log")},
                                                 stdout=log, stderr=subprocess.STDOUT)
    try:
        web = Web(url, web_token(target, env))
        wait_for(web, procs)
        yield web, target, script, procs["shell_worker"]
    finally:
        stop(procs)
        left = runtime.read(load_project(target)).get("processes") or {}
        assert not [n for n, r in left.items() if r.get("pid") and runtime.alive(r["pid"])], left


def test_the_supervisor_reconfigures_and_the_collector_ends_up_running(broken):
    web, target, script, shell = broken
    from orchestra_agents.config import load_project as agents_project
    from orchestra_agents.events import Events
    from orchestra_agents.protocol import TaskClient
    ask = TaskClient(Events.for_project(agents_project(target), source="planner"), "planner")
    assert ask("create_milestone", {"name": "Measurements"})["milestone"]["id"] == "M1"

    def announced() -> bool:
        return any(e["type"] == "orchestra.machine.started" and e["source"] == "worker/shell" for e in all_events(web))
    wait_until("the shell worker", announced)

    lines = chat(web, "Add a measurement of the repository: how many files git tracks, logged to Orchestra's metrics.")
    assert lines[-1]["type"] == "done" and not lines[-1].get("error"), lines[-1]
    reply = "\n".join(ln.get("text") or "" for ln in lines if ln["type"] == "assistant_end")
    assert reply, lines
    assert "The AI agents here fill: reviewer" in reply               # check_setup saw what was wrong
    assert "worker/shell fills the worker role but is not a [[machines]] entry" in reply     # ... and the program it cannot change
    assert '"commit"' in reply

    # each change is a commit of orchestra.toml alone on main, with its reason, and on the bus
    log = git(target, "log", "--format=%s|%b", "main")
    assert "Supervisor: the AI agents fill worker, reviewer ([agents] serves)|a collector is code" in log
    assert "Supervisor: work is code ([project] work)|a collector must land in the repository" in log
    assert 'serves = ["worker", "reviewer"]' in git(target, "show", "main:orchestra.toml")
    changed = [e for e in all_events(web) if e["type"] == "config.changed"]
    assert [e["source"] for e in changed] == ["supervisor", "supervisor"]
    assert [e["data"]["section"] for e in changed] == ["agents", "project"]

    # the human stops the program, as the Supervisor asked, and says so; the Supervisor files the ticket
    shell.terminate()
    shell.wait(timeout=10)
    script.write_text(json.dumps(FILE))
    lines = chat(web, "I stopped the shell worker. Go ahead with the measurement.")
    assert lines[-1]["type"] == "done" and not lines[-1].get("error"), lines[-1]
    assert "DEMO-1" in "\n".join(ln.get("text") or "" for ln in lines if ln["type"] == "assistant_end")

    # an AI worker takes the collector ticket, the reviewer approves, the gate lands it on main
    def done() -> bool:
        return {t["id"]: t["state"] for t in web.get(f"{TASKS}/state")["tickets"]}.get("DEMO-1") == "DONE"
    wait_until("the collector ticket DONE", done, timeout_s=300)
    workers = [e["source"] for e in all_events(web) if e["type"] == "review.requested" and e.get("subject") == "DEMO-1"]
    assert workers[0] == "worker/1", workers
    assert "machines/repo_stats.sh" in git(target, "ls-tree", "-r", "--name-only", "main")

    # orchestra run starts the landed collector, and its series reach the project's store
    def written() -> bool:
        rows = {m["name"]: m for m in web.get("/api/core/metrics/catalog")["metrics"]}
        return "repo_stats" in ((rows.get("repo_files") or {}).get("labels") or {}).get("instrument", {}).get("values", [])
    wait_until("the collector's series in the store", written, timeout_s=90)
