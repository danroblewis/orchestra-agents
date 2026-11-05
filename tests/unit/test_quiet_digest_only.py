"""A machine gone quiet is news for the next chat, never a Supervisor turn of its own (the owner,
2026-11-04: "The Supervisor keeps wasting tokens on this quiet reminder")."""

import subprocess
from types import SimpleNamespace

from conftest import agents_project

from orchestra_agents import notices
from orchestra_agents.store import AgentStore


def store(tmp_path, monkeypatch=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return AgentStore(agents_project(repo))


def test_a_quiet_notice_is_held_for_the_digest_and_never_released_as_a_wake(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    st = store(tmp_path)
    cfg = SimpleNamespace(attention_every=0.0)
    notices.hold(st, {"type": "orchestra.machine.quiet", "subject": "poller"}, "event orchestra.machine.quiet: poller", now=1.0)
    assert notices.release(st, cfg, None, False, False, now=10_000.0) == 0
    assert st.drain_wakeups(notices.MACHINE) == []
    assert "poller" in notices.take_digest(st)


def test_other_notices_still_wake_and_leave_the_quiet_ones_for_the_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    st = store(tmp_path)
    cfg = SimpleNamespace(attention_every=0.0)
    notices.hold(st, {"type": "orchestra.machine.quiet", "subject": "poller"}, "event orchestra.machine.quiet: poller", now=1.0)
    notices.hold(st, {"type": "attention.needed", "subject": "ABC-1"}, "event attention.needed: ABC-1", now=2.0)
    assert notices.release(st, cfg, None, False, False, now=10_000.0) == 1
    assert [n["type"] for n in notices.held(st)] == ["orchestra.machine.quiet"]


def test_a_review_round_that_finds_only_a_quiet_machine_wakes_nobody():
    from orchestra_agents import review
    from pathlib import Path
    src = Path(review.__file__).read_text()
    assert 'waking = [f for f in new if f.get("kind") != "quiet"]' in src and "if waking or proposal or design:" in src
