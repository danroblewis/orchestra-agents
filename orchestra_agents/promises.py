"""A Supervisor reply must not claim an action the Supervisor did not take. Its reply ends its
turn: nothing runs after it until the human writes or an event wakes it, so "I'll check with the
coordinator" is a promise nobody keeps, and "I've updated DEMO-6" in a turn with no tool call is
not true. The prompt says so (prompts/supervisor.md); this is the check behind it, on the reply.

It is a heuristic over the reply's sentences, kept narrow so it does not cost a turn for nothing:
- a promise of a later action ("I'll", "I will", "I'm going to", "Let me") counts unless the
  sentence ties it to something that will happen ("when", "once", "if", "after", "as soon as",
  "tell me", "let me know"): "tell me the city and I'll update DEMO-6" waits on the human, and
  "I'll tell you when it lands" is kept by the landing wake-up (landings.py);
- a claim that something was done ("I've updated", "I asked") counts only in a turn that made no
  tool call at all.
What it finds gets one more turn (``follow_up``) to do it now or say it is not done."""

from __future__ import annotations

import re

_SENTENCES = re.compile(r"[^.!?\n]+[.!?]?")
_LATER = re.compile(r"\b(I'll|I will|I'm going to|I am going to|let me)\b", re.IGNORECASE)
_TIED = re.compile(r"\b(when|once|if|after|as soon as|tell me|let me know)\b", re.IGNORECASE)
_DONE = re.compile(r"\bI(?:'ve| have)? (?:just )?(updated|created|cancelled|canceled|messaged|asked|told|notified|checked|filed|"
                   r"added|changed|set|raised|unblocked|resumed|paused|restarted|reset)\b", re.IGNORECASE)


def unkept(text: str, tool_calls: int) -> str | None:
    """The first sentence of ``text`` that claims an action not taken in this turn, or None."""
    for m in _SENTENCES.finditer(text or ""):
        s = m.group(0).strip()
        if _LATER.search(s) and not _TIED.search(s):
            return s
        if tool_calls == 0 and _DONE.search(s):
            return s
    return None


def follow_up(sentence: str) -> str:
    """The extra turn's prompt: do it now with a tool, or correct the reply."""
    return (f"[Orchestra: your reply says \"{sentence}\" Nothing runs after your reply until the human writes or an "
            "event wakes you, and this turn's tools did not do it. Do it now with your tools if it should be done "
            "(message_agent reaches the coordinator), or do not do it. Then write one or two lines for the human: what "
            "you did just now, or that it is not done. Do not repeat the rest of your reply.]")
