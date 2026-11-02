"""A Supervisor reply must not claim an action the Supervisor did not take. Its reply ends its
turn: nothing runs after it until the human writes or an event wakes it, so "I'll check with the
coordinator" is a promise nobody keeps, and "I've updated DEMO-6" in a turn with no tool call is
not true. The prompt says so (prompts/supervisor.md); this is the check behind it, on the reply.

It is a heuristic over the reply's sentences, kept narrow so it does not cost a turn for nothing:
- a promise of a later action ("I'll", "I will", "I'm going to", "Let me") counts unless the
  sentence ties it to something that will happen ("when", "once", "if", "after", "as soon as",
  "tell me", "let me know"): "tell me the city and I'll update DEMO-6" waits on the human, and
  "I'll tell you when it lands" is kept by the landing wake-up (landings.py); a promise right after
  a question to the human waits on their answer ("Which do you want? I'll set it up.": R3 e O10,
  one paid follow-up turn each at T2i s01, s14 and s15); and a promise to tell
  the human later ("I will tell you the plan") counts as kept in a turn that called a tool whose
  answer schedules that wake-up (``WAKES``: a hand-off, whose plan wakes it);
- a claim that something was done ("I've updated", "I asked") counts only in a turn that made no
  tool call at all.
What it finds gets one more turn (``follow_up``) to do it now or say it is not done."""

from __future__ import annotations

import re

_SENTENCES = re.compile(r"[^.!?\n]+[.!?]?")
_LATER = re.compile(r"\b(I'll|I will|I'm going to|I am going to|let me)\b", re.IGNORECASE)
_TIED = re.compile(r"\b(when|once|if|after|as soon as|tell me|let me know)\b", re.IGNORECASE)
_TELL = re.compile(r"\b(tell|let) you\b", re.IGNORECASE)
WAKES = ("hand_to_coordinator",)      # its answer wakes the Supervisor with the plan (work.planned), which it then tells
_DONE = re.compile(r"\bI(?:'ve| have)? (?:just )?(updated|created|cancelled|canceled|messaged|asked|told|notified|checked|filed|"
                   r"added|changed|set|raised|unblocked|resumed|paused|restarted|reset)\b", re.IGNORECASE)


def unkept(text: str, tools: list[str]) -> str | None:
    """The first sentence of ``text`` that claims an action not taken in this turn (whose tool
    calls were ``tools``, by name), or None."""
    woken, asked = any(t in WAKES for t in tools), False
    for m in _SENTENCES.finditer(text or ""):
        s = m.group(0).strip()
        if not s:
            continue
        if _LATER.search(s) and not _TIED.search(s) and not asked and not (woken and _TELL.search(s)):
            return s
        if not tools and _DONE.search(s):
            return s
        asked = s.endswith("?")
    return None


def follow_up(sentence: str) -> str:
    """The extra turn's prompt: do it now with a tool, or correct the reply."""
    return (f"[Orchestra: your reply says \"{sentence}\" Nothing runs after your reply until the human writes or an "
            "event wakes you, and this turn's tools did not do it. Do it now with your tools if it should be done "
            "(message_agent reaches the coordinator), or do not do it. Then write one or two lines for the human: what "
            "you did just now, or that it is not done. Do not repeat the rest of your reply.]")
