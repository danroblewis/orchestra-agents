"""``read_model``'s levels over a real web server and a fake store, as the Supervisor calls the tool.

A queue lives in another store (``[[sources]]`` rig, a fake Prometheus); Orchestra's discovery makes
the stock of it; Orchestra's web server (on a port the system picks, recorded as ``orchestra web``
records it) reads its level through the source, as the Dynamics page shows it. The Supervisor's
``read_model``, through the MCP server its dummy harness starts, gives that level "measured by
source rig", never a same-named series of the own store; with the store gone it says why, never
"no data"; with no web server it says that."""

import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest
import uvicorn
from conftest import agents_project
from fake_store import FakeStore
from orchestra import runtime
from orchestra.metrics import Point, for_project
from orchestra.sd.rediscover import rediscover
from orchestra.web.app import create_app

from orchestra_agents import access, tooling
from orchestra_agents.builtins import ROLES
from orchestra_agents.design import begin_turn
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore

STEP = 30.0


def _queue(end: float) -> list:
    """A queue a consumer fills and drains, written every 30 s: its level and its two counters."""
    level, ins, outs, n_in, n_out = [], [], [], 0, 0
    for k in range(241):
        t = end - 7200 + STEP * k
        n_in += (k * 7) % 3
        n_out += (k * 5) % 2 if n_in > n_out else 0
        level.append((t, float(n_in - n_out)))
        ins.append((t, float(n_in)))
        outs.append((t, float(n_out)))
    lab = {"machine": "consumer"}
    return [("queue_depth", lab, level), ("jobs_in_total", lab, ins), ("jobs_out_total", lab, outs)]


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The project with its source declared, the store, and the model discovered from it."""
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    end = (time.time() // STEP) * STEP
    dump = _queue(end)
    store = FakeStore(dump, meta={})
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    p = agents_project(repo)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    p.reload_config()
    sup = AgentStore(p, Events.for_project(p, source="supervisor"))
    set_ = lambda key, value: tooling.handle(p, sup, "set_setting", {"key": key, "value": json.dumps(value), "target": "repo",
                                                                    "reason": "The rig's queue."}, role="supervisor")
    begin_turn(sup, "Read only the rig's queue and jobs series, at most 100 series.")   # the owner's limits (T36)
    set_("sources", [{"name": "rig", "url": store.url, "infer": False, "match": ['{__name__=~"queue_depth|jobs_.*"}']}])   # a source needs a match (C14)
    set_("series", [{"source": "rig", "name": n, "query": n, "kind": k}
                    for n, k in (("queue_depth", "level"), ("jobs_in_total", "counter"), ("jobs_out_total", "counter"))])
    p.orchestra.reload_config()
    own = for_project(p.orchestra)
    own.write([Point("queue_depth", 999.0, time.time(), {"machine": "consumer"})])    # a same-named own series: never the level
    model = rediscover(p.orchestra, own, window_s=7200, step_s=STEP).model
    own.close()
    stock = next(s for s in model.stocks if s.series and s.series.source == "rig")
    yield p, store, stock, dump[0][2][-1][1]
    store.close()


def _serve(project):
    """Orchestra's web server on a free loopback port, recorded where ``orchestra web`` records it."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(project.orchestra), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    runtime.update(project.orchestra, lambda d: d.__setitem__("web", {"port": port, "pid": os.getpid(), "since": time.time()}))

    def stop():
        runtime.update(project.orchestra, lambda d: d.pop("web", None))
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
    return stop


def _mcp(project, home, role: str = "supervisor"):
    token = access.issue(AgentStore(project), role, role)
    env = {**os.environ, "ORCHESTRATOR_HOME": str(home), "ORCHESTRA_REPO": str(project.repo_root),
           "ORCHESTRA_ROLE": role, "ORCHESTRA_TOOLS": ",".join(ROLES[role].tools), access.TOKEN_ENV: token}
    proc = subprocess.Popen([sys.executable, "-m", "orchestra_agents.harness.mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, env=env, bufsize=1)
    n = 0

    def rpc(method, params=None):
        nonlocal n
        n += 1
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": params or {}}) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())
    rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})

    def read_model() -> list[str]:
        r = rpc("tools/call", {"name": "read_model", "arguments": {}})["result"]
        assert not r.get("isError"), r
        return json.loads(r["content"][0]["text"])["summary"]
    return proc, read_model


def _until(read, stock: str, want, seconds: float = 45.0) -> str:
    deadline, got = time.time() + seconds, None
    while time.time() < deadline:
        got = next(x for x in read() if x.startswith(f"  {stock}: "))
        if want(got):
            return got
        time.sleep(0.5)
    raise AssertionError(f"not within the time limit; last: {got}")


def test_a_source_fed_stocks_level_is_read_through_the_web_server_with_its_source(world, tmp_path):
    project, _, stock, last = world
    stop = _serve(project)
    proc, read_model = _mcp(project, tmp_path / "home")
    try:
        got = _until(read_model, stock.id, lambda x: "level not read" not in x)
        assert got.startswith(f"  {stock.id}: {last:g}") and got.endswith("(measured by source rig)"), got
        assert "999" not in got
        stop()
        stop = lambda: None
        gone = next(x for x in read_model() if x.startswith(f"  {stock.id}: "))
        assert gone.startswith(f"  {stock.id}: level not read (measured by source rig): Orchestra's web server is not running"), gone
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
        stop()


def test_a_source_that_cannot_be_reached_says_why(world, tmp_path):
    project, store, stock, _ = world
    store.close()                                      # the store goes away after discovery read it
    for f in (project.orchestra.state_dir / "sources").glob("*.reads.json"):
        f.unlink()                                     # and no read of it is held: the host's discovery never read it
    stop = _serve(project)
    proc, read_model = _mcp(project, tmp_path / "home")
    try:
        got = _until(read_model, stock.id, lambda x: "has not read it yet" not in x)
        assert got.startswith(f"  {stock.id}: level not read (measured by source rig): "), got
        assert "no data" not in got and "999" not in got
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
        stop()
