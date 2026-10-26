"""The demo, end to end through a real Orchestra: the template copied to a temporary directory,
`orchestra run` and `orchestra web` on a free port, `tasking run`, `orchestra-agents run` with the
fake Claude Code standing in for the real one, and the shell worker, all as processes
(demo/run.py's own functions); the tickets created as a planner would (requests over the bus);
and the rest read over HTTP: the plugins' pages, the task system's tickets and board, Orchestra's
/api/machines, the agents' sessions and transcripts, and the Supervisor chat."""

import json
import shutil
import stat
import urllib.request
from pathlib import Path

import pytest
from conftest import free_port

from demo.run import get, make_project, plan, start, stop, wait_for, wait_until

AGENTS, TASKS = "/api/plugins/agents", "/api/plugins/tasks"
FAKE = Path(__file__).parent / "fake_claude.py"


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """The demo running against a temporary home, with the fake Claude Code (a slow reviewer, so
    it can be seen holding a ticket); every process is stopped afterwards, whatever the test did."""
    home = tmp_path / "home"
    fake = tmp_path / "claude"
    shutil.copy(FAKE, fake)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {"ORCHESTRATOR_HOME": str(home), "CLAUDE_BINARY": str(fake), "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-home"),
           "FAKE_CLAUDE_SLOW": "12"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    target = make_project(tmp_path / "project")
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    procs = start(target, port, env=env)
    try:
        wait_for(url, procs)
        yield url, target
    finally:
        stop(procs)
        assert all(p.poll() is not None for p in procs.values())


def states(url: str) -> dict[str, str]:
    return {t["id"]: t["state"] for t in get(url + f"{TASKS}/state")["tickets"]}


def settled(url: str) -> bool:
    s = states(url)
    return bool(s) and all(v in ("DONE", "BLOCKED") for v in s.values())


def get_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.read().decode()


