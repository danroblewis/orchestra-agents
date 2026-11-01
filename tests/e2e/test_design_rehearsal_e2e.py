"""Goal task A3's scenario, every suite: the owner's design turns against the agents' demo, on the
fake Claude with each turn's tool calls scripted (the free rehearsal of
tests/live/test_design_live.py, ``ORCHESTRA_LIVE_DESIGN=rehearse``), judged by the same checks on
the core's API. One way of calling the tools (MCP), and a shorter warm-up before the turns (the
script links the stock to whatever discovery found)."""

from live.live_helpers import scenario

WARM_S = 180.0


def test_the_owner_designs_notes_files_and_the_notes_improver_rehearsed(tmp_path, monkeypatch):
    scenario("mcp", tmp_path, monkeypatch, rehearse=True, warm_s=WARM_S)
