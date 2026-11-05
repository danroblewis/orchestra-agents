"""One Claude Code reply written several times as it grows (its text, then its text and a tool call,
under one message id) is one message in the transcript, each block once (the owner, 2026-11-04)."""

import json

from orchestra_agents.harness.claude_code import ClaudeCodeHarness
from orchestra_agents.integrate_heard import reason_for


def line(mid, *blocks, ts="2026-11-04T10:00:00Z"):
    return json.dumps({"type": "assistant", "timestamp": ts, "message": {"id": mid, "content": list(blocks)}})


def test_a_reply_written_twice_is_one_message(tmp_path):
    text = {"type": "text", "text": "Let me check the poller."}
    call = {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join([line("m1", text), line("m1", text, call), line("m1", call),
                            line("m2", {"type": "text", "text": "Done."})]) + "\n")
    got = ClaudeCodeHarness.read_transcript(f)
    assert [(m["text"], len(m["tool_calls"])) for m in got] == [("Let me check the poller.", 1), ("Done.", 0)]
    assert not any(k.startswith("_") for m in got for k in m)



def test_a_join_reason_stays_within_the_layers_limit():
    many = [f"person.tracker.{'x' * 30}{n}" for n in range(500)]
    r = reason_for("tracker", many)
    assert len(r) <= 4000 and "500 actors" in r and "and 480 more" in r
