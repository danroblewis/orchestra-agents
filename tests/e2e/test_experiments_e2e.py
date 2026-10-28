"""Experiments for the agents, end to end on dummy agents (modeling.py): a scripted worker, given a
ticket that will double a queue's arrivals, reads the model Orchestra discovered, drafts an
experiment, replaces two flows' equations with other forms, replays it, runs it forward, asks an
inverse question, commits it as the prediction of its change, makes the change and announces it
with the prediction's change id; after the horizon Orchestra judges the prediction, and the
verdict wakes the worker, which reads it.

Everything runs as the dummy demo runs it (``demo/run.py --dummy``: a real ``orchestra run`` and
``orchestra web``, the task system, scripted agents, no model, no network), with a measured queue
beside it as in ``test_eras_e2e`` (jobs arrive at the rate in ``rate.txt``, a tenth of the queue
is done each second) and discovery every 20 s. The script lives outside the repository, so the
test writes the worker's rule once the model's numbers are known."""

import os
import stat
import subprocess

import pytest
from conftest import free_port
from test_demo_e2e import AGENTS, all_events, settled, states
from test_dummy_demo_e2e import GUARD, KEYS
from test_eras_e2e import QUEUE

from demo.run import Web, make_project, start, stop, wait_for, wait_until, web_token

TITLE = "Double the queue's arrivals"


@pytest.fixture
def queue_demo(tmp_path, monkeypatch):
    home, logs, bins, queue = tmp_path / "home", tmp_path / "logs", tmp_path / "bin", tmp_path / "queue"
    for d in (logs, bins, queue):
        d.mkdir()
    for product in ("claude", "pi", "codex"):
        b = bins / product
        b.write_text(f'#!/bin/sh\necho "{product} $*" >> {logs / "products.log"}\nexit 1\n')
        b.chmod(b.stat().st_mode | stat.S_IEXEC)
    (queue / "rate.txt").write_text("1.0")
    (queue / "queue.py").write_text(QUEUE)
    (queue / "queue.py").chmod(0o755)
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    env = {"ORCHESTRATOR_HOME": str(home), "NO_NETWORK_LOG": str(logs / "network.log"),
           "PYTHONPATH": os.pathsep.join(filter(None, [str(GUARD), os.environ.get("PYTHONPATH")])),
           "PATH": os.pathsep.join([str(bins), os.environ.get("PATH", "")]),
           "CLAUDE_BINARY": str(bins / "claude"), "PI_BINARY": str(bins / "pi"), "CODEX_BINARY": str(bins / "codex")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    target = make_project(tmp_path / "project", dummy=True)
    script = tmp_path / "script.toml"                    # outside the repository: the test writes it as it goes
    script.write_text((target / "dummy.toml").read_text(encoding="utf-8"), encoding="utf-8")
    toml = target / "orchestra.toml"
    text = toml.read_text(encoding="utf-8").replace('every = "5m"', 'every = "20s"', 1)
    assert "[discovery]\n" in text and "strategy" not in text
    text = text.replace("[discovery]\n", '[discovery]\nstrategy = "conservation"   # counters that balance a level\n', 1)
    text = text.replace('dummy_script = "dummy.toml"', f'dummy_script = "{script}"', 1)
    text += f'\n[[collect]]\nname = "queue"\ncommand = "{queue / "queue.py"}"\nevery = "2s"\n'
    toml.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-qam", "rediscover often; measure a queue"],
                   cwd=target, check=True)
    port = free_port()
    procs = start(target, port, env=env)
    try:
        web = Web(f"http://127.0.0.1:{port}", web_token(target, env))
        wait_for(web, procs)
        yield web, target, queue, script
    finally:
        stop(procs)


def worker_rules(queue, k: float, c: float) -> str:
    """The worker's turn on the ticket, then its turn on the verdict. The equations are other forms
    of the two flows: arrivals twice what they were, the drain capped (far above the queue)."""
    edits = (f'[{{ op = "replace_flow", id = "queue_jobs_arrived_total", equation = "2 * c", params = {{ c = {c} }} }}, '
             f'{{ op = "replace_flow", id = "queue_jobs_done_total", equation = "k * min(queue_jobs, cap)", '
             f'params = {{ k = {k}, cap = 1000.0 }} }}]')
    return f'''
[[rule]]
role = "worker"
prompt = "A verdict on your prediction"
steps = [{{ tool = "read_predictions", args = {{ mine = true }}, keep = "v" }}]
reply = "Read the verdict: {{kept.v.summary.1}}"

[[rule]]
role = "worker"
prompt = "{TITLE}"
steps = [
  {{ tool = "read_model", keep = "m" }},
  {{ tool = "create_experiment", args = {{ title = "double the arrivals", purpose = "what does doubling the arrivals do to the queue?" }}, keep = "e" }},
  {{ tool = "edit_experiment", args = {{ id = "{{kept.e.experiment}}", edits = {edits} }} }},
  {{ tool = "run_replay", args = {{ id = "{{kept.e.experiment}}", span = "2m" }} }},
  {{ tool = "run_forward", args = {{ id = "{{kept.e.experiment}}", horizon = "2m" }} }},
  {{ tool = "run_inverse", args = {{ id = "{{kept.e.experiment}}", span = "2m", targets = [{{ stock = "queue_jobs", scale = 1.5 }}], hold = [{{ flow = "queue_jobs_done_total" }}], deadline_s = 20, check = false }} }},
  {{ tool = "commit_prediction", args = {{ id = "{{kept.e.experiment}}", horizon = "60s" }}, keep = "p" }},
  {{ run = "echo 2.0 > {queue / 'rate.txt'}" }},
  {{ tool = "announce_change", args = {{ change = "{{kept.p.change}}", note = "the arrivals doubled" }} }},
  {{ write = "work/{{ticket}}.md", text = "Doubled the arrivals; prediction {{kept.p.prediction}}.\\n" }},
  {{ commit = "{{ticket}}: double the arrivals" }},
  {{ tool = "report_ticket", args = {{ state = "READY_FOR_REVIEW", summary = "Doubled the arrivals, as predicted in {{kept.e.experiment}}." }} }},
]
'''


