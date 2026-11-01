"""The owner starts a piece of work from the Supervisor's chat, end to end on the Workflows plugin's
task loop example (its target, made by the example's own ``demo.make``) and the dummy harness: a
real ``orchestra run`` starts the floor, the program host, the scripted workers and reviewer, and the
agents' host; the owner's message goes to the Supervisor through the host's control endpoint; its
scripted turn calls ``request_work``. Seen: before ``orchestra run`` the tool refuses (no floor);
then the request goes out as ``owner`` (``via: supervisor``), the reply names the item, a scripted
worker claims its write offer, and ``explain_item`` shows it moving from write on to its end
(landed). Skipped when the plugin (with its examples) is not installed beside the agents."""

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from conftest import agents_project

from orchestra_agents import tooling
from orchestra_agents.control import bearer
from orchestra_agents.store import AgentStore, StoreError

workflows = pytest.importorskip("orchestra_workflows")
EXAMPLE = Path(workflows.__file__).resolve().parents[1] / "examples" / "task_loop"
MESSAGE = "Please start the task loop on adding a greeting file for the new team."
QUOTE = "start the task loop on adding a greeting file"
SCRIPT = f'''
[[rule]]
role = "supervisor"
prompt = "start the task loop"
steps = [
  {{ tool = "list_workflows", args = {{}} }},
  {{ tool = "request_work", args = {{ workflow = "task_loop", brief = "add work/greeting.txt for the new team", quote = "{QUOTE}", reason = "the owner asked for the greeting file", fields = {{ priority = 2 }} }}, keep = "r" }},
]
reply = "Started {{kept.r.item}} on task_loop; it shows on /items/{{kept.r.item}}, and I can tell you where it is."
'''


def demo():
    spec = importlib.util.spec_from_file_location("task_loop_demo", EXAMPLE / "demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wait(cond, what, timeout=120.0):
    end = time.time() + timeout
    while time.time() < end:
        if got := cond():
            return got
        time.sleep(0.5)
    raise AssertionError(f"{what}: not within {timeout} s")


@pytest.fixture
def target(tmp_path, monkeypatch):
    if not (EXAMPLE / "demo.py").is_file():
        pytest.skip("the Workflows plugin's examples are not beside its package (an editable install has them)")
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    for k in ("ORCHESTRA_EVENTS_URL", "ORCHESTRA_METRICS_URL", "ORCHESTRA_CONFIG_OVERLAY", "ORCHESTRA_EVENTS_ENDPOINT",
              "ORCHESTRA_EVENTS_TOKEN", "ORCHESTRA_SOURCE", "ORCHESTRA_ROLE"):
        monkeypatch.delenv(k, raising=False)
    d = demo()
    repo = d.make(tmp_path.resolve())
    project = agents_project(repo, harness="dummy")
    toml = repo / "orchestra.toml"
    toml.write_text(toml.read_text().replace('serves = ["worker", "reviewer"]', "serves = []"))   # the scripted machines work
    (repo / ".orchestra" / "dummy.toml").write_text(SCRIPT)
    for args in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-q", "-m", "agents on the dummy harness"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    project.reload_config()
    monkeypatch.setenv("ORCHESTRA_REPO", str(repo))
    procs: list[subprocess.Popen] = []

    def start() -> None:
        log = (tmp_path / "run.log").open("ab")
        procs.append(subprocess.Popen([sys.executable, "-m", "orchestra", "run"], cwd=repo, stdout=log,
                                      stderr=subprocess.STDOUT, start_new_session=True,
                                      env={**os.environ, "PYTHONUNBUFFERED": "1"}))
    try:
        yield d, project, repo, start
    finally:
        for p in procs:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(60)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()


def chat(project, store, message: str) -> dict:
    port = store.runtime()["control"]["port"]
    req = urllib.request.Request(f"http://127.0.0.1:{port}/supervisor", data=json.dumps({"message": message}).encode(),
                                 headers={**bearer(project), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        lines = [json.loads(x) for x in r.read().decode().splitlines() if x.strip()]
    (done,) = [x for x in lines if x.get("type") == "done"]
    assert not done.get("error"), done
    return done


def call(project, store, tool, **args):
    return tooling.handle(project, store, tool, args, role="supervisor")


def test_the_owner_starts_a_piece_of_work_from_the_chat(target):
    d, project, repo, start = target
    store = AgentStore(project)
    store.set_agent_state("supervisor", turn={"n": 0, "id": "sessions/supervisor/0", "message": MESSAGE, "at": 1.0})
    with pytest.raises(StoreError, match="no orchestra run launches this project now"):
        call(project, store, "request_work", workflow="task_loop", brief="add work/greeting.txt", quote=QUOTE,
             reason="before the floor runs")

    start()
    d.wait_for_machines(repo, 120)                                           # the task loop's machines announced
    wait(lambda: (store.runtime().get("control") or {}).get("port"), "the agents' host")
    wait(lambda: request_work_floor_up(project), "the floor")
    done = chat(project, store, MESSAGE)
    m = re.search(r"Started (task_loop-[0-9a-f]{6}) on task_loop; it shows on /items/\1", done.get("text") or "")
    assert m, done
    item = m.group(1)

    evs = store.events.read(0, 5000)[0]
    (req,) = [e for e in evs if e["type"] == "item.requested" and e.get("subject") == item]
    assert req["source"] == "owner" and req["via"] == "supervisor" and req["workflow"] == "task_loop"
    (fact,) = [e for e in evs if e["type"] == "item.fact" and e.get("subject") == item]
    assert fact["source"] == "owner" and fact["set"] == {"priority": 2}

    def claimed():
        return [e for e in store.events.read(0, 5000)[0] if e["type"] == "offer.claimed" and e.get("subject") == item]
    first = wait(claimed, "a worker's claim")[0]
    assert first["source"] in ("worker/1", "worker/2")

    steps: list[str] = []

    def ended():
        ex = call(project, store, "explain_item", item=item)
        step = (ex["positions"].get("task_loop") or {}).get("step")
        if step and (not steps or steps[-1] != step):
            steps.append(step)
        return ex if steps and not ex["summary"]["open"] else None
    ex = wait(ended, f"{item} to move on and end", timeout=180)
    assert steps[0] in ("write", "review", "gate") and set(steps) <= {"write", "review", "gate"}, steps
    assert ex["fields"]["priority"]["value"] == 2 and ex["fields"]["priority"]["set_by"].startswith("owner")
    assert ex["requested"]["workflow"] == "task_loop" and ex["requested"]["by"]["source"] == "owner"
    landed = [e for e in store.events.read(0, 5000)[0] if e["type"] == "offer.finished" and e.get("subject") == item]
    assert landed and landed[-1]["source"] == "merge_gate/1" and landed[-1]["outcome"] == "landed"


def request_work_floor_up(project) -> bool:
    from orchestra_agents.request_work import floor_down
    return floor_down(project) is None
