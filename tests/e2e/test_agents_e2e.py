"""End-to-end: the agents fill the task system's roles. The real task system (``tasking run``:
its scheduler, desk, git and gate) runs as a separate process and is reached only on the bus,
as any machine reaches it: the tickets are created with requests, their state is read with
queries, and the agents' host answers the assignments with a fake harness, in a thread of its
own, as a separate machine would.

Every test runs once per harness: the fake Pi (RPC protocol, tools via the extension's
`orchestra-agents api` path), the fake Claude Code (stream-json, tools via the real MCP server),
and the fake Codex (`exec --json`, tools via the same MCP server). The agents must behave
identically on all of them. What is checked is what the agents put on the bus for each ticket
(the protocol, from the instance that holds the assignment), their archived sessions and
transcripts, and the ticket's final state; the transitions in between are the task system's own
suite's."""

import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import agents_project
from tasking.config import init_project

from orchestra_agents.config import Project
from orchestra_agents.events import Events
from orchestra_agents.harness import read_transcript
from orchestra_agents.host import AgentHost
from orchestra_agents.protocol import Follower, TaskClient
from orchestra_agents.store import AgentStore

FAKES = {"pi": Path(__file__).parent / "fake_pi.py", "claude_code": Path(__file__).parent / "fake_claude.py",
         "codex": Path(__file__).parent / "fake_codex.py"}
BINARY_ENV = {"pi": "PI_BINARY", "claude_code": "CLAUDE_BINARY", "codex": "CODEX_BINARY"}
SETTLED = ("DONE", "BLOCKED", "CANCELLED")


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@dataclass
class Env:
    project: Project
    events: Events          # the test's own view of the bus, as a planner
    ask: TaskClient
    milestone: str
    repo: Path
    host: AgentHost         # running in a thread beside the task system, as a separate machine would

    def create(self, **fields) -> str:
        r = self.ask("create_ticket", {"milestone_id": self.milestone, **fields})
        assert r.get("ok"), r
        return r["ticket"]["id"]

    def state(self, ticket: str) -> str:
        r = self.ask("get_ticket", {"id": ticket})
        assert r.get("ok"), r
        return r["ticket"]["state"]

    def settle(self, *tickets: str, timeout: float = 120) -> None:
        """Until every ticket named has reached a settled state and the host has nothing left to
        do for what is on the bus (a ticket done: its sessions archived; a lead woken on it and
        answered)."""
        deadline = time.time() + timeout
        while time.time() < deadline and not all(self.state(t) in SETTLED for t in tickets):
            time.sleep(0.5)
        end = self.host.events.end()
        while time.time() < deadline and not self.host.idle(end):
            time.sleep(0.2)


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
    init_project(repo, prefix="E2E", unit_command="test -f README.md")      # the repository's orchestra.toml, as `tasking init` writes it
    toml = repo / "orchestra.toml"
    text = toml.read_text().replace('work = "tasks"', 'work = "code"')      # the code path: a branch per ticket, the gate
    assert '# tick = "3s"' in text
    toml.write_text(text.replace('# tick = "3s"', 'tick = "500ms"'))        # the scheduler looks at the queue often: the tests wait on it
    project = agents_project(repo, harness=harness)
    assert project.config.harness == harness and project.config.work == "code"
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "orchestra config", cwd=repo)
    monkeypatch.setenv("ORCHESTRA_REPO", str(repo))
    events = Events.for_project(project, source="planner")
    log = (tmp_path / "scheduler.log").open("ab")
    scheduler = subprocess.Popen([sys.executable, "-m", "tasking", "run"], cwd=repo, env=dict(os.environ), stdout=log, stderr=subprocess.STDOUT)
    ev = Events.for_project(project)
    host = AgentHost(project, AgentStore(project, ev), ev)       # up before any ticket exists: an offer is answered as it comes
    th = threading.Thread(target=host.run, daemon=True)
    th.start()
    try:
        started, deadline = Follower(events, start=0), time.time() + 30
        while not any(e["type"] == "orchestra.machine.started" and e["source"] == "task_scheduler" for e in started.new()):
            assert scheduler.poll() is None, "tasking run exited; see scheduler.log"
            assert time.time() < deadline, "the scheduler did not start"
            time.sleep(0.2)
        ask = TaskClient(events, "planner")
        ms = ask("create_milestone", {"name": "m"})
        assert ms.get("ok"), ms
        yield Env(project, events, ask, ms["milestone"]["id"], repo, host)
    finally:
        host.stop.set()
        th.join(60)
        scheduler.terminate()
        scheduler.wait(30)
        log.close()