def test_a_worker_predicts_its_change_on_the_model_and_wakes_on_the_verdict(queue_demo):
    web, target, queue, script = queue_demo
    from orchestra.config import load_project
    from orchestra.sd.experiments.api import Experiments

    from orchestra_agents.events import Events
    from orchestra_agents.protocol import TaskClient

    x = Experiments(load_project(target))
    model = lambda: web.get("/api/dynamics/model").get("model") or {}
    flows = lambda: {f["id"] for f in model().get("flows") or []}
    wait_until("a model with the queue", lambda: {"queue_jobs_arrived_total", "queue_jobs_done_total"} <= flows(), timeout_s=240)
    wait_until("two minutes of it", lambda: model()["discovery"]["live_minutes"] >= 2, timeout_s=300)
    m = {f["id"]: f for f in x.model()["model"]["flows"]}
    arrive, done = m["queue_jobs_arrived_total"], m["queue_jobs_done_total"]
    c = arrive["params"]["c"] if arrive["equation"] == "c" else 1.0 * x.model()["model"].get("unit_s", 60)
    k = done["params"]["k"] if done["equation"] == "k * queue_jobs" else 0.1 * 60
    script.write_text(worker_rules(queue, k, c) + script.read_text(encoding="utf-8"), encoding="utf-8")

    ask = TaskClient(Events.for_project(load_project_agents(target), source="planner"), "planner")
    milestone = ask("create_milestone", {"name": "Queue"})["milestone"]["id"]
    tid = ask("create_ticket", {"title": TITLE, "objective": "More throughput.", "description": "Double the queue's arrival rate.",
                                "acceptance": "- the arrivals double", "verification": "The model's verdict.", "milestone_id": milestone})["ticket"]["id"]

    judged = lambda: [e for e in all_events(web) if e["type"] == "orchestra.prediction.judged"]
    wait_until("the ticket done", lambda: settled(web), timeout_s=240)
    assert states(web)[tid] == "DONE"
    events = all_events(web)
    [made] = [e for e in events if e["type"] == "orchestra.prediction.made"]
    [changed] = [e for e in events if e["type"] == "system.changed"]
    worker = made["source"]
    assert worker.startswith("worker/") and made["subject"] == tid
    change = made["data"]["change"]
    assert changed["id"] == change and changed["source"] == worker and changed["subject"] == tid
    expect = {e["flow"]: e for e in made["data"]["expect"]}
    assert expect["queue_jobs_done_total"]["equation"] == "k * min(queue_jobs, cap)" and expect["queue_jobs_arrived_total"]["equation"] == "2 * c"
    assert set(made["data"]["trajectories"]["stocks"]) >= {"queue_jobs"}, "the prediction carries the worker's forward run"

    mine = [e for e in x.list()["experiments"] if e["owner"] == worker]
    [drafted] = [e for e in mine if e["origin"] is None]
    [solved] = [e for e in mine if (e["origin"] or {}).get("mode") == "inverse"]
    assert drafted["status"] == "committed" and solved["origin"]["experiment"] == drafted["id"]
    assert [r["mode"] for r in x.get(drafted["id"])["experiment"]["runs"]] == ["replay", "forward", "inverse"]

    wait_until("the verdict", lambda: [e for e in judged() if e.get("correlationid") == change], timeout_s=360)
    [verdict] = [e for e in judged() if e.get("correlationid") == change]
    assert verdict["data"]["verdict"] in ("passed", "no effect", "side effect", "model wrong elsewhere", "undecided"), verdict
    said = lambda: [e for e in all_events(web) if e["type"] == "agent.said" and "-verdict-" in (e["data"].get("session") or "")]
    wait_until("the worker woken by its verdict", said, timeout_s=60)
    [woken] = said()
    assert woken["source"].startswith("worker/") and woken["data"]["session"].startswith(f"worker-{tid}-verdict-")
    assert woken["data"]["summary"].split()[:5] == ["Read", "the", "verdict:", "VERDICT:", verdict["data"]["verdict"].split()[0]], woken
    turn = web.get(f"{AGENTS}/sessions/{woken['data']['session']}/transcript")
    calls = [c["name"] for msg in turn["messages"] for c in msg.get("tool_calls") or []]
    assert calls == ["read_predictions"], turn
    started = [e for e in events if e["type"] == "orchestra.machine.started" and e["source"] == worker]
    assert "orchestra.prediction.judged" in started[0]["data"]["listens_for"]


def load_project_agents(target):
    from orchestra_agents.config import load_project
    return load_project(target)
