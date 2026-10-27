"""A resident agent can be started over: its transcript is archived as a run, where its harness
leaves it, and the next process begins with an empty one."""

import json
import subprocess

import pytest
from conftest import FakeHarness, agents_project, archive

from orchestra_agents import tooling as api
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore, StoreError


class FakeProc:
    def __init__(self, sid, session_file=None):
        self.session_id, self._alive, self.session_file, self.harness_name = sid, True, session_file, "fake"
    def compact(self):
        return None
    def alive(self):
        return self._alive
    @property
    def pid(self):
        return 4242 if self._alive else None
    def close(self):
        self._alive = False
    def retire(self):
        return archive(self.session_file)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo, provider="p", model="m")


class CompactingProc(FakeProc):
    def compact(self):
        return {"tokens_before": 150000, "tokens_after": 32000}


def pi_call(tokens: int) -> str:
    """A Pi transcript's assistant message with its call's usage."""
    return json.dumps({"type": "message", "message": {"role": "assistant", "content": "ok", "usage": {"totalTokens": tokens}}}) + "\n"


def pi_compaction(before: int) -> str:
    return json.dumps({"type": "compaction", "summary": "s", "tokensBefore": before}) + "\n"


def session_with(project, sid, text):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    f = project.sessions_dir / f"2026_{sid}.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    store.set_session(sid, kind="worker", status="idle", session_file=str(f))
    return host, store, events, f


def compacted(events):
    return [e for e in events.read(0)[0] if e["type"] == "agent.compacted"]


def test_the_bound_is_the_contexts_tokens_not_the_transcripts_size(project):
    """A transcript of megabytes whose last call held few tokens is left alone (tool output and
    request records are not context); one whose last call held more than compact_tokens is
    compacted in place, once, and reported once."""
    limit = project.config.compact_tokens
    host, _store, events, f = session_with(project, "worker-T-1", "x" * 3_000_000 + "\n" + pi_call(20_000))
    proc = CompactingProc("worker-T-1")
    assert host.residents.trim_if_large(proc, "worker-T-1", "worker") is None and compacted(events) == []
    with f.open("a") as w:
        w.write(pi_call(limit + 1))
    assert host.residents.trim_if_large(proc, "worker-T-1", "worker") == "compacted"
    assert f.exists() and proc.alive()                                                       # transcript and process kept
    with f.open("a") as w:                                                                   # what the harness writes when it compacts
        w.write(pi_compaction(limit + 1))
    assert host.residents.trim_if_large(proc, "worker-T-1", "worker") is None                # not reported a second time
    [ev] = compacted(events)
    assert ev["tokens_before"] == 150000 and ev["tokens_after"] == 32000 and f"{limit + 1:,} tokens" in ev["reason"]


def test_a_compaction_the_harness_did_by_itself_is_reported_once(project):
    """Claude Code compacts on its own; its transcript's compact_boundary is the signal, and each
    one is reported exactly once. A large context it keeps itself is not rotated."""
    from orchestra_agents.harness.claude_code import ClaudeCodeHarness

    class AutoProc(FakeProc):
        def compact(self):
            return {"mode": "auto", "limit": 100000}

    def call(tokens):
        return json.dumps({"type": "assistant", "message": {"content": [], "usage": {"input_tokens": 2, "cache_read_input_tokens": tokens,
                                                                                      "cache_creation_input_tokens": 0, "output_tokens": 10}}}) + "\n"
    boundary = json.dumps({"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto", "preTokens": 167849,
                                                                                               "postTokens": 12334}}) + "\n"
    sub = json.dumps({"type": "assistant", "isSidechain": True, "message": {"usage": {"input_tokens": 900_000}}}) + "\n"
    host, _store, events, f = session_with(project, "worker-T-3", call(5000) * 40 + sub)
    host.harness = ClaudeCodeHarness()
    proc = AutoProc("worker-T-3")
    assert host.residents.trim_if_large(proc, "worker-T-3", "worker") is None and compacted(events) == []   # a subagent's context is not this one
    with f.open("a") as w:
        w.write(call(170_000))
    assert host.residents.trim_if_large(proc, "worker-T-3", "worker") is None and compacted(events) == []   # past the limit: the harness's to do
    with f.open("a") as w:
        w.write(boundary + call(12_000))
    assert host.residents.trim_if_large(proc, "worker-T-3", "worker") == "compacted"
    assert host.residents.trim_if_large(proc, "worker-T-3", "worker") is None
    [ev] = compacted(events)
    assert (ev["tokens_before"], ev["tokens_after"]) == (167849, 12334) and "by itself" in ev["reason"]
    assert f.exists() and proc.alive() and not [e for e in events.read(0)[0] if e["type"] == "agent.reset"]


def test_large_sessions_rotate_only_when_the_harness_cannot_compact(project):
    host, store, events, f = session_with(project, "worker-T-2", pi_call(project.config.compact_tokens + 1))
    assert host.residents.trim_if_large(FakeProc("worker-T-2", str(f)), "worker-T-2", "worker") == "rotated"   # base compact() returns None
    assert not f.exists() and (project.sessions_dir / "archive" / f.name).exists()
    assert store.find_session("worker-T-2")["session_file"] is None
    ev = [e for e in events.read(0)[0] if e["type"] == "agent.reset"][-1]
    assert ev["by"] == "orchestra" and "could not compact" in ev["reason"] and "tokens" in ev["reason"]


def test_reset_archives_the_transcript_and_starts_over(project):
    store, events = AgentStore(project), Events.for_project(project)
    f = project.sessions_dir / "2026_supervisor.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("old conversation\n")
    store.set_session("supervisor", kind="supervisor", status="idle", pid=4242, session_file=str(f), last_text="long ago")
    store.set_agent_state("supervisor", context_hash="abc", turn_depth=2)
    with pytest.raises(StoreError):
        api.handle(project, store, "reset_agent", {"name": "worker"}, role="supervisor")     # not resident
    api.handle(project, store, "reset_agent", {"name": "supervisor"}, role="supervisor")
    host = AgentHost(project, store, events)
    host.harness = FakeHarness()
    host.residents.procs["supervisor"] = proc = FakeProc("supervisor", str(f))
    host._refresh_org(store.runtime()["agent_state"])
    assert not proc.alive() and host.residents.procs["supervisor"] is not proc                  # closed, and started over
    live = store.find_session("supervisor")
    assert live["session_file"] != str(f) and live["last_text"] == "" and live["markers"][-1]["type"] == "agent.reset"   # fresh
    archived = [k for k in store.runtime()["archived_sessions"] if k.startswith("supervisor-reset-")]
    assert len(archived) == 1
    old = store.runtime()["archived_sessions"][archived[0]]
    assert old["last_text"] == "long ago" and old["session_file"].endswith("archive/2026_supervisor.jsonl")
    assert (project.sessions_dir / "archive" / "2026_supervisor.jsonl").read_text() == "old conversation\n" and not f.exists()
    st = store.agent_state("supervisor")
    assert not st.get("reset_requested") and st.get("context_hash") is None and st.get("turn_depth") == 0   # context is re-sent
    ev = next(e for e in events.read(0)[0] if e["type"] == "agent.reset")
    assert ev["archived"] == archived[0] and ev["by"] == "supervisor"
    runs = api.handle(project, store, "get_agent", {"name": "supervisor"})["runs"]
    assert archived[0] in [r["id"] for r in runs]                                              # the old conversation is still a run
