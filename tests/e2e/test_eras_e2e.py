"""Goal G11's end-to-end acceptance on dummy agents (Orchestra's eras, sd/eras.py): a machine
announces mid-run that it changed the configuration (``config.changed``, which Orchestra's
``[discovery] eras_from`` names by default), and a new era begins in the model Orchestra's host
discovers. Everything runs as the dummy demo runs it (``demo/run.py --dummy``: a real
``orchestra run`` and ``orchestra web``, the task system, scripted agents, no model, no network),
with two additions so the test need not wait for hours: the host rediscovers every 20 s, and a
small queue is measured beside the tickets (a ``[[collect]]`` script outside the repository: jobs
arrive at the rate in ``rate.txt`` and a tenth of the queue is done each second), so the model has
a stock from the first minute. The scripted change doubles the queue's arrival rate.

It checks that:
- the host records the era and announces it (``orchestra.era.started``, caused by the change);
- the discoveries after it are of that era: the model says so, reads nothing from before it, has
  the previous era's model as its prior (saved as a version of its own, readable), and reports
  the arrival rate changed at the boundary, about doubled;
- the Dynamics page's API has the era on its timeline, and the Machines page's map is drawn from
  the era's events by default;
- the web UI's eras read (``/api/dynamics/eras``) lists it."""

import os
import stat
import subprocess

import pytest
from conftest import free_port
from test_demo_e2e import all_events
from test_dummy_demo_e2e import GUARD, KEYS

from demo.run import Web, make_project, plan, start, stop, wait_for, wait_until, web_token

QUEUE = '''#!/usr/bin/env python3
"""A queue: jobs arrive at the rate in rate.txt (per second); a tenth of the queue is done each second."""
import json, math, pathlib, time
here = pathlib.Path(__file__).parent
state = here / "queue.json"
now = time.time()
s = json.loads(state.read_text()) if state.exists() else {"t": now, "arrived": 0.0, "done": 0, "carry": 0.0}
dt = max(0.0, now - s["t"])
rate = float((here / "rate.txt").read_text())
s["arrived"] += rate * dt
q = math.floor(s["arrived"]) - s["done"]
s["carry"] += 0.1 * q * dt
d = min(q, math.floor(s["carry"]))
s["carry"] -= d
s["done"] += d
s["t"] = now
state.write_text(json.dumps(s))
print("# TYPE queue_jobs gauge")
print(f"queue_jobs {math.floor(s['arrived']) - s['done']}")
print(f"queue_jobs_arrived_total {math.floor(s['arrived'])}")
print(f"queue_jobs_done_total {s['done']}")
'''


@pytest.fixture
def quick_demo(tmp_path, monkeypatch):
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
    toml = target / "orchestra.toml"
    text = toml.read_text(encoding="utf-8")
    assert 'every = "5m"' in text
    text = text.replace('every = "5m"', 'every = "20s"', 1)
    text += f'\n[[collect]]\nname = "queue"\ncommand = "{queue / "queue.py"}"\nevery = "2s"\n'
    toml.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-qam", "rediscover often; measure a queue"],
                   cwd=target, check=True)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    procs = start(target, port, env=env)
    try:
        web = Web(url, web_token(target, env))
        wait_for(web, procs)
        yield web, target, queue
    finally:
        stop(procs)


def test_a_change_announced_during_a_run_begins_an_era_in_the_model(quick_demo):
    web, target, queue = quick_demo
    from orchestra.config import load_project
    from orchestra.events import Events

    plan(target)
    model = lambda: web.get("/api/dynamics/model").get("model") or {}
    flows = lambda: {f["id"] for f in model().get("flows") or []}
    wait_until("a first model with the queue", lambda: "queue_jobs_arrived_total" in flows(), timeout_s=240)
    wait_until("a few minutes of it", lambda: model()["discovery"]["live_minutes"] >= 3, timeout_s=300)
    assert model()["era"] is None and model()["eras_recorded"] == []

    # the Supervisor's setup tools publish this when they change orchestra.toml; scripted here, with the change it announces
    (queue / "rate.txt").write_text("2.0")
    project = load_project(target)
    change = Events.for_project(project, source="supervisor").publish(
        "config.changed", {"section": "queue", "change": "arrivals 1 → 2 a second", "reason": "a test"}, subject="orchestra.toml")

    wait_until("the era in the model", lambda: model().get("era") == "e1", timeout_s=120)

    def compared() -> dict:
        cmp = (model().get("discovery") or {}).get("era_comparison") or {}
        return {f["id"]: f for f in cmp.get("flows") or []}
    wait_until("the arrivals measured in the era", lambda: compared().get("queue_jobs_arrived_total", {}).get("status") == "changed",
               timeout_s=240)
    m = model()
    [era] = m["eras_recorded"]
    assert era["id"] == "e1" and era["cause"] == change["id"] and era["label"] == "config.changed from supervisor"
    assert era["subject"] == "orchestra.toml"
    assert m["current_era"] == "e1" and m["discovery"]["start"] >= era["start"] - 5, "nothing from before the era is read"
    assert m["discovery"]["era_start"] == era["start"]
    assert m["discovery"]["prior"]["era"] is None, "the era before is its prior"
    [c] = compared()["queue_jobs_arrived_total"]["params"]
    assert 1.6 < c["ratio"] < 2.4 and c["z"] > 2, c
    assert {None, "e1"} <= {v["era"] for v in m["versions"]}, "both eras have saved versions"

    started = [e for e in all_events(web) if e["type"] == "orchestra.era.started"]
    assert [(e["source"], e["subject"], e["causationid"]) for e in started] == [("orchestra-host", "e1", change["id"])]

    before = web.get("/api/dynamics/model?era=none")
    assert before["ok"] and before["model"]["era"] is None, "the past era stays readable"
    d = web.get("/api/machines")
    assert d["ok"] and d["era"] == "e1" and [e["id"] for e in d["eras"]] == ["e1"]
    assert "supervisor" in {x["id"] for x in d["machines"]}, "its change is the era's first event"
    every = web.get("/api/machines?era=all")
    assert {x["id"] for x in d["machines"]} <= {x["id"] for x in every["machines"]}
    assert "planner" in {x["id"] for x in every["machines"]} - {x["id"] for x in d["machines"]}, "the planner acted before the era only"

    eras = web.get("/api/dynamics/eras")         # the web UI's read of the eras (the CLI is gone)
    assert eras["ok"] and eras["current"] == "e1" and [e["id"] for e in eras["eras"]] == ["e1"], eras
    assert eras["eras"][0]["label"] == "config.changed from supervisor", eras["eras"][0]
    changed = {f["id"]: f for f in (eras["comparison"] or {}).get("flows", [])}
    assert changed.get("queue_jobs_arrived_total", {}).get("status") == "changed", eras["comparison"]
