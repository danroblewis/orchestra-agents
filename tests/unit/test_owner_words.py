"""The owner's words in a Supervisor prompt, told apart from Orchestra's context exactly (at the
seam, whatever the message holds), as every harness's reader gives the prompt back."""

import json
from datetime import UTC, datetime

import pytest

from orchestra_agents import prompting
from orchestra_agents.harness.codex import CodexHarness
from orchestra_agents.session_turns import owner_words

CONTEXT = "[Orchestra: now 2026-11-04 09:00 CET (UTC+01:00), the zone the pages show.]\n\n[Orchestra: 3 events since your last turn: x=3]\n\nsome notes\n"
MULTI = "First line of my request.\n\nSecond paragraph, after a blank line.\n\n\n- a list item\n- another\n\nLast line."


def test_a_multi_line_message_with_blank_lines_is_the_owners_whole():
    context, words = owner_words(prompting.chat_turn(CONTEXT, MULTI))
    assert words == MULTI                                         # every line, the blank ones too
    assert context.startswith("[Orchestra: now") and "some notes" in context and MULTI not in context


def test_a_message_that_looks_like_orchestras_stays_the_owners():
    msg = "Why did you say this?\n\n[Orchestra: ticket counts now: OPEN=2]\n\n[Orchestra: the notices above are for you]\n\nthanks"
    assert owner_words(prompting.chat_turn(CONTEXT, msg))[1] == msg
    seam = prompting.CHAT_SEAM
    assert owner_words(prompting.chat_turn(CONTEXT, "quoting:\n" + seam + "end"))[1] == "quoting:\n" + seam + "end"   # the first seam is Orchestra's
    # with no context, a message that begins like Orchestra's prompts is marked as the owner's
    alone = prompting.chat_turn("", "[Orchestra: is this yours?]\nline two")
    assert owner_words(alone) == ("", "[Orchestra: is this yours?]\nline two")


def test_no_context_at_all_is_the_message_itself():
    assert prompting.chat_turn("", MULTI) == MULTI
    assert owner_words(MULTI) == ("", MULTI)


def test_orchestras_own_prompts_have_no_owner_words_and_old_prompts_lose_nothing():
    assert owner_words(prompting.SUPERVISOR_WAKE + "# Wake-up for the supervisor\n\n## Why you were woken\n- x") is None
    assert owner_words("[Orchestra: escalation from worker/1]\nhelp") is None
    assert owner_words("[Orchestra: your reply says you will check; nothing runs after your reply]") is None
    # before the seam (2026-10-26): split only after the ticket-counts line, the multi-line message whole
    old = "[Orchestra: project context]\nnotes\n\n[Orchestra: ticket counts now: OPEN=1]\n" + MULTI
    assert owner_words(old)[1] == MULTI
    # an older shape with no such line is not split at all, so no line of the owner's is folded away
    assert owner_words("[Orchestra: project context]\nnotes\n\n" + MULTI) is None


def _harness_records(text: str) -> dict[str, list[dict]]:
    """The prompt as each harness writes it, read back by its own reader."""
    from orchestra_agents.harness.claude_code import ClaudeCodeHarness
    from orchestra_agents.harness.dummy import DummyHarness
    from orchestra_agents.harness.pi import PiHarness
    ts = datetime.now(UTC).isoformat()
    return {
        "claude": (ClaudeCodeHarness, [{"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}]),
        "pi": (PiHarness, [{"type": "message", "timestamp": ts, "message": {"role": "user", "content": [{"type": "text", "text": text}]}}]),
        "codex": (CodexHarness, [{"timestamp": ts, "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}]),
        "dummy": (DummyHarness, [{"role": "user", "text": text, "tool_calls": [], "ts": ts}]),
    }


@pytest.mark.parametrize("name", ["claude", "pi", "codex", "dummy"])
@pytest.mark.parametrize("message", [MULTI, "<b>bold</b> words, and a tag first", "<div>a tag first"])
def test_every_harness_gives_back_the_owners_words(tmp_path, name, message):
    cls, lines = _harness_records(prompting.chat_turn(CONTEXT, message))[name]
    f = tmp_path / f"{name}.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    users = [m for m in cls.read_transcript(f) if m["role"] == "user"]
    assert len(users) == 1 and owner_words(users[0]["text"])[1] == message
    # and the same message sent with no context, which a harness must not take for its own
    f.write_text("\n".join(json.dumps(x) for x in _harness_records("<div>a tag first")[name][1]) + "\n", encoding="utf-8")
    assert [m["text"] for m in cls.read_transcript(f) if m["role"] == "user"] == ["<div>a tag first"]
