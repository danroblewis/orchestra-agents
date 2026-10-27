"""Goal G1's acceptance: the agents' demo runs end to end on the dummy harness (`demo/run.py
--dummy`), for free. Everything runs as the demo runs it, through a real `orchestra run` and
`orchestra web`, with the real task system and its gate; only the agents are scripted
(demo/dummy.toml). With no API key in the environment, a `claude`, `pi` and `codex` on the PATH
that only note that they ran, and every Python process unable to reach beyond this machine
(no_network/sitecustomize.py), it checks that:

- a ticket is offered, accepted, worked (a real commit), reviewed, gated and landed;
- a second ticket is rejected once by the reviewer, reworked by the same worker instance and
  session, and lands, the work passing directly between them: the hand-offs Orchestra's event map
  computes are worker → reviewer → worker → reviewer → merge_gate, the task system only where it
  acted (the first offer);
- the Supervisor answers a chat turn through the web's /supervisor;
- no model was run and nothing tried the network."""

import os
import stat
from pathlib import Path

import pytest
from conftest import free_port
from orchestra.web.machines import MachineIndex
from test_demo_e2e import AGENTS, TASKS, all_events, chat, git, reported_by, settled, states

from demo.run import get, make_project, plan, start, stop, wait_for, wait_until

GUARD = Path(__file__).parent / "no_network"
KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
        "GEMINI_API_KEY", "OPENROUTER_API_KEY")


def handoffs(events: list[dict], subject: str) -> list[tuple[str, str | None, str]]:
    """The hand-offs Orchestra's Machines page and event map compute from the bus (its
    ``MachineIndex``, behind ``/api/machines``), in the order they arose, for the events about
    ``subject``: (from machine, via event type, to machine). The index reads the bus as it grows."""
    seen: list[dict] = []

    class Grown:                                     # the bus as it stood after each event
        def end(self):
            return len(seen)

        def read(self, since=0, limit=500):
            return seen[since:][-limit:], len(seen)

    idx, out = MachineIndex(Grown()), []
    for ev in events:
        before = dict(idx.handoffs)
        seen.append(ev)
        idx.update()
        if ev.get("subject") == subject:
            out += [k for k, n in idx.handoffs.items() if n > before.get(k, 0)]
    return out


