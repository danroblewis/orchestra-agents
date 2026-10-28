"""The demo, end to end through a real Orchestra: the template copied to a temporary directory,
`orchestra run` and `orchestra web` on a free port, and what the host starts (`orchestra tasks run`,
`orchestra agents run` with the fake Claude Code standing in for the real one, and the shell
worker from `[[machines]]`), all as processes (demo/run.py's own functions); the tickets created
as a planner would (requests over the bus); and the rest read over HTTP and from git: the
plugins' pages, the task system's tickets and board, Orchestra's /api/machines, the agents'
sessions and transcripts, the Supervisor chat, and main, where every ticket's branch lands."""

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from conftest import free_port
from orchestra import runtime
from orchestra.config import load_project

from demo.run import PLAN, Web, make_project, plan, start, stop, wait_for, wait_until, web_token

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
        web = Web(url, web_token(target, env))
        wait_for(web, procs)
        yield web, target
    finally:
        stop(procs)
        assert all(p.poll() is not None for p in procs.values())
        # the host stopped what it launched: no process it recorded outlives it
        left = runtime.read(load_project(target)).get("processes") or {}
        assert not [n for n, r in left.items() if r.get("pid") and runtime.alive(r["pid"])], left


def states(web: Web) -> dict[str, str]:
    return {t["id"]: t["state"] for t in web.get(f"{TASKS}/state")["tickets"]}


def settled(web: Web) -> bool:
    s = states(web)
    return bool(s) and all(v in ("DONE", "BLOCKED") for v in s.values())


def chat(web: Web, message: str) -> list[dict]:
    """One message to the Supervisor: the NDJSON stream the web plugin proxies from the host."""
    with web.open(f"{AGENTS}/supervisor", {"message": message}, timeout=60) as r:
        return [json.loads(line) for line in r.read().decode().splitlines() if line.strip()]


def all_events(web: Web) -> list[dict]:
    """The whole event log, a page at a time."""
    out, since = [], 0
    while True:
        page = web.get(f"/api/events?since={since}")
        if not page["events"]:
            return out
        out += page["events"]
        since = page["next"]


ENDS = ("review.requested", "changes.requested", "review.approved", "task.reported")


def reported_by(web: Web) -> dict[str, list[str]]:
    """Who ended each turn on each ticket, in order, from the bus: a review requested, changes
    requested, an approval, a report. Both workers may claim a command ticket's offer: the first
    claim on the bus holds it, and the other starts nothing."""
    out: dict[str, list[str]] = {}
    for e in all_events(web):
        if e["type"] in ENDS:
            out.setdefault(e["subject"], []).append(e["source"])
    return out


def git(target: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=target, capture_output=True, text=True, check=True).stdout


def test_a_program_worker_and_ai_agents_work_the_same_queue_and_land_on_main(demo):
    web, target = demo
    ids = plan(target)
    assert ids == ["DEMO-1", "DEMO-2", "DEMO-3", "DEMO-4"]

    # while the reviewer holds a ticket: the agents' sessions name it, and the task system's board
    # shows what the reviewer said it was doing (the heartbeat's text)
    seen = {}

    def reviewing() -> bool:
        live = [s for s in web.get(f"{AGENTS}/agents")["sessions"] if s["status"] == "running" and s["role"] == "reviewer"]
        board = {t["id"]: t for t in web.get(f"{TASKS}/board")["tickets"]}
        for s in live:
            t = board.get((s.get("ticket") or {}).get("id"))
            if t and t["assignment"] and t["assignment"]["text"]:
                seen.update(session=s, card=t)
                return True
        return False
    wait_until("the reviewer on a ticket", reviewing, timeout_s=120)
    s, card = seen["session"], seen["card"]
    assert s["instance"].startswith("reviewer/") and s["ticket"]["state"] == "IN_REVIEW" and s["ticket"]["title"] in [t["title"] for t in PLAN["tickets"]]
    assert card["assignment"]["instance"] == s["instance"] and card["assignment"]["text"] == "Reading RESULT.md against the acceptance criteria."

    wait_until("the tickets", lambda: settled(web), timeout_s=300)
    assert states(web) == dict.fromkeys(ids, "DONE")
    by = reported_by(web)
    # each offer has one holder, and only it ends the turn; the program takes only the command tickets, an AI
    # worker any; any reviewer instance may take a review a worker asked for (reviews run side by side)
    for tid in ids:
        workers, reviewers = by[tid][:-1], by[tid][-1:]
        assert len(workers) == 1 and reviewers[0].startswith("reviewer/"), (tid, by[tid])
        assert workers[0] in ("worker/1", "worker/2", "worker/shell"), (tid, by[tid])           # the AI workers, the program
        assert workers[0] != "worker/shell" or tid in ("DEMO-1", "DEMO-2"), (tid, by[tid])
    assert [e["source"] for e in all_events(web) if e["type"] == "task.requested"] == ["planner"] * len(ids)
    one = web.get(f"{TASKS}/tickets/DEMO-1")
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
    def written() -> bool:                      # an instrument, named by its label (Orchestra: measurements are not machines)
        rows = {m["name"]: m for m in web.get("/api/core/metrics/catalog")["metrics"]}
        labels = (rows.get("repo_files") or {}).get("labels") or {}
        return load_project(target).key in labels.get("project", {}).get("values", []) \
            and "repo_stats" in labels.get("instrument", {}).get("values", [])
    wait_until("the collector's series in the store", written, timeout_s=60)

    # a finished ticket's sessions are archived (on the host's next tick), their transcripts still served
    wait_until("the archive", lambda: not [s for s in web.get(f"{AGENTS}/agents")["sessions"] if s["role"] == "reviewer"], timeout_s=15)
    tr = web.get(f"{AGENTS}/sessions/reviewer-DEMO-3/transcript")
    assert tr["session"]["status"] == "archived" and tr["session"]["ticket_title"] == "Count the lines of the Python files"
    assert tr["session"]["harness"] == "claude_code" and tr["turns"] == 1
    assert any(m["role"] == "assistant" and any(c["name"] == "report_review" for c in m["tool_calls"]) for m in tr["messages"])
    assert tr["started_with"].startswith("# You are an Orchestra Reviewer")      # the code reviewer's prompt

    # the Supervisor answers through the host's control endpoint, as the web proxies it
    lines = chat(web, "What is going on?")
    assert lines[-1]["type"] == "done" and not lines[-1].get("error")
    assert any(ln["type"] == "assistant_end" and ln["text"].startswith("supervisor acknowledges") for ln in lines)
    sup = web.get(f"{AGENTS}/sessions/supervisor/transcript")
    assert sup["turns"] == 1 and sup["session"]["alive"]
    assert web.get(f"{AGENTS}/attention")["message"]["text"].startswith("supervisor acknowledges")


