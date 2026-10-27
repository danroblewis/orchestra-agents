"""The demo, end to end through a real Orchestra: the template copied to a temporary directory,
`orchestra run` and `orchestra web` on a free port, and what the host starts (`tasking run`,
`orchestra-agents run` with the fake Claude Code standing in for the real one, and the shell
worker from `[[machines]]`), all as processes (demo/run.py's own functions); the tickets created
as a planner would (requests over the bus); and the rest read over HTTP and from git: the
plugins' pages, the task system's tickets and board, Orchestra's /api/machines, the agents'
sessions and transcripts, the Supervisor chat, and main, where every ticket's branch lands."""

import json
import shutil
import stat
import subprocess
import urllib.request
from pathlib import Path

import pytest
from conftest import free_port
from orchestra import runtime
from orchestra.config import load_project

from demo.run import PLAN, get, make_project, plan, start, stop, wait_for, wait_until

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
        # the host stopped what it launched: no process it recorded outlives it
        left = runtime.read(load_project(target)).get("processes") or {}
        assert not [n for n, r in left.items() if r.get("pid") and runtime.alive(r["pid"])], left


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


def all_events(url: str) -> list[dict]:
    """The whole event log, a page at a time."""
    out, since = [], 0
    while True:
        page = get(url + f"/api/events?since={since}")
        if not page["events"]:
            return out
        out += page["events"]
        since = page["next"]


def reported_by(url: str) -> dict[str, list[str]]:
    """Who reported on each ticket, in order, from the bus.
    Both workers may accept a command ticket's offer: the first keeps it, the other is released."""
    out: dict[str, list[str]] = {}
    for e in all_events(url):
        if e["type"] == "task.reported":
            out.setdefault(e["subject"], []).append(e["source"])
    return out


def git(target: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=target, capture_output=True, text=True, check=True).stdout


