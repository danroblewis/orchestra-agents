"""A new user's project, through a real `orchestra run`, on the dummy harness (no `claude` on the
PATH, so that is the default), with no [agents] section at all:

- nothing configured (no [agents], no [project], no instrument; orchestra.toml as `orchestra init`
  wrote it, not committed): the host still starts, wakes the
  Supervisor once to set the project up, and the (scripted) Supervisor reads the guides, tries a
  one-liner collector, declares it (a commit of orchestra.toml alone, announced on the bus), and
  reads it back once Orchestra stored its first points; the setup is recorded and not offered again;
- a chat request: the Supervisor hands it to the Coordinator (work.requested), which plans it into
  a milestone and a ticket and reports back (work.planned); a worker does it, a reviewer approves
  it, and the Supervisor is woken to tell the human it is done.

The dummy agents play their built-in rules, plus the repository's own `.orchestra/dummy.toml` (the
default script when [agents] names none) for the setup turn."""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from orchestra.config import init_project as init_orchestra
from orchestra.config import load_project as orchestra_project
from orchestra.metrics import for_project as metrics_for

from demo.run import stop, wait_until
from orchestra_agents.config import load_project
from orchestra_agents.control import bearer
from orchestra_agents.events import Events
from orchestra_agents.harness import read_transcript
from orchestra_agents.protocol import TaskClient
from orchestra_agents.store import AgentStore

TODOS = 'echo "todo_comments $(git grep -I -c TODO -- app.py lib.py | awk -F: ' + "'{s+=$2} END {print s+0}'" + ')"'
SETUP_RULE = f"""
[[rule]]
role = "supervisor"
prompt = "\\\\[First run: set up this project\\\\]"
steps = [
  {{ tool = "list_guides" }},
  {{ tool = "read_guide", args = {{ name = "orchestra", section = "Naming series" }} }},
  {{ tool = "try_collector", args = {{ command = '''{TODOS}''', shell = true }} }},
  {{ tool = "set_collector", args = {{ name = "todos", command = '''{TODOS}''', shell = true, every = "2s", reason = "The code's TODO comments, counted: the first measurement of this project." }} }},
  {{ tool = "read_instrument", args = {{ name = "todos", wait_s = 60 }}, keep = "r" }},
]
reply = "I set the project up: the instrument todos stores {{kept.r.series.0.name}} = {{kept.r.series.0.latest.0}}."
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=owner", "-c", "user.email=owner@example.invalid", *args], cwd=repo,
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A repository Orchestra was just initialised in, with no `claude` to be found: the agents run
    on the dummy harness by default. Yields (repo, start), start running `orchestra run` on it."""
    env = {"ORCHESTRATOR_HOME": str(tmp_path / "home"), "CLAUDE_BINARY": str(tmp_path / "no-claude"),
           "PATH": os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "app.py").write_text("# TODO: one\nx = 1  # TODO two\n")
    (repo / "lib.py").write_text("# TODO three\n")
    git(repo, "init", "-q", "-b", "main")
    init_orchestra(repo)
    assert "[agents]" not in (repo / "orchestra.toml").read_text() and "[project]" not in (repo / "orchestra.toml").read_text()
    procs: dict[str, subprocess.Popen] = {}

    def start(extra_toml: str = "", script: str = "", track_config: bool = True) -> None:
        if extra_toml:
            (repo / "orchestra.toml").write_text((repo / "orchestra.toml").read_text() + extra_toml)
        if script:
            (repo / ".orchestra").mkdir(exist_ok=True)
            (repo / ".orchestra" / "dummy.toml").write_text(script)
        git(repo, "add", "-A")
        if not track_config:                       # as `orchestra init` leaves it: written, not committed
            git(repo, "reset", "-q", "--", "orchestra.toml")
        git(repo, "commit", "-qm", "the owner's project")
        (repo / "run").mkdir(exist_ok=True)
        with (repo / "run" / "orchestra.log").open("ab") as log:
            procs["run"] = subprocess.Popen([sys.executable, "-m", "orchestra", "run"], cwd=repo, stdout=log, stderr=subprocess.STDOUT,
                                            env={**os.environ, "ORCHESTRA_REPO": str(repo), "PYTHONUNBUFFERED": "1"})

    try:
        yield repo, start
    finally:
        stop(procs)


