"""A Supervisor reply checked against the facts Orchestra holds, after the turn, without the model.

Two facts the Supervisor has got wrong in a live test, in replies the human read:
- what it did: right after two commits of ``orchestra.toml`` it said it had not done anything.
  The record of its changes (ledger.py) says otherwise;
- where a ticket is: it called two tickets "in flight" that were DONE. The task system's snapshot,
  read after the reply, says otherwise.

The prompt already tells it to state only what a tool did and what a tool answered this turn; that
did not hold. So the host checks the reply against the record and the snapshot, and a reply that
contradicts them gets a correction appended, written by Orchestra, not the model: the changes the
record holds, or each ticket's true state. The checks are narrow on purpose (a sentence that says
nothing was done; a ticket named in a sentence that places it on the other side of done), so an
honest reply is never touched; a claim they miss is still answered by the facts the next turn's
context carries (``facts_line``)."""

from __future__ import annotations

import re

from .protocol import TERMINAL

_SENTENCES = re.compile(r"[^.!?\n]+[.!?]?")
_NOTHING = re.compile(
    r"\b(?:I(?:'ve| have)? (?:not|n't|haven't|have not|did not|didn't) (?:yet )?(?:done|changed|made|committed|created|touched|"
    r"do|change|make|commit|create) (?:anything|any changes?|any commits?)"
    r"|I(?:'ve| have)? (?:done|changed|made|committed) nothing"
    r"|I(?:'ve| have)? (?:made|done) no (?:changes?|commits?)"
    r"|nothing (?:has been|was|is|got) (?:done|changed|committed)"
    r"|no changes? (?:has|have|was|were) (?:been )?made"
    r"|I(?:'ve| have) not (?:yet )?(?:done|changed) anything)\b",
    re.IGNORECASE)
_LIVE = re.compile(r"\b(?:(?i:in flight|in progress|in review|under way|underway|queued|being (?:worked|reviewed|done)|still (?:open|running|"
                   r"going|waiting|pending|working)|not (?:yet )?(?:done|landed|merged|finished)|yet to (?:land|merge|finish))|"
                   r"OPEN|IN_PROGRESS|IN_REVIEW|READY_FOR_REVIEW|REJECTED|BLOCKED)\b")
_FINISHED = re.compile(r"\b(?:is|are|was|were|has|have|now) (?:already |now |also )?(?:DONE|done|landed|merged|finished|complete)\b")


def denies_doing(text: str) -> str | None:
    """The first sentence of ``text`` that says nothing was done, or None."""
    for m in _SENTENCES.finditer(text or ""):
        if _NOTHING.search(m.group(0)):
            return m.group(0).strip()
    return None


def ticket_ids(text: str, prefix: str) -> list[str]:
    """The ticket ids ``text`` names, in order, each once."""
    return list(dict.fromkeys(re.findall(rf"\b{re.escape(prefix)}-\d+\b", text or "")))


def states(snapshot: dict, ids: list[str]) -> dict[str, str]:
    """Each named ticket's state in the snapshot; nothing when the task system did not answer."""
    if snapshot.get("unavailable"):
        return {}
    known = {t.get("id"): t.get("state") for t in snapshot.get("tickets") or []}
    return {i: known[i] for i in ids if known.get(i)}


def misstated(text: str, now: dict[str, str]) -> dict[str, str]:
    """The tickets a sentence of ``text`` places on the wrong side of done: one that is DONE or
    CANCELLED called in flight, or one that is not called done. A sentence naming its true state
    is left alone. Returns id -> the true state."""
    wrong: dict[str, str] = {}
    for m in _SENTENCES.finditer(text or ""):
        s = m.group(0)
        for tid, state in now.items():
            if not re.search(rf"\b{re.escape(tid)}\b", s) or re.search(rf"\b{state}\b", s, re.IGNORECASE):
                continue
            live = bool(_LIVE.search(s))
            if live if state in TERMINAL else (_FINISHED.search(s) and not live):
                wrong[tid] = state
    return wrong


def correction(text: str, did: list[str], now: dict[str, str]) -> str | None:
    """Orchestra's correction to append to ``text``, or None when the reply agrees with the facts."""
    out = []
    said = denies_doing(text) if did else None
    if said:
        out.append(f"The reply says \"{said}\", but Orchestra's record shows these changes by the Supervisor in this turn:\n"
                   + "\n".join(f"- {d}" for d in did))
    wrong = misstated(text, now)
    if wrong:
        out.append("Where the reply places these tickets is not where they are; the task system says, now: "
                   + ", ".join(f"{t} is {s}" for t, s in wrong.items()) + ".")
    if not out:
        return None
    return "[Correction from Orchestra, from its records rather than the Supervisor's memory]\n" + "\n".join(out)


def facts_line(now: dict[str, str], did: list[str]) -> str:
    """What the next turn is told as fact: the tickets it is dealing with as they stand, read just
    now, and the changes its tools made since its previous turn began."""
    out = ""
    if did:
        out += ("[Orchestra: your tools' changes since your previous turn began, from Orchestra's record]\n"
                + "\n".join(f"- {d}" for d in did) + "\n")
    if now:
        out += ("[Orchestra: these tickets as the task system has them now; say a ticket's state from here or a tool's "
                "answer, never from memory: " + ", ".join(f"{t} {s}" for t, s in now.items()) + "]\n")
    return out
