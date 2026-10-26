"""End-to-end: the agents fill the task system's roles. The real task scheduler (orchestra-tasking:
its store, git, and gate) offers the work over the bus; the agents' host answers with a fake
harness, in a thread of its own, as a separate machine would.

Every test runs once per harness: the fake Pi (RPC protocol, tools via the extension's
`orchestra-agents api` path), the fake Claude Code (stream-json, tools via the real MCP server),
and the fake Codex (`exec --json`, tools via the same MCP server). The agents must behave
identically on all of them. What is checked is what the agents put on the bus for each ticket
(the protocol, from the instance that holds the assignment), their archived sessions and
transcripts, and the ticket's final state; the transitions in between are the task system's own
suite's."""

import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest
from conftest import agents_project
from tasking.config import init_project
from tasking.events import Events
from tasking.model import State
from tasking.scheduler import Scheduler
from tasking.store import LocalStore

from orchestra_agents.events import Events as AgentEvents
from orchestra_agents.harness import read_transcript
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore

FAKES = {"pi": Path(__file__).parent / "fake_pi.py", "claude_code": Path(__file__).parent / "fake_claude.py",
         "codex": Path(__file__).parent / "fake_codex.py"}
BINARY_ENV = {"pi": "PI_BINARY", "claude_code": "CLAUDE_BINARY", "codex": "CODEX_BINARY"}


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture(params=["pi", "claude_code", "codex"])
def env(tmp_path, monkeypatch, request):
    harness = request.param
    home = tmp_path / "home"
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    fake = tmp_path / harness
    shutil.copy(FAKES[harness], fake)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv(BINARY_ENV[harness], str(fake))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.delenv("FAKE_PI_WORKER_BLOCK", raising=False)
    monkeypatch.delenv("FAKE_PI_REJECT_FIRST", raising=False)
    monkeypatch.delenv("FAKE_PI_BREAK_FIRST", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "t@t", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("# e2e\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "init", cwd=repo)
    project = init_project(repo, prefix="E2E", unit_command="test -f README.md")
    toml = repo / "orchestra.toml"
    toml.write_text(toml.read_text().replace('work = "tasks"', 'work = "code"'))   # the code path: a branch per ticket, the gate
    agents = agents_project(repo, harness=harness)
    project.reload_config()
    assert agents.config.harness == harness and agents.config.work == "code"
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "orchestra config", cwd=repo)
    monkeypatch.setenv("ORCHESTRA_REPO", str(repo))
    events = Events.for_project(project, source="task_scheduler")
    store = LocalStore(project, events)
    ms = store.create_milestone(name="m")
    return project, store, events, ms, repo


def agents_of(project):
    a = agents_project(project.repo_root)
    return a, AgentStore(a, AgentEvents.for_project(a))


def run_loop(project, store, events):
    """The scheduler until its work is done, with the agents' host answering beside it. The
    scheduler's desk keeps answering queries while the host reads what was said last (a ticket
    done: its sessions archived; a lead woken and asking for a snapshot), as a running task
    system would; then both stop."""
    from tasking.requests import Desk
    a = agents_project(project.repo_root)
    ev = AgentEvents.for_project(a)
    host = AgentHost(a, AgentStore(a, ev), ev)
    th = threading.Thread(target=host.run, daemon=True)
    th.start()
    desk = None
    try:
        Scheduler(project, store, events).run(once=True)
        desk = Desk(project, store, events, lambda *_: None)
        desk.start()
        end, deadline = ev.end(), time.time() + 60
        while time.time() < deadline and not host.idle(end):
            time.sleep(0.2)
    finally:
        if desk is not None:
            desk.stop.set()
        host.stop.set()
        th.join(60)


def on_the_bus(events, ticket):
    """The agents' assignment events for one ticket, in order: (type, source), with each event."""
    evs, _ = events.read(0, limit=10000)
    return [(e["type"], e["source"], e) for e in evs if e.get("ticket") == ticket and e["type"] in ("task.accepted", "task.working", "task.reported")]


def check_protocol(events, ticket, *rounds):
    """Each round is (role, report shape): the instance accepted, reported from the same instance
    and session, with the report the role owes, and every heartbeat between carried its text (a
    turn shorter than the heartbeat interval sends none)."""
    seen = on_the_bus(events, ticket)
    reports = [x for x in seen if x[0] == "task.reported"]
    assert [e["role"] for _, _, e in reports] == [role for role, _ in rounds]
    for (role, shape), (_, source, rep) in zip(rounds, reports, strict=True):
        aid = rep["assignment"]
        mine = [(t, s, e) for t, s, e in seen if e["assignment"] == aid]
        assert mine[0][0] == "task.accepted" and mine[-1][0] == "task.reported"
        assert all(isinstance(e.get("text"), str) for t, _, e in mine if t == "task.working")
        accepted = mine[0][2]
        assert source == f"{role}/1" and accepted["instance"] == source and accepted["session"] == f"{role}-{ticket}"
        assert all(s == source for _, s, _ in mine)
        assert rep["failed"] is False and rep["report"]["kind"] == role
        for k, v in shape.items():
            assert rep["report"].get(k) == v, (k, rep["report"])
        if role == "reviewer" and rep.get("commit") is not None:
            assert len(rep["commit"]) >= 7
    return reports


WORK_READY = ("worker", {"state": "READY_FOR_REVIEW"})
APPROVED = ("reviewer", {"verdict": "APPROVED"})
REJECTED = ("reviewer", {"verdict": "REJECTED"})


def test_ticket_goes_open_to_done(env):
    project, store, events, ms, repo = env
    t = store.create_ticket(title="Do the thing", milestone_id=ms.id, description="x", acceptance="y")
    run_loop(project, store, events)
    assert store.get_ticket(t.id).state == State.DONE
    reports = check_protocol(events, t.id, WORK_READY, APPROVED)
    assert reports[1][2]["commit"] in git("log", "--format=%H", "main", cwd=repo)      # the reviewer named the commit it read
    _, astore = agents_of(project)
    assert {s["id"] for s in astore.sessions()} == {"supervisor", "coordinator"}
    archived = astore.runtime()["archived_sessions"]
    assert set(archived) == {f"worker-{t.id}", f"reviewer-{t.id}"}
    assert all(Path(a["session_file"]).exists() for a in archived.values())
    assert {a["harness"] for a in archived.values()} == {agents_of(project)[0].config.harness}
    assert [m["type"] for m in archived[f"worker-{t.id}"]["markers"]] == ["session.started"]


def test_rejection_loops_back_to_worker(env, monkeypatch):
    project, store, events, ms, _repo = env
    monkeypatch.setenv("FAKE_PI_REJECT_FIRST", "1")
    t = store.create_ticket(title="Needs two tries", milestone_id=ms.id)
    run_loop(project, store, events)
    assert store.get_ticket(t.id).state == State.DONE
    reports = check_protocol(events, t.id, WORK_READY, REJECTED, WORK_READY, APPROVED)
    assert reports[1][2]["report"]["feedback"]                                            # a rejection says why
    msgs = read_transcript(agents_of(project)[1].runtime()["archived_sessions"][f"worker-{t.id}"])
    assert sum(1 for m in msgs if m["role"] == "user") == 2                              # the same worker session, re-engaged


def test_blocked_worker(env, monkeypatch):
    project, store, events, ms, _repo = env
    monkeypatch.setenv("FAKE_PI_WORKER_BLOCK", "1")
    t = store.create_ticket(title="Blocked one", milestone_id=ms.id)
    run_loop(project, store, events)
    assert store.get_ticket(t.id).state == State.BLOCKED
    check_protocol(events, t.id, ("worker", {"state": "BLOCKED", "blocked_reason": "need a decision"}))


def test_dependency_order(env):
    project, store, events, ms, _repo = env
    a = store.create_ticket(title="A", milestone_id=ms.id, priority=5)
    b = store.create_ticket(title="B", milestone_id=ms.id, priority=1, depends_on=[a.id])
    run_loop(project, store, events)
    assert store.get_ticket(a.id).state == State.DONE and store.get_ticket(b.id).state == State.DONE
    evs, _ = events.read(0, limit=10000)
    accepted = [e["ticket"] for e in evs if e["type"] == "task.accepted" and e["role"] == "worker"]
    assert accepted == [a.id, b.id]                                                       # B waited for A


def test_gate_rejection_reengages_worker_then_lands(env, monkeypatch):
    """The worker's first attempt passes review but fails the gate's tests; the task system sends
    it back; the same worker session is re-engaged in the same worktree, fixes it, is re-reviewed,
    and the ticket lands."""
    project, store, events, ms, _repo = env
    monkeypatch.setenv("FAKE_PI_BREAK_FIRST", "1")
    t = store.create_ticket(title="Breaks the build first", milestone_id=ms.id)
    run_loop(project, store, events)
    assert store.get_ticket(t.id).state == State.DONE
    check_protocol(events, t.id, WORK_READY, APPROVED, WORK_READY, APPROVED)
    archived = agents_of(project)[1].runtime()["archived_sessions"]
    assert set(archived) == {f"worker-{t.id}", f"reviewer-{t.id}"}
    msgs = read_transcript(archived[f"worker-{t.id}"])
    assert sum(1 for m in msgs if m["role"] == "user") == 2                              # one session, re-engaged rather than replaced
    assert any(m["role"] == "assistant" and any(c["name"] == "report_ticket" for c in m["tool_calls"]) for m in msgs)


def test_team_lead_is_woken_in_its_space(env):
    """A custom team: the lead is a scheduled agent that also wakes on landings.
    When a ticket lands, the host wakes it in the team's space with a prompt made of its
    role, the team's responsibilities, and its access."""
    from orchestra_agents import tooling as api
    project, store, events, ms, _repo = env
    agents, astore = agents_of(project)
    api.handle(agents, astore, "create_role", {"name": "owner", "description": "Owns the backlog",
                                               "prompt": "# You own the backlog\n\n" + "Keep it full and honest. " * 4, "tools": ["tickets"]}, role="supervisor")
    api.handle(agents, astore, "create_agent", {"name": "po", "roles": ["owner"], "team": "product", "kind": "scheduled", "every": "1d",
                                                "wake_on": ["ticket.transitioned:DONE"]}, role="supervisor")
    api.handle(agents, astore, "create_team", {"name": "product", "lead": "po", "responsibilities": "# Product\n\n1. The backlog is never empty.",
                                               "access": [{"tools": ["events"]}]}, role="supervisor")
    astore.drain_wakeups("po")   # the creation introduction; this test is about the landing
    t = store.create_ticket(title="Do the thing", milestone_id=ms.id)
    run_loop(project, store, events)
    assert store.get_ticket(t.id).state == State.DONE
    sess = next(s for s in astore.sessions() if s["id"] == "po")
    texts = [m["text"] for m in read_transcript(sess) if m["role"] == "user"]
    assert any("event ticket.transitioned" in x and "to=DONE" in x for x in texts)
    assert (project.repo_root / ".orchestra" / "spaces" / "product" / "tools").is_dir()
    assert "The backlog is never empty" in (agents.logs_dir / "agents" / "po.prompt.md").read_text()   # what it was started with
    assert astore.agent_state("po").get("last_woken")
