"""A live check of the modeling tools with a real model: Claude Code on Haiku, as the Supervisor,
asked "What would happen if review were twice as fast?" about a project whose metrics are a ticket
pipeline (tickets created, worked, then reviewed). It should read the model, make an experiment
that speeds up the review flow, run it forward and answer with the numbers.

It costs money and needs a logged-in ``claude``, so it runs only when asked:

    ORCHESTRA_LIVE_HAIKU=1 uv run python -m pytest -q -s tests/e2e/test_modeling_live.py

It prints the reply, the tools called, the cost (from Claude Code's own ``result`` events) and the
transcript's path. Everything else here is the agents' real path: the harness, the MCP server,
the session's token, the Supervisor's role prompt and tool groups."""

import json
import math
import os
import random
import shutil
import stat
import subprocess
import time

import pytest
from conftest import agents_project

pytestmark = pytest.mark.skipif(os.environ.get("ORCHESTRA_LIVE_HAIKU") != "1" or not shutil.which("claude"),
                                reason="a live run on Haiku: set ORCHESTRA_LIVE_HAIKU=1, with a logged-in claude on the PATH")
QUESTION = "What would happen if review were twice as fast?"
DISCOVERY = {'# strategy = "kalman_lna"': 'strategy = "conservation"', 'window = "60m"': 'window = "120m"', 'step = "30s"': 'step = "5s"'}


def poisson(rng, lam):
    if lam <= 0:
        return 0
    k, p, limit = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def pipeline(store, key: str, end: float, minutes: int = 120):
    """Tickets are created at 2 a minute; each waiting one is taken up at 0.5 a minute into review;
    each in review is reviewed at 0.25 a minute: review is the slow step (about 8 wait there)."""
    from orchestra.metrics import Point

    rng = random.Random(7)
    q, r, a_t, d_t, v_t, pts = 4, 8, 0, 0, 0, []
    lab = {"project": key}
    for i in range(minutes * 12):
        t = end - minutes * 60 + i * 5
        a = poisson(rng, 2.0 / 12)
        d = min(q, poisson(rng, 0.5 * q / 12))
        v = min(r, poisson(rng, 0.25 * r / 12))
        q, r = q + a - d, r + d - v
        a_t, d_t, v_t = a_t + a, d_t + d, v_t + v
        pts += [Point("tickets_waiting", q, t, lab), Point("tickets_in_review", r, t, lab), Point("tickets_created_total", a_t, t, lab),
                Point("tickets_submitted_total", d_t, t, lab), Point("tickets_reviewed_total", v_t, t, lab)]
    store.write(pts)


def test_haiku_answers_a_what_if_with_an_experiment(tmp_path, monkeypatch):
    from orchestra.metrics import for_project
    from orchestra.sd.experiments.api import Experiments
    from orchestra.sd.rediscover import rediscover

    from orchestra_agents import access, prompting
    from orchestra_agents.builtins import ROLES
    from orchestra_agents.events import Events
    from orchestra_agents.harness import SessionSpec, get_harness
    from orchestra_agents.store import AgentStore

    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    for k in ("ORCHESTRA_EVENTS_URL", "ORCHESTRA_METRICS_URL", "ORCHESTRA_SOURCE", "ORCHESTRA_ROLE", access.TOKEN_ENV):
        monkeypatch.delenv(k, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    project = agents_project(repo, harness="claude_code", model="haiku")
    text = (repo / "orchestra.toml").read_text()
    for a, b in DISCOVERY.items():                     # Orchestra's starter file, discovering on a 5 s grid
        assert a in text, a
        text = text.replace(a, b, 1)
    (repo / "orchestra.toml").write_text(text)
    project.reload_config()
    metrics = for_project(project.orchestra)
    pipeline(metrics, project.orchestra.key, time.time())
    rediscover(project.orchestra, metrics)
    assert {f["id"] for f in Experiments(project.orchestra).model()["model"]["flows"]} >= {"tickets_reviewed_total"}

    log = tmp_path / "claude-stream.jsonl"                 # Claude Code's own stream, for its cost
    wrapper = tmp_path / "claude"
    wrapper.write_text(f'#!/bin/bash\n"{shutil.which("claude")}" "$@" | tee -a "{log}"\nexit ${{PIPESTATUS[0]}}\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CLAUDE_BINARY", str(wrapper))

    store = AgentStore(project, Events.for_project(project, source="supervisor"))
    sid = "supervisor-live"
    spec = SessionSpec(session_id=sid, session_dir=project.sessions_dir, cwd=repo, system_prompt=prompting.role_prompt(project, "supervisor"),
                       role="supervisor", tool_groups=ROLES["supervisor"].tools,
                       env={"ORCHESTRA_ROLE": "supervisor", "ORCHESTRA_REPO": str(repo), "ORCHESTRA_TICKET": "", "ORCHESTRA_SOURCE": "supervisor",
                            access.TOKEN_ENV: access.issue(store, sid, "supervisor")},
                       log_path=project.logs_dir / "agents" / f"{sid}.stderr.log", model="haiku")
    (project.logs_dir / "agents").mkdir(parents=True, exist_ok=True)
    session = get_harness("claude_code").start(spec)
    session.open()
    calls = []
    res = session.prompt(QUESTION, timeout=900, on_event=lambda ev: calls.append(ev["name"]) if ev.get("type") == "tool_start" else None,
                         idle_timeout=600)
    session.close()

    results = [json.loads(ln) for ln in log.read_text().splitlines() if ln.startswith("{") and '"type":"result"' in ln.replace(" ", "")]
    cost = sum(float(r.get("total_cost_usd") or 0) for r in results)
    print("\nREPLY:\n" + res.text + f"\n\nTOOLS: {calls}\nCOST: ${cost:.4f}\nTRANSCRIPT: {session.session_file}\nSTREAM: {log}")
    assert not res.error and not res.timed_out, res
    names = [c.removeprefix("mcp__orchestra__") for c in calls]
    assert "create_experiment" in names and ("run_forward" in names or "run_replay" in names), names
    mine = [e for e in Experiments(project.orchestra).list(owner="supervisor")["experiments"]]
    assert mine, "it made an experiment"
    changed = [c for e in mine for c in Experiments(project.orchestra).diff(e["id"])["diff"]["flows"]["changed"]]
    assert any(c["id"] == "tickets_reviewed_total" for c in changed), changed
    assert any(ch.isdigit() for ch in res.text), "the answer has numbers"
