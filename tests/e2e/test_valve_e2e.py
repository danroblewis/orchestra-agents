"""The valve end to end: the Supervisor's ``declare_valve`` inserts a decision step into an on-call
workflow; the Workflows plugin's real floor and program host (``python -m orchestra_workflows run``
and ``programs``, as ``orchestra run`` starts them) run it, as the shipped program another package
gives (W20), for each ticket a Jira event starts; the valve asks a fake model (the messages API);
each ticket goes the way its label says (page, queue, ignore, and the fallback ``ask`` to the
owner's step for an unsure answer), carrying its decision as a field; an unchanged ticket is never
asked twice; and the plugin's instrument ``agents.valves`` writes ``valve_decisions_total``."""

import json
import os
import subprocess
import sys
import time

import pytest
from conftest import agents_project
from fake_model import FakeModel

from orchestra_agents import tooling, web
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore

pytest.importorskip("orchestra_workflows.api")
from orchestra.collect.round import collect
from orchestra.events import Events as OrchestraEvents
from orchestra.metrics import for_project as metrics_for
from orchestra_workflows.api import Record, write

TOKEN = "sk-test-0123456789abcdef"
ONCALL = {"name": "oncall", "items": "ticket", "planner": "supervisor",
          "starts": [{"event": "jira.issue.created", "fields": {"summary": {"data": "summary"}}}],
          "steps": [{"name": "paged", "who": {"outside": "jira"}, "done_when": {"event": "jira.issue.transitioned"},
                     "outcomes": {"done": None}},
                    {"name": "backlog", "who": {"outside": "jira"}, "done_when": {"event": "jira.issue.transitioned"},
                     "outcomes": {"done": None}},
                    {"name": "decide", "who": {"person": "owner"}, "done_when": {"event": "jira.issue.assigned"},
                     "outcomes": {"done": "paged"}}]}
TICKETS = {"HK-1": "the checkout is down for every customer", "HK-2": "the reports page is slow on Mondays",
           "HK-3": "a typo in the footer", "HK-4": "something about the thing"}
ANSWERS = {"down": ("page", 0.95), "slow": ("queue", 0.85), "typo": ("ignore", 0.9), "thing": ("page", 0.3)}