def test_orchestra_serves_both_plugins_and_observes_the_agents_as_machines(demo):
    web, target = demo
    plugins = web.get("/api/plugins")
    assert plugins["errors"] == []
    assert sorted(plugins["pages"], key=lambda p: p["plugin"]) == [{"plugin": "agents", "path": "/agents", "title": "Agents"},
                                                                    {"plugin": "tasks", "path": "/tickets", "title": "Tickets"}]
    assert set(plugins["scripts"]) == {"/plugins/agents/static/attention.js", "/plugins/agents/static/machine-cards.js",
                                      "/plugins/agents/static/ticket-sessions.js", "/plugins/tasks/static/tasks-boot.js"}
    html = web.text("/agents")
    assert "<title>Agents</title>" in html and "/plugins/agents/static/agents.js" in html
    head = web.text("/").split("</head>")[0]                # each plugin's look, linked on every page
    assert "/plugins/agents/static/agents.css" in head and "/plugins/tasks/static/tasks.css" in head
    org = web.get(f"{AGENTS}/org")
    assert [a["name"] for a in org["agents"]] == ["supervisor", "coordinator", "worker", "reviewer"]   # serves worker and reviewer
    assert [t["name"] for t in org["teams"]] == ["engineering"]
    assert web.get(f"{AGENTS}/state")["probes"] == []

    plan(target)
    wait_until("the tickets", lambda: settled(web), timeout_s=300)
    seen = {}

    def observed() -> bool:
        d = web.get("/api/machines")
        if not d.get("ok"):
            return False
        seen.update({m["id"]: m for m in d["machines"]})
        return {"supervisor", "coordinator", "reviewer", "worker"} <= set(seen) \
            and bool({"review.approved", "changes.requested"} & {p["type"] for p in seen["reviewer"]["publishes"]})   # a review ended
    wait_until("the machines", observed, timeout_s=120)
    assert "agents" not in seen                                      # the agents' host is no machine: the agents it runs are
    assert {"gate.landed", "ticket.created", "request.done"} <= set(seen["supervisor"]["subscribes"])   # what the host watches for it
    for name in ("supervisor", "coordinator"):                                              # resident: continuous, whatever wakes them
        m = seen[name]
        # the coordinator is also the task system's dispatch, an instance of it (a program): one machine, both kinds
        kind, instances = ("agent ×1, program ×1", ["dispatch"]) if name == "coordinator" else ("agent", [])
        assert m["package"] == "agents" and m["mode"] == "continuous" and m["kind"] == kind and not m.get("unseen")
        model = "sonnet" if name == "supervisor" else "haiku"           # the Supervisor runs on its machine spec's model (D20)
        assert m["about"]["harness"] == "claude_code" and m["about"]["model"] == model and [i["id"] for i in m["instances"]] == instances
    reviewer = seen["reviewer"]
    assert reviewer["package"] == "agents" and reviewer["mode"] == "ad hoc" and reviewer["kind"] == "agent"
    announced = [e["source"] for e in all_events(web) if e["type"] == "orchestra.machine.started" and e["source"].startswith("reviewer/")]
    assert len(announced) == len(set(announced)), "an instance announces itself once in the host's lifetime, not per review"
    assert {i["id"] for i in reviewer["instances"]} == {a.partition("/")[2] for a in announced} and {"task.accepted", "task.working"} <= {p["type"] for p in reviewer["publishes"]} \
        and {"review.approved", "changes.requested"} & {p["type"] for p in reviewer["publishes"]}
    # the worker role has two kinds of machine: the program (instance shell, started by `orchestra run`
    # from [[machines]]) and the agents' AI workers (1, 2); one machine, worker, with their instances
    worker = seen["worker"]
    assert "shell" in [i["id"] for i in worker["instances"]] or "shell" in (worker.get("logs") or []), worker
    started = {e["source"]: e["data"] for e in all_events(web) if e["type"] == "orchestra.machine.started"}
    assert started["worker/shell"]["kind"] == "program" and "task.assigned" in started["worker/shell"]["listens_for"]
    assert "review.requested" in reviewer["subscribes"] and "changes.requested" in worker["subscribes"]
    assert "task_scheduler" not in seen                               # the task system's scheduler is no machine
    assert seen["planner"]["mode"] == "ad hoc" and seen["planner"]["kind"] == "program"