def agent_store(env: Env) -> AgentStore:
    return AgentStore(env.project, Events.for_project(env.project))


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
    t = env.create(title="Do the thing", description="x", acceptance="y")
    env.settle(t)
    assert env.state(t) == "DONE"
    reports = check_protocol(env.events, t, WORK_READY, APPROVED)
    assert reports[1][2]["commit"] in git("log", "--format=%H", "main", cwd=env.repo)      # the reviewer named the commit it read
    astore = agent_store(env)
    assert {s["id"] for s in astore.sessions()} == {"supervisor", "coordinator"}
    archived = astore.runtime()["archived_sessions"]
    assert set(archived) == {f"worker-{t}", f"reviewer-{t}"}
    assert all(Path(a["session_file"]).exists() for a in archived.values())
    assert {a["harness"] for a in archived.values()} == {env.project.config.harness}
    assert [m["type"] for m in archived[f"worker-{t}"]["markers"]] == ["session.started"]


def test_rejection_loops_back_to_worker(env, monkeypatch):
    monkeypatch.setenv("FAKE_PI_REJECT_FIRST", "1")
    t = env.create(title="Needs two tries")
    env.settle(t)
    assert env.state(t) == "DONE"
    reports = check_protocol(env.events, t, WORK_READY, REJECTED, WORK_READY, APPROVED)
    assert reports[1][2]["report"]["feedback"]                                            # a rejection says why
    msgs = read_transcript(agent_store(env).runtime()["archived_sessions"][f"worker-{t}"])
    assert sum(1 for m in msgs if m["role"] == "user") == 2                              # the same worker session, re-engaged


def test_blocked_worker(env, monkeypatch):
    monkeypatch.setenv("FAKE_PI_WORKER_BLOCK", "1")
    t = env.create(title="Blocked one")
    env.settle(t)
    assert env.state(t) == "BLOCKED"
    check_protocol(env.events, t, ("worker", {"state": "BLOCKED", "blocked_reason": "need a decision"}))


def test_dependency_order(env):
    a = env.create(title="A", priority=5)
    b = env.create(title="B", priority=1, depends_on=[a])
    env.settle(a, b)
    assert env.state(a) == "DONE" and env.state(b) == "DONE"
    evs, _ = env.events.read(0, limit=10000)
    accepted = [e["ticket"] for e in evs if e["type"] == "task.accepted" and e["role"] == "worker"]
    assert accepted == [a, b]                                                             # B waited for A


def test_gate_rejection_reengages_worker_then_lands(env, monkeypatch):
    """The worker's first attempt passes review but fails the gate's tests; the task system sends
    it back; the same worker session is re-engaged in the same worktree, fixes it, is re-reviewed,
    and the ticket lands."""
    monkeypatch.setenv("FAKE_PI_BREAK_FIRST", "1")
    t = env.create(title="Breaks the build first")
    env.settle(t)
    assert env.state(t) == "DONE"
    check_protocol(env.events, t, WORK_READY, APPROVED, WORK_READY, APPROVED)
    archived = agent_store(env).runtime()["archived_sessions"]
    assert set(archived) == {f"worker-{t}", f"reviewer-{t}"}
    msgs = read_transcript(archived[f"worker-{t}"])
    assert sum(1 for m in msgs if m["role"] == "user") == 2                              # one session, re-engaged rather than replaced
    assert any(m["role"] == "assistant" and any(c["name"] == "report_ticket" for c in m["tool_calls"]) for m in msgs)


def test_team_lead_is_woken_in_its_space(env):
    """A custom team: the lead is a scheduled agent that also wakes on landings.
    When a ticket lands, the host wakes it in the team's space with a prompt made of its
    role, the team's responsibilities, and its access."""
    from orchestra_agents import tooling as api
    agents, astore = env.project, agent_store(env)
    api.handle(agents, astore, "create_role", {"name": "owner", "description": "Owns the backlog",
                                               "prompt": "# You own the backlog\n\n" + "Keep it full and honest. " * 4, "tools": ["tickets"]}, role="supervisor")
    api.handle(agents, astore, "create_agent", {"name": "po", "roles": ["owner"], "team": "product", "kind": "scheduled", "every": "1d",
                                                "wake_on": ["ticket.transitioned:DONE"]}, role="supervisor")
    api.handle(agents, astore, "create_team", {"name": "product", "lead": "po", "responsibilities": "# Product\n\n1. The backlog is never empty.",
                                               "access": [{"tools": ["events"]}]}, role="supervisor")
    astore.drain_wakeups("po")   # the creation introduction; this test is about the landing
    t = env.create(title="Do the thing")
    env.settle(t)
    assert env.state(t) == "DONE"
    sess = next(s for s in astore.sessions() if s["id"] == "po")
    texts = [m["text"] for m in read_transcript(sess) if m["role"] == "user"]
    assert any("event ticket.transitioned" in x and "to=DONE" in x for x in texts)
    assert (env.repo / ".orchestra" / "spaces" / "product" / "tools").is_dir()
    assert "The backlog is never empty" in (agents.logs_dir / "agents" / "po.prompt.md").read_text()   # what it was started with
    assert astore.agent_state("po").get("last_woken")