def answer(inputs):
    summary = inputs["fields"]["summary"]
    label, score = next(v for k, v in ANSWERS.items() if k in summary)
    return {"label": label, "score": score, "reason": f"judged from the summary ({label})"}


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def wait_for(check, procs, timeout=60.0, what=lambda: ""):
    end = time.time() + timeout
    while time.time() < end:
        if v := check():
            return v
        if any(p.poll() is not None for p in procs):
            break
        time.sleep(0.2)
    logs = "\n".join(p.args[-2] + ": " + (p.stdout.read() if p.poll() is not None else "(running)") for p in procs)
    raise AssertionError(f"timed out {what()}\n{logs}")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    for k in ("ORCHESTRA_EVENTS_ENDPOINT", "ORCHESTRA_EVENTS_TOKEN", "ORCHESTRA_EVENTS_URL", "ORCHESTRA_METRICS_URL"):
        monkeypatch.delenv(k, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    project = agents_project(repo)
    toml = repo / "orchestra.toml"
    toml.write_text(toml.read_text() + '\n[workflows]\ntick = "1s"\n')
    project.reload_config()
    monkeypatch.setenv("ORCHESTRA_REPO", str(repo))
    token = tmp_path / "model.token"
    token.write_text(TOKEN)
    token.chmod(0o600)
    return project, token


def test_a_valve_routes_each_ticket_by_the_models_label(setup):
    project, token = setup
    o = project.orchestra
    write(o, ONCALL, "state")
    store = AgentStore(project, Events.for_project(project, source="supervisor"))
    with FakeModel(TOKEN, default=answer) as fake:
        args = {"id": "oncall_filter", "question": "Does this ticket need the on-call engineer now?",
                "labels": ["page", "queue", "ignore"], "inputs": {"fields": ["summary"]}, "api": "anthropic",
                "base_url": fake.url, "model": "a-model", "token_file": str(token), "min_confidence": 0.7,
                "max_calls_per_day": 20, "workflow": "oncall", "routes": {"queue": "backlog", "ignore": None},
                "reason": "the owner wants tickets that don't need on-call filtered out"}
        tooling.check_args("declare_valve", args)
        out = tooling.handle(project, store, "declare_valve", args, role="supervisor")
        assert out["workflow"]["step"]["outcomes"] == {"page": "paged", "queue": "backlog", "ignore": None, "ask": "decide"}
        assert "at most 20 calls a day" in out["say"]

        env = {**os.environ, "ORCHESTRA_REPO": str(o.repo_root)}
        procs = [subprocess.Popen([sys.executable, "-m", "orchestra_workflows", part, "--wait"], cwd=o.repo_root, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                 for part in ("run", "programs")]
        try:
            jira = OrchestraEvents.for_project(o, source="person.jira.iruiz")
            bus = OrchestraEvents.for_project(o, source="test")
            wait_for(lambda: [e for e in bus.after(0)[0] if e["type"] == "orchestra.machine.started"
                              and e["source"] == "oncall_filter/1"], procs)
            for key, summary in TICKETS.items():
                jira.publish("jira.issue.created", {"key": key, "summary": summary}, subject=key)
            record = Record.for_project(o)

            def finished():
                done = [e for e in bus.after(0)[0] if e["type"] == "offer.finished" and e["source"].startswith("oncall_filter/")]
                return len(done) >= 4 and done
            done = wait_for(finished, procs)
            assert {e["subject"]: e["data"]["outcome"] for e in done} == {
                "HK-1": "page", "HK-2": "queue", "HK-3": "ignore", "HK-4": "ask"}

            def where(key):
                """The ticket's step, as the floor annotates the record's index, or <end> once it is closed."""
                s = next((s for s in record.items() if s["item"] == key), None)
                if s is None or s.get("closed"):
                    return "<end>" if (record.item(key) or {}).get("ended") else None
                return ((s.get("at") or {}).get("oncall") or {}).get("step")
            routed = {"HK-1": "paged", "HK-2": "backlog", "HK-3": "<end>", "HK-4": "decide"}
            wait_for(lambda: all(where(k) == s for k, s in routed.items()), procs, what=lambda: {k: where(k) for k in routed})
            for key in TICKETS:
                fact = record.item(key)["fields"]["valve_oncall_filter"]["value"]
                assert fact["outcome"] == done[[e["subject"] for e in done].index(key)]["data"]["outcome"]
                assert fact["reason"] and fact["score"] is not None and fact["cached"] is False
            assert "below the declared 0.70" in record.item("HK-4")["fields"]["valve_oncall_filter"]["value"]["reason"]
            assert len(fake.requests) == 4

            jira.publish("jira.issue.created", {"key": "HK-5", "summary": TICKETS["HK-1"]}, subject="HK-5")
            wait_for(lambda: where("HK-5") == "paged", procs)
            assert record.item("HK-5")["fields"]["valve_oncall_filter"]["value"]["cached"] is True
            assert len(fake.requests) == 4                                       # the same inputs: no second call
        finally:
            for p in procs:
                p.terminate()
            for p in procs:
                try:
                    p.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    p.kill()

    every = json.dumps(bus.after(0)[0]) + "".join(
        f.read_text(errors="replace") for f in o.state_dir.rglob("*") if f.is_file() and f.name != "model.token")
    assert TOKEN not in every                                                     # not on the bus, in the record or a log

    [c] = [c for c in web.collectors(project) if c.name == "valves"]
    r = collect(c, o.repo_root, o.key, o.logs_dir / "instruments" / "valves.log")
    assert not r.error, r.error
    metrics_for(o).write(r.points)
    got = {s[1]["outcome"]: s[2][-1][1] for s in metrics_for(o).dump("valve_decisions_total")}
    assert got == {"page": 2, "queue": 1, "ignore": 1, "ask": 1}
    assert all(s[1]["valve"] == "oncall_filter" for s in metrics_for(o).dump("valve_decisions_total"))
