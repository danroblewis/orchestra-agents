"""What the Supervisor wrote to the human that the human has not read: the banner on every page
(static/attention.js) and the chat it opens read this.

A message is a Supervisor turn's reply, as its transcript holds it, so what the banner announces
is what the chat shows. A chat turn's reply is always for the human. A turn an event woke (a
blocked ticket, a landing, a process Orchestra gave up on, a judged prediction) is for the human
when it says something: an empty reply or a bare acknowledgement ("Nothing to do.", "Noted.") is
the Supervisor's own business and calls nobody. What the Supervisor last said that its transcript
does not hold (a reply cut off before the harness wrote it, a transcript moved on) is still a
message, returned with ``in_chat`` false so the chat shows its text. A Supervisor turn that failed
(``session.error``: the harness could not run it, e.g. Claude Code without Orchestra's tools) is
the newest thing the human must read when nothing newer waits: ``kind`` "failed", with the reason."""

from __future__ import annotations

import re

from .session_turns import WOKEN, current_turn, epoch, messages, split

SUPERVISOR = "supervisor"
# A reply that tells the human nothing: an acknowledgement, or that there was nothing to do.
NOOP = re.compile(r"^(nothing( else| new)?( to (do|report|add)| for (you|the human)| needed)?|no (action|changes?|reply)( is)?( needed| required| taken)?"
                  r"|noted|acknowledged|ack|ok(ay)?|all good|no-?op)\b([^\n]{0,60})$", re.IGNORECASE)
ASKS = re.compile(r"\?|\byour?\b", re.IGNORECASE)     # what follows the acknowledgement asks or tells the human something


def quiet(text: str) -> bool:
    """A reply that says nothing to the human: short, an acknowledgement or "nothing to do", and
    nothing after it that asks them or speaks to them ("Noted. Which database do you want?" calls)."""
    m = NOOP.match(text.strip())
    return bool(m) and len(text.strip()) <= 120 and not ASKS.search(m.group(m.lastindex))


def for_the_human(turn: dict) -> bool:
    text = turn["reply"].strip()
    if not text:
        return False
    return turn["kind"] == "chat" or not quiet(text)


def reasons(prompt: str) -> list[str]:
    """Why a wake-up woke the Supervisor, as its prompt lists it."""
    if WOKEN not in prompt:
        return []
    block = prompt.split(WOKEN, 1)[1].split("\n## ", 1)[0]
    return [ln[2:].strip() for ln in block.splitlines() if ln.startswith("- ")][:5]


def unread(rt: dict) -> dict | None:
    """The Supervisor's newest message for the human since the human last read the chat
    (``supervisor_seen_at``), with how many there are, or the turn that failed after it; None
    when nothing waits."""
    s = rt["sessions"].get(SUPERVISOR) or {}
    seen = float(rt.get("supervisor_seen_at") or 0)
    said = _said(s, seen)
    failed = [m for m in s.get("markers") or [] if m.get("type") == "session.error" and epoch(m.get("ts")) > seen]
    if failed and (said is None or epoch(failed[-1].get("ts")) >= said["at"]):
        return {"text": f"Orchestra could not run the Supervisor's turn:\n\n{failed[-1].get('error') or 'the harness failed'}",
                "at": epoch(failed[-1].get("ts")), "index": None, "count": 1 + (said["count"] if said else 0),
                "kind": "failed", "woken_by": [], "in_chat": False}
    return said


def _said(s: dict, seen: float) -> dict | None:
    turns = split(messages(s))
    live = current_turn(s, turns)
    waiting = [t for n, t in enumerate(turns) if n != live and t["ended"] > seen and for_the_human(t)]
    if waiting:
        last = waiting[-1]
        return {"text": last["reply"], "at": last["ended"], "index": last["reply_index"], "count": len(waiting),
                "kind": last["kind"], "woken_by": reasons(last["prompt"]), "in_chat": True}
    text, settled = (s.get("last_text") or "").strip(), float(s.get("settled_at") or 0)
    if settled <= seen or not text or quiet(text):
        return None
    if any(t["reply"] and t["reply"][:200] in text for t in turns[-3:]):
        return None                                     # in the chat, and read or not for the human
    return {"text": text, "at": settled, "index": None, "count": 1, "kind": None, "woken_by": [], "in_chat": False}
