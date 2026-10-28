"""find_balance across two fake stores, as the agents call it, then declared and discovered.

A queue's depth lives in the main store with its publishes; what drains it is counted in a second
store that writes once a minute, beside a staging copy. The Coordinator calls ``find_balance``
through the MCP server the harness starts (its grant), which searches both stores and returns the
balance with the ``[[series]]`` that declare it; the Supervisor declares them with ``set_setting``,
and Orchestra's own discovery, reading the two stores, makes the stock of them."""

import json
import os
import random
import subprocess
import sys
import time

import pytest
from conftest import agents_project
from fake_store import FakeStore

from orchestra_agents import access, tooling
from orchestra_agents.builtins import ROLES
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore

STEP = 15.0
SPAN = 80 * 60


def _stores(now: float):
    rnd = random.Random(7)
    n = int(SPAN / STEP)
    t0 = now - SPAN
    ins = [rnd.randint(0, 20) for _ in range(n)]
    outs = [rnd.randint(0, 20) for _ in range(n)]
    other = [rnd.randint(0, 9) for _ in range(n)]
    noise = [rnd.randint(0, 30) for _ in range(n)]

    def cum(xs, base=0.0):
        out, total = [], base
        for x in xs:
            total += x
            out.append(total)
        return out
    times = [t0 + i * STEP for i in range(n)]
    depth = cum([a - b for a, b in zip(ins, outs)], 500.0)
    shop = [
        ("jobs_queue_messages", {"queue": "jobs"}, list(zip(times, depth))),
        ("jobs_queue_messages", {"queue": "other"}, list(zip(times, cum(other, 40.0)))),
        ("jobs_published_total", {"queue": "jobs"}, list(zip(times, cum(ins)))),
        ("jobs_published_total", {"queue": "other"}, list(zip(times, cum(other)))),
        ("jobs_payload_bytes_total", {"queue": "jobs"}, list(zip(times, cum([1000 * x for x in ins])))),
        ("http_requests_total", {"service": "api"}, list(zip(times, cum(noise)))),
    ]
    done = cum(outs)
    minute = [(times[i], done[i]) for i in range(0, n, 4)]          # once a minute
    biz = [
        ("jobs_done_total", {"env": "prod"}, minute),
        ("jobs_done_total", {"env": "staging"}, [(t, v / 10) for t, v in minute]),
    ]
    return FakeStore(shop, meta={}), FakeStore(biz, meta={})


@pytest.fixture
def stores():
    shop, biz = _stores(time.time())
    yield shop, biz
    shop.close()
    biz.close()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    p = agents_project(repo)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    p.reload_config()
    return p


def _mcp(project, tmp_path, role: str):
    store = AgentStore(project)
    token = access.issue(store, role, role)
    env = {**os.environ, "ORCHESTRATOR_HOME": str(tmp_path / "home"), "ORCHESTRA_REPO": str(project.repo_root),
           "ORCHESTRA_ROLE": role, "ORCHESTRA_TOOLS": ",".join(ROLES[role].tools), access.TOKEN_ENV: token}
    p = subprocess.Popen([sys.executable, "-m", "orchestra_agents.harness.mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True, env=env, bufsize=1)
    n = 0

    def rpc(method, params=None):
        nonlocal n
        n += 1
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": params or {}}) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())
    rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
    return p, rpc


def test_a_queue_drained_from_another_store_is_found_declared_and_discovered(project, stores, tmp_path):
    shop, biz = stores
    sup = AgentStore(project, Events.for_project(project, source="supervisor"))
    sources = [{"name": "shop", "url": shop.url, "infer": False}, {"name": "biz", "url": biz.url, "infer": False, "settle": "90s"}]
    tooling.handle(project, sup, "set_setting", {"key": "sources", "value": json.dumps(sources), "target": "repo",
                                                  "reason": "The shop's queue and the business store."}, role="supervisor")

    # the Coordinator holds the tool (read only), through the MCP server its harness starts
    proc, rpc = _mcp(project, tmp_path, "coordinator")
    try:
        assert "find_balance" in {t["name"] for t in rpc("tools/list")["result"]["tools"]}
        r = rpc("tools/call", {"name": "find_balance", "arguments": {"level": 'jobs_queue_messages{queue="jobs"}', "step_s": 30,
                                                                     "sources": [{"name": "biz"}, {"name": "shop"}]}})
        r = r["result"]
        assert not r.get("isError"), r
        got = json.loads(r["content"][0]["text"])
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
    best = got["balances"][0]
    assert best["accepted"], best
    assert sorted((t["store"], t["select"], t["role"]) for t in best["terms"]) == [
        ("biz", 'jobs_done_total{env="prod"}', "outflow"), ("shop", 'jobs_published_total{queue="jobs"}', "inflow")]
    assert "jobs_done_total" in best["fit"]["on"]                     # balanced on the slow store's writes
    assert not any("staging" in k for k in got["candidates_read"])    # a staging copy is never read
    assert not any("bytes" in k for k in got["candidates_read"])      # nor a counter in another unit
    assert best["declare"][0] == {"source": "shop", "name": "jobs_level", "query": 'sum(jobs_queue_messages{queue="jobs"})',
                                  "kind": "level", "stock": "jobs", "role": "level"}

    # a worker has no such tool
    with pytest.raises(Exception, match="find_balance"):
        tooling.handle(project, sup, "find_balance", {"level": "x"}, role="worker")

    # the Supervisor declares it; Orchestra's discovery reads both stores and makes the stock of it
    tooling.handle(project, sup, "set_setting", {"key": "series", "value": json.dumps(best["declare"]), "target": "repo",
                                                  "reason": "The jobs queue, its publishes and what the workers finish."},
                   role="supervisor")
    from orchestra.metrics import for_project as metrics_for
    from orchestra.sd.rediscover import rediscover
    project.orchestra.reload_config()
    res = rediscover(project.orchestra, metrics_for(project.orchestra), window_s=3600, step_s=30)
    model = res.model
    jobs = next((s for s in model.stocks if s.series and s.series.name == "shop:jobs_level"), None)
    assert jobs is not None, [s.series for s in model.stocks]
    flows = {(f.counter.name if f.counter else None, f.src, f.dst) for f in model.flows}
    assert ("shop:jobs_published", None, jobs.id) in flows and ("biz:jobs_done", jobs.id, None) in flows, flows