def test_a_program_worker_and_ai_agents_work_the_same_queue_and_land_on_main(demo):
    url, target = demo
    ids = plan(target)
    assert ids == ["DEMO-1", "DEMO-2", "DEMO-3", "DEMO-4"]

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
    wait_until("the reviewer on a ticket", reviewing, timeout_s=120)
    s, card = seen["session"], seen["card"]
    assert s["instance"] == "reviewer/1" and s["ticket"]["state"] == "IN_REVIEW" and s["ticket"]["title"] in [t["title"] for t in PLAN["tickets"]]
    assert card["assignment"]["instance"] == "reviewer/1" and card["assignment"]["text"] == "Reading RESULT.md against the acceptance criteria."

    wait_until("the tickets", lambda: settled(url), timeout_s=300)
    assert states(url) == dict.fromkeys(ids, "DONE")
    by = reported_by(url)
    # each offer has one holder, and only it reports; the program takes only the command tickets, an AI worker any
    for tid in ids:
        workers, reviewers = by[tid][:-1], by[tid][-1:]
        assert len(workers) == 1 and reviewers == ["reviewer/1"], (tid, by[tid])
        assert workers[0] in ("worker/1", "worker/2", "worker/shell"), (tid, by[tid])           # the AI workers, the program
        assert workers[0] != "worker/shell" or tid in ("DEMO-1", "DEMO-2"), (tid, by[tid])
    assert [e["source"] for e in all_events(url) if e["type"] == "task.requested"] == ["planner"] * len(ids)
    one = get(url + f"{TASKS}/tickets/DEMO-1")
    assert [n["author"] for n in one["notes"]] == ["worker", "reviewer", "merge_gate"] and one["notes"][1]["text"].startswith("APPROVED")
    assert one["notes"][-1]["text"].startswith("landed on main as ")

    # every ticket's branch landed on main through the merge gate
    landed = git(target, "ls-tree", "-r", "--name-only", "main").split()
    assert {"notes/toolchain.txt", "notes/started.txt", "tools/line_counts.py", "tests/test_line_counts.py", "machines/repo_stats.sh"} <= set(landed)
    toml = git(target, "show", "main:orchestra.toml")
    assert "[[collect]]" in toml and "machines/repo_stats.sh" in toml

    # the collector that landed runs: `orchestra run` read the root's orchestra.toml, which the
    # landing updated, announced the collector on its behalf, runs it, and its series are in the
    # project's store, offered to discovery
    def announced() -> bool:
        return any(e["type"] == "orchestra.machine.started" and e["source"] == "repo_stats" for e in all_events(url))
    wait_until("the landed collector announced", announced, timeout_s=60)

    def written() -> bool:
        rows = {m["name"]: m for m in get(url + "/api/core/metrics/catalog")["metrics"]}
        labels = (rows.get("repo_files") or {}).get("labels") or {}
        return load_project(target).key in labels.get("project", {}).get("values", []) \
            and "repo_stats" in labels.get("machine", {}).get("values", [])
    wait_until("the collector's series in the store", written, timeout_s=60)

    # a finished ticket's sessions are archived (on the host's next tick), their transcripts still served
    wait_until("the archive", lambda: not [s for s in get(url + f"{AGENTS}/agents")["sessions"] if s["role"] == "reviewer"], timeout_s=15)
    tr = get(url + f"{AGENTS}/sessions/reviewer-DEMO-3/transcript")
    assert tr["session"]["status"] == "archived" and tr["session"]["ticket_title"] == "Count the lines of the Python files"
    assert tr["session"]["harness"] == "claude_code" and tr["turns"] == 1
    assert any(m["role"] == "assistant" and any(c["name"] == "report_review" for c in m["tool_calls"]) for m in tr["messages"])
    assert tr["started_with"].startswith("# You are an Orchestra Reviewer")      # the code reviewer's prompt

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
    head = get_text(url + "/").split("</head>")[0]                # each plugin's look, linked on every page
    assert "/plugins/agents/static/agents.css" in head and "/plugins/tasks/static/tasks.css" in head
    org = get(url + f"{AGENTS}/org")
    assert [a["name"] for a in org["agents"]] == ["supervisor", "coordinator", "worker", "reviewer"]   # serves worker and reviewer
    assert [t["name"] for t in org["teams"]] == ["engineering"]
    assert get(url + f"{AGENTS}/state")["probes"] == []

    plan(target)
    wait_until("the tickets", lambda: settled(url), timeout_s=300)
    seen = {}

    def observed() -> bool:
        d = get(url + "/api/machines")
        if not d.get("ok"):
            return False
        seen.update({m["id"]: m for m in d["machines"]})
        return {"agents", "supervisor", "coordinator", "reviewer", "worker", "task_scheduler"} <= set(seen)
    wait_until("the machines", observed, timeout_s=60)
    host = seen["agents"]
    assert host["package"] == "agents" and host["mode"] == "continuous" and host["kind"] == "program" and host["active"]
    assert host["subscribes"] == ["ticket.transitioned"]            # what the host acts on as itself; the agents announce their own
    log = get(url + "/api/machines/agents/log")
    assert log["ok"] and any("control endpoint" in line for line in log["lines"])
    for name in ("supervisor", "coordinator"):                                              # resident: continuous, whatever wakes them
        m = seen[name]
        assert m["package"] == "agents" and m["mode"] == "continuous" and m["kind"] == "agent" and not m.get("unseen")
        assert m["about"]["harness"] == "claude_code" and m["about"]["model"] == "haiku" and m["instances"] == []
    reviewer = seen["reviewer"]
    assert reviewer["package"] == "agents" and reviewer["mode"] == "ad hoc" and reviewer["kind"] == "agent"
    announced = [e for e in all_events(url) if e["type"] == "orchestra.machine.started" and e["source"] == "reviewer/1"]
    assert len(announced) == 1, "an instance announces itself once in the host's lifetime, not per review"
    assert [i["id"] for i in reviewer["instances"]] == ["1"] and {"task.accepted", "task.reported", "task.working"} <= {p["type"] for p in reviewer["publishes"]}
    # the worker role has two kinds of machine: the program (instance shell, started by `orchestra run`
    # from [[machines]]) and the agents' AI workers (1, 2); one machine, worker, with their instances
    worker = seen["worker"]
    assert "shell" in [i["id"] for i in worker["instances"]] or "shell" in (worker.get("logs") or []), worker
    started = {e["source"]: e["data"] for e in all_events(url) if e["type"] == "orchestra.machine.started"}
    assert started["worker/shell"]["kind"] == "program" and "task.assigned" in started["worker/shell"]["listens_for"]
    assert seen["task_scheduler"]["package"] == "tasks"
    assert seen["planner"]["mode"] == "ad hoc" and seen["planner"]["kind"] == "program"
