"""Pi's turns cost what Pi's own usage records say, at the provider's prices (the live run,
2026-11-04: $0.00 recorded for Pi, $2.77 spent): read from a recorded Pi session file, each record
once, a compaction's call included; a model not priced here leaves the cost unknown, and then the
dollar caps refuse to start a turn and say so."""

import shutil
from pathlib import Path

from orchestra_agents import budget
from orchestra_agents.config import Config
from orchestra_agents.harness.dummy import DummyHarness
from orchestra_agents.harness.pi import PiHarness
from orchestra_agents.harness.pi_cost import Meter, price, usage_cost

FIXTURE = Path(__file__).parent / "fixtures" / "pi_session_usage.jsonl"
# claude-sonnet-5 at $2 in, $10 out, $0.20 cache read, $2.50 cache write per million tokens:
TURN = (12_000 * 2 + 800 * 10 + 40_000 * 0.2 + 5_000 * 2.5) / 1e6          # the assistant message: $0.0525
COMPACTION = (30_000 * 2 + 2_000 * 10) / 1e6                               # its compaction's summary: $0.08


def test_a_recorded_pi_session_is_priced_each_record_once(tmp_path):
    f = tmp_path / "s.jsonl"
    shutil.copy(FIXTURE, f)
    m = Meter("claude-sonnet-5")
    usd, n = m.turn(f)
    assert n == 2 and usd == round(TURN + COMPACTION, 6)                      # Pi's own figure there was 0
    assert m.turn(f) == (None, 0)                                             # nothing new: nothing counted again
    with f.open("a") as w:
        w.write(f.read_text().splitlines()[3].replace('"id": "m2"', '"id": "m4"') + "\n")
    assert m.turn(f) == (round(TURN, 6), 1)                                   # the next turn's message alone


def test_prices_by_model_id_and_unknown_models():
    assert price("claude-sonnet-5") == (2.0, 10.0, 0.20, 2.50) == price("anthropic/claude-sonnet-5:high")
    assert price("claude-sonnet-5-5") == price("claude-sonnet-5-5-20261001") == (2.0, 10.0, 0.20, 2.50)
    assert price("gpt-5") is None and price(None) is None
    assert usage_cost({"input": 1_000_000}, "some-local-model") is None
    assert usage_cost({"input": 10, "cost": {"total": 1.5}}, "claude-sonnet-5") == 1.5   # Pi's figure, when higher



def test_one_record_of_a_model_not_priced_leaves_the_turn_s_cost_unknown(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(FIXTURE.read_text().replace('"model": "claude-sonnet-5", ', '"model": "some-local-model", '))
    assert Meter("some-local-model").turn(f) == (None, 2)


def test_the_dollar_caps_refuse_to_start_on_a_model_pi_cannot_price():
    cfg = Config.from_raw({"agents": {"harness": "dummy"}})
    pi = PiHarness()
    assert pi.priced("claude-sonnet-5") and not pi.priced("qwen-27b") and DummyHarness().priced(None)
    why = budget.unpriced(pi, "qwen-27b", cfg, chat=True)
    assert "qwen-27b" in why and "chat_usd_per_day" in why and "claude-sonnet-5" in why
    assert "review_usd_per_day" in budget.unpriced(pi, "qwen-27b", cfg)
    assert budget.unpriced(pi, "claude-sonnet-5", cfg) is None
    cfg.chat_usd_per_day = 0                                                  # no chat cap: nothing to hold
    assert budget.unpriced(pi, "qwen-27b", cfg, chat=True) is None


def test_a_pi_session_s_turn_reports_its_cost_from_the_session_file(tmp_path):
    """The fake Pi (tests/e2e/fake_pi.py) speaks Pi's RPC and records a turn's usage as Pi does,
    with Pi's own cost 0: the turn's cost is its tokens at claude-sonnet-5's prices."""
    import sys

    from orchestra_agents.harness.base import SessionSpec
    from orchestra_agents.harness.pi import PiSession
    fake = tmp_path / "pi"
    fake.write_text(f"#!{sys.executable}\n" + (Path(__file__).parents[1] / "e2e" / "fake_pi.py").read_text())
    fake.chmod(0o755)
    spec = SessionSpec(session_id="supervisor", session_dir=tmp_path / "s", cwd=tmp_path, system_prompt="x", role="supervisor",
                       tool_groups=(), env={"ORCHESTRA_ROLE": "supervisor"}, log_path=tmp_path / "log", model="claude-sonnet-5",
                       provider="anthropic", tools_via="bash")
    s = PiSession(spec, str(fake))
    try:
        s.open()
        assert s.prompt("hello", 30).cost_usd == round(TURN, 6)
        assert s.prompt("again", 30).cost_usd == round(TURN, 6)                  # each turn its own records only
    finally:
        s.close()
    s2 = PiSession(spec, str(fake))                                               # the same session opened again
    try:
        s2.open()
        assert s2.prompt("third", 30).cost_usd == round(TURN, 6)                 # the earlier turns are not counted again
    finally:
        s2.close()
