"""A turn ends when the agent settles, when nothing has come from the harness for the
silence limit, or, only if the project set one, at the hard cap. A busy agent is never
cut off without a cap."""

import time
from pathlib import Path

from orchestra_agents.harness.base import SessionSpec, _Deadlines
from orchestra_agents.harness.pi import PiSession

FAKE_PI = Path(__file__).parent.parent / "e2e" / "fake_pi.py"     # FAKE_MODE: silent or chatty


def spec(tmp_path, sid):
    return SessionSpec(session_id=sid, session_dir=tmp_path / "s", cwd=tmp_path, system_prompt="", role="x", tool_groups=(),
                       env={"ORCHESTRA_REPO": str(tmp_path), "ORCHESTRA_ROLE": "x", "ORCHESTRA_TICKET": ""},
                       log_path=tmp_path / "log" / f"{sid}.log")


def test_deadlines():
    d = _Deadlines(None, None)
    assert d.expired() is None and d.wait() == 5.0                    # nothing can end it but the agent
    d = _Deadlines(0, 0.05)
    time.sleep(0.06)
    assert d.expired() == "silence"
    d.touch()
    assert d.expired() is None                                        # an event resets the silence clock
    d = _Deadlines(0.01, None)
    time.sleep(0.02)
    assert d.expired() == "limit"


def test_silent_agent_is_stopped_after_the_silence_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "silent")
    s = PiSession(spec(tmp_path, "quiet"), pi_binary=str(FAKE_PI))
    s.open()
    t0 = time.time()
    res = s.prompt("go", None, idle_timeout=1.0)
    assert res.timed_out and res.stopped_by == "silence" and time.time() - t0 < 5
    s.close()


def test_pi_compacts_on_request(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "silent")
    s = PiSession(spec(tmp_path, "big"), pi_binary=str(FAKE_PI))
    s.open()
    assert s.compact() == {"tokens_before": 150000, "tokens_after": 32000}
    s.close()


def test_busy_agent_is_only_stopped_by_a_hard_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "chatty")
    s = PiSession(spec(tmp_path, "busy"), pi_binary=str(FAKE_PI))
    s.open()
    res = s.prompt("go", 1.5, idle_timeout=0.8)                        # streaming every 0.2s: silence never trips
    assert res.timed_out and res.stopped_by == "limit"
    s.close()