def chat(url: str, message: str) -> list[dict]:
    """One message to the Supervisor: the NDJSON stream the web plugin proxies from the host."""
    req = urllib.request.Request(url + f"{AGENTS}/supervisor", method="POST", data=json.dumps({"message": message}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return [json.loads(line) for line in r.read().decode().splitlines() if line.strip()]


def test_a_program_worker_and_an_ai_reviewer_work_the_same_queue(demo):
    url, target = demo
    ids = plan(target, 3)
    assert ids == ["DEMO-1", "DEMO-2", "DEMO-3"]

    # while the reviewer holds a ticket: the agents' sessions name it, and the task system's board
    # shows what the reviewer said it was doing (the heartbeat's text)
    seen = {}

    def reviewing() -> bool:
        live = [s for s in get(url + f"{AGENTS}/agents")["sessions"] if s["status"] == "running" and s["role"] == "reviewer"]
        board = {t["id"]: t for t in get(url + f"{TASKS}/board")["tickets"]}
        for s in live:
            t = board.get((s.get("ticket") or {}).get("id"))
            if t and t["assignment"] and t["assignment"]["text"]:
                seen.update(session=s, card=t)
                return True
        return False
    wait_until("the reviewer on a ticket", reviewing, timeout_s=90)
    s, card = seen["session"], seen["card"]
    assert s["instance"] == "reviewer/1" and s["ticket"]["state"] == "IN_REVIEW" and s["ticket"]["title"] in ("Say hello", "List the project's files")
    assert card["assignment"]["instance"] == "reviewer/1" and card["assignment"]["text"] == "Reading RESULT.md against the acceptance criteria."

    wait_until("the tickets", lambda: settled(url), timeout_s=180)
    assert states(url) == {"DEMO-1": "DONE", "DEMO-2": "DONE", "DEMO-3": "DONE"}
    evs = get(url + "/api/events")["events"]                       # the last 500: the whole demo is fewer
    protocol = [(e["type"], e["source"]) for e in evs if e.get("subject") == "DEMO-1" and e["type"] in ("task.assigned", "task.accepted", "task.reported")]
    assert protocol == [("task.assigned", "task_scheduler"), ("task.accepted", "worker/1"), ("task.reported", "worker/1"),
                        ("task.assigned", "task_scheduler"), ("task.accepted", "reviewer/1"), ("task.reported", "reviewer/1")]
    assert [e["source"] for e in evs if e["type"] == "task.requested"] == ["planner"] * 3
    one = get(url + f"{TASKS}/tickets/DEMO-1")
    assert [n["author"] for n in one["notes"]] == ["worker", "reviewer"] and one["notes"][1]["text"].startswith("APPROVED")

    # a finished ticket's sessions are archived (on the host's next tick), their transcripts still served
    wait_until("the archive", lambda: not [s for s in get(url + f"{AGENTS}/agents")["sessions"] if s["role"] == "reviewer"], timeout_s=15)
    tr = get(url + f"{AGENTS}/sessions/reviewer-DEMO-1/transcript")
    assert tr["session"]["status"] == "archived" and tr["session"]["ticket_title"] == "Say hello" and tr["session"]["harness"] == "claude_code"
    assert tr["turns"] == 1 and any(m["role"] == "assistant" and any(c["name"] == "report_review" for c in m["tool_calls"]) for m in tr["messages"])
    assert tr["started_with"].startswith("# You are a Reviewer")

    # the Supervisor answers through the host's control endpoint, as the web proxies it
    lines = chat(url, "What is going on?")
    assert lines[-1]["type"] == "done" and not lines[-1].get("error")
    assert any(ln["type"] == "assistant_end" and ln["text"].startswith("supervisor acknowledges") for ln in lines)
    sup = get(url + f"{AGENTS}/sessions/supervisor/transcript")
    assert sup["turns"] == 1 and sup["session"]["alive"]
    assert get(url + f"{AGENTS}/attention")["message"]["text"].startswith("supervisor acknowledges")


def test_orchestra_serves_both_plugins_and_observes_the_agents_as_machines(demo):
    url, target = demo
    plugins = get(url + "/api/plugins")
    assert plugins["errors"] == []
    assert sorted(plugins["pages"], key=lambda p: p["plugin"]) == [{"plugin": "agents", "path": "/agents", "title": "Agents"},
                                                                    {"plugin": "tasks", "path": "/tickets", "title": "Tickets"}]
    assert set(plugins["scripts"]) == {"/plugins/agents/static/attention.js", "/plugins/agents/static/machine-cards.js", "/plugins/tasks/static/tasks-boot.js"}
    html = get_text(url + "/agents")
    assert "<title>Agents</title>" in html and "/plugins/agents/static/agents.js" in html
    org = get(url + f"{AGENTS}/org")
    assert [a["name"] for a in org["agents"]] == ["supervisor", "coordinator", "reviewer"]        # the worker role is the shell worker's
    assert [t["name"] for t in org["teams"]] == ["engineering"]
    assert get(url + f"{AGENTS}/state")["probes"] == []

    plan(target, 3)
    wait_until("the tickets", lambda: settled(url), timeout_s=180)
    seen = {}

    def observed() -> bool:
        d = get(url + "/api/machines")
        if not d.get("ok"):
            return False
        seen.update({m["id"]: m for m in d["machines"]})
        return {"agents", "supervisor", "coordinator", "reviewer", "worker", "task_scheduler"} <= set(seen)
    wait_until("the machines", observed, timeout_s=60)
    host = seen["agents"]
    assert host["package"] == "agents" and host["mode"] == "continuous" and host["about"]["kind"] == "program" and host["active"]
    assert {"task.assigned", "task.released", "task.attention_needed"} <= set(host["subscribes"])
    log = get(url + "/api/machines/agents/log")
    assert log["ok"] and any("control endpoint" in line for line in log["lines"])
    for name in ("supervisor", "coordinator"):                                              # resident: continuous, whatever wakes them
        m = seen[name]
        assert m["package"] == "agents" and m["mode"] == "continuous" and m["about"]["kind"] == "agent" and not m.get("unseen")
        assert m["about"]["harness"] == "claude_code" and m["about"]["model"] == "haiku" and m["instances"] == []
    reviewer = seen["reviewer"]
    assert reviewer["package"] == "agents" and reviewer["mode"] == "ad hoc" and reviewer["about"]["kind"] == "agent"
    assert [i["id"] for i in reviewer["instances"]] == ["1"] and {"task.accepted", "task.reported", "task.working"} <= {p["type"] for p in reviewer["publishes"]}
    worker = seen["worker"]
    assert worker["package"] is None and worker["about"]["kind"] == "program" and worker["mode"] == "continuous"
    assert [i["id"] for i in worker["instances"]] == ["1"]
    assert seen["task_scheduler"]["package"] == "tasks"
    assert "task.attention_needed" in get(url + f"{AGENTS}/eventmap")["known_types"]