@pytest.fixture
def free_demo(tmp_path, monkeypatch):
    home, logs, bins = tmp_path / "home", tmp_path / "logs", tmp_path / "bin"
    logs.mkdir()
    bins.mkdir()
    for product in ("claude", "pi", "codex"):          # a model product that runs says so and fails
        b = bins / product
        b.write_text(f'#!/bin/sh\necho "{product} $*" >> {logs / "products.log"}\nexit 1\n')
        b.chmod(b.stat().st_mode | stat.S_IEXEC)
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    env = {"ORCHESTRATOR_HOME": str(home), "NO_NETWORK_LOG": str(logs / "network.log"),
           "PYTHONPATH": os.pathsep.join(filter(None, [str(GUARD), os.environ.get("PYTHONPATH")])),
           "PATH": os.pathsep.join([str(bins), os.environ.get("PATH", "")]),
           "CLAUDE_BINARY": str(bins / "claude"), "PI_BINARY": str(bins / "pi"), "CODEX_BINARY": str(bins / "codex")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    target = make_project(tmp_path / "project", dummy=True)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    procs = start(target, port, env=env)
    try:
        wait_for(url, procs)
        yield url, target, logs
    finally:
        stop(procs)


def test_the_demo_runs_end_to_end_on_dummy_agents_with_no_model_and_no_network(free_demo):
    url, target, logs = free_demo
    assert 'harness = "dummy"' in (target / "orchestra.toml").read_text()
    ids = plan(target)
    assert ids == ["DEMO-1", "DEMO-2", "DEMO-3", "DEMO-4"]
    wait_until("the tickets", lambda: settled(url), timeout_s=300)
    assert states(url) == dict.fromkeys(ids, "DONE")

    events = all_events(url)
    by = reported_by(url)
    verdicts = {}
    for e in events:
        if e["type"] in ("review.approved", "changes.requested") and e["source"].startswith("reviewer/"):
            verdicts.setdefault(e["subject"], []).append("APPROVED" if e["type"] == "review.approved" else "REJECTED")

    # DEMO-4, one round: offered, accepted by a dummy worker, worked with a real commit, reviewed,
    # gated and landed on main, where the collector it wrote now runs
    assert len(by["DEMO-4"]) == 2 and by["DEMO-4"][0] in ("worker/1", "worker/2") and by["DEMO-4"][1].startswith("reviewer/"), by["DEMO-4"]
    assert verdicts["DEMO-4"] == ["APPROVED"]
    offered = [e for e in events if e["type"] == "task.assigned" and e["subject"] == "DEMO-4"]
    accepted = [e for e in events if e["type"] == "task.accepted" and e["subject"] == "DEMO-4"]
    asked = [e for e in events if e["type"] == "review.requested" and e["subject"] == "DEMO-4"]
    assert {e["data"]["role"] for e in offered} == {"worker"} and by["DEMO-4"][1] in {e["source"] for e in accepted}   # the queue offered the work
    assert [e["source"] for e in asked] == [by["DEMO-4"][0]]                                 # the worker itself asked for the review
    assert "DEMO-4: measure the repository" in git(target, "log", "--format=%s", "main")
    assert "machines/repo_stats.sh" in git(target, "ls-tree", "-r", "--name-only", "main")
    assert "[[collect]]" in git(target, "show", "main:orchestra.toml")
    assert get(url + f"{TASKS}/tickets/DEMO-4")["notes"][-1]["text"].startswith("landed on main as ")

    # DEMO-3, two rounds: rejected once, reworked in the same worker session, approved, landed
    assert verdicts["DEMO-3"] == ["REJECTED", "APPROVED"]
    assert len(by["DEMO-3"]) == 4 and all(r.startswith("reviewer/") for r in by["DEMO-3"][1::2])   # any reviewer may take a review
    assert by["DEMO-3"][0] == by["DEMO-3"][2] and by["DEMO-3"][0].startswith("worker/")     # reworked by the same worker instance
    back = [e for e in events if e["type"] == "changes.requested" and e["subject"] == "DEMO-3"]
    assert [e["data"]["to"] for e in back] == [by["DEMO-3"][0]]                             # the rejection went to it, not to a queue
    assert handoffs(events, "DEMO-3") == [("task_scheduler", "task.assigned", "worker"), ("worker", "review.requested", "reviewer"),
                                          ("reviewer", "changes.requested", "worker"), ("worker", "review.requested", "reviewer"),
                                          ("reviewer", "review.approved", "merge_gate")]
    assert "def test_longest_first" in git(target, "show", "main:tests/test_line_counts.py")
    assert {"DEMO-3: count the lines of the Python files", "DEMO-3: test that the longest file comes first"} <= \
        set(git(target, "log", "--format=%s", "main").splitlines())
    worker = get(url + f"{AGENTS}/sessions/worker-DEMO-3/transcript")
    assert worker["session"]["harness"] == "dummy" and worker["turns"] == 2

    # the Supervisor answers through the web's /supervisor, with a tool call on the way
    lines = chat(url, "What is going on?")
    assert lines[-1]["type"] == "done" and not lines[-1].get("error")
    assert any(ln["type"] == "tool_start" and ln.get("name") == "project_status" for ln in lines)
    assert any(ln["type"] == "assistant_end" and ln["text"].startswith("I read the project's status") for ln in lines)

    # no model ran, and nothing tried the network; the guard was in the agents' own processes
    assert not (logs / "products.log").exists(), (logs / "products.log").read_text()
    network = (logs / "network.log").read_text().splitlines()
    assert not [ln for ln in network if " refused " in ln], [ln for ln in network if " refused " in ln]
    assert any("orchestra_agents.harness.dummy_agent" in ln for ln in network)
    assert any("tasking" in ln for ln in network) and any("orchestra web" in ln or "orchestra run" in ln or "-m orchestra" in ln for ln in network)