def events_of(repo: Path, kind: str) -> list[dict]:
    return [e for e in Events.for_project(load_project(repo), source="test").read(0, 100_000)[0] if e["type"] == kind]


def test_a_new_project_is_set_up_by_the_supervisor_with_no_configuration(fresh):
    repo, start = fresh
    start(script=SETUP_RULE, track_config=False)
    project = load_project(repo)
    assert project.config.harness == "dummy" and "no claude command" in project.config.harness_note
    store = AgentStore(project)
    wait_until("the setup recorded", lambda: bool(store.agent_state("supervisor").get("setup")), timeout_s=120)

    [changed] = events_of(repo, "config.changed")
    assert changed["source"] == "supervisor" and changed["section"] == "collect"
    assert git(repo, "log", "-1", "--format=%s", "--", "orchestra.toml").startswith("Supervisor: added the collector todos")
    assert not git(repo, "status", "--porcelain", "--", "orchestra.toml")    # the untracked file `orchestra init` wrote, committed with it
    assert '"sh"' in git(repo, "show", "HEAD:orchestra.toml")
    dump = metrics_for(orchestra_project(repo)).dump("todo_comments", {})
    assert dump and dump[0][1]["instrument"] == "todos" and dump[0][2][-1][1] == 3.0
    sup = store.find_session("supervisor")
    assert sup["last_text"] == "I set the project up: the instrument todos stores todo_comments = 3.0.", sup["last_text"]
    assert store.agent_state("supervisor")["setup"]["harness"] == "dummy"
    tools = [a["tool"] for a in store.agent_state("supervisor").get("actions") or []]
    assert tools == ["set_collector"]                                       # the one change, on the record


def test_a_chat_request_is_handed_to_the_coordinator_planned_and_done(fresh):
    repo, start = fresh
    start(extra_toml='\n[project]\nbase_branch = "main"\nticket_prefix = "APP"\nwork = "tasks"\n')
    project = load_project(repo)
    store = AgentStore(project)
    ask = TaskClient(Events.for_project(project, source="planner"), "planner")
    wait_until("the agents' host", lambda: bool((store.runtime().get("control") or {}).get("port")), timeout_s=60)
    wait_until("the task system", lambda: ask("list_tickets", {}).get("ok"), timeout_s=60)
    assert "setup" not in store.agent_state("supervisor")                  # [project] is there: it was set up

    port = store.runtime()["control"]["port"]
    req = urllib.request.Request(f"http://127.0.0.1:{port}/supervisor", method="POST",
                                 data=json.dumps({"message": "Write a short note on what this project does."}).encode(),
                                 headers={"Content-Type": "application/json", **bearer(project)})
    with urllib.request.urlopen(req, timeout=120) as r:
        lines = [json.loads(x) for x in r.read().decode().splitlines() if x.strip()]
    assert any(ln.get("type") == "tool_start" and ln.get("name") == "hand_to_coordinator" for ln in lines), lines
    assert "I handed your request to the Coordinator as r-" in lines[-1].get("text", "") + "".join(
        ln.get("text") or "" for ln in lines if ln.get("type") == "assistant_end")

    def done() -> bool:
        got = ask("list_tickets", {})
        return bool(got.get("tickets")) and all(t["state"] == "DONE" for t in got["tickets"])
    wait_until("the planned ticket DONE", done, timeout_s=180)

    [asked] = events_of(repo, "work.requested")
    [planned] = events_of(repo, "work.planned")
    [made] = events_of(repo, "ticket.created")
    assert (asked["source"], asked["to"]) == ("supervisor", "coordinator")
    assert (planned["source"], planned["to"], planned["request"]) == ("coordinator", "supervisor", asked["request"])
    assert made["source"] == "coordinator" and planned["tickets"] == [{"id": made["ticket"], "title": made["title"]}]
    assert "Write a short note" in ask("get_ticket", {"id": made["ticket"]})["ticket"]["description"]
    [approved] = events_of(repo, "review.approved")
    assert approved["source"].startswith("reviewer/")
    # the Supervisor was told the plan, then woken when the ticket was done, to tell the human
    def told() -> list[str]:
        return [m["text"] for m in read_transcript(store.find_session("supervisor")) if m["role"] == "user"]
    wait_until("the Supervisor told it is done", lambda: any("made for the human's request, is DONE" in t for t in told()), timeout_s=60)
    assert any("event work.planned" in t for t in told())
