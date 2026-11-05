"""The owner's chat messages from the moment the host accepts one until the Supervisor's transcript
holds it (``runtime.json`` ``owner_messages``, through store.py), so the chat shows a message sent
and waiting, or being answered, across a refresh, a reopened dialog, another tab or a proxy that
cut the streamed reply (the owner, 2026-11-04: a message not yet in the transcript could not be
found). This is no second transcript (AGENTS.md rule 6): an entry is dropped once the session file
holds its message, and only one the transcript never took (the turn failed first, the host
stopped) is kept a while, marked so.

Each entry: ``id``, ``text`` (as sent), ``at`` (accepted), ``status`` (``waiting`` in the queue,
``answering``, ``answered``: the turn ended but the transcript does not show it yet, ``failed``)
and, once known, ``started_at``, ``ended_at``, ``error``, and ``behind`` when it was accepted while a wake-up
turn ran (it waits for that turn, never interrupts it; no new wake-up turn starts before it). The host writes; the pages read
(``pending``). Messages are answered in the order accepted (``Line``)."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from .session_turns import epoch, owner_words
from .store import AgentStore

ANSWERED_KEPT_S = 600       # an answered message the transcript does not show is kept this long
FAILED_KEPT = 10            # the newest failed ones kept, for the owner to send again
CLOCK_SLACK_S = 5           # a transcript time this far before the acceptance still counts


def _prune(entries: list[dict], now: float) -> None:
    failed = [e for e in entries if e["status"] == "failed"][:-FAILED_KEPT]
    entries[:] = [e for e in entries if e not in failed
                  and not (e["status"] == "answered" and now - (e.get("ended_at") or now) > ANSWERED_KEPT_S)]


def accept(store: AgentStore, text: str) -> dict:
    """The owner's message, recorded as waiting; the entry."""
    entry = {"id": uuid.uuid4().hex[:12], "text": text, "at": time.time(), "status": "waiting"}

    def fn(entries):
        _prune(entries, entry["at"])
        entries.append(entry)
    store.update_owner_messages(fn)
    return entry


def _set(store: AgentStore, mid: str, **fields) -> None:
    def fn(entries):
        for e in entries:
            if e["id"] == mid:
                e.update(fields)
    store.update_owner_messages(fn)


def answering(store: AgentStore, mid: str) -> None:
    _set(store, mid, status="answering", started_at=time.time())


def behind(store: AgentStore, mid: str, what: str) -> None:
    """A waiting message's place: behind the turn that runs (a wake-up's), said on the chat."""
    _set(store, mid, behind=what)


def settle(store: AgentStore, mid: str, msgs: list[dict], error: str | None = None) -> None:
    """The turn ended: the entry is dropped when the transcript holds the message, else kept as
    answered (the transcript may only be late) or failed, with the turn's error."""
    def fn(entries):
        now = time.time()
        gone = held(entries, msgs)
        for e in entries:
            if e["id"] == mid:
                e.update(status="failed" if error else "answered", ended_at=now, **({"error": error} if error else {}))
        entries[:] = [e for e in entries if e["id"] not in gone or e["status"] in ("waiting", "answering")]
        _prune(entries, now)
    store.update_owner_messages(fn)


def interrupted(store: AgentStore) -> None:
    """At a host's start: what an earlier host accepted and never finished is failed, said why."""
    def fn(entries):
        for e in entries:
            if e["status"] in ("waiting", "answering"):
                e.update(status="failed", ended_at=time.time(),
                         error="the agents' host stopped before the Supervisor answered it")
    store.update_owner_messages(fn)


def held(entries: list[dict], msgs: list[dict]) -> set[str]:
    """The ids of the entries whose message the transcript holds: a user message, at or after the
    acceptance, whose owner's words are the entry's text. Each transcript message counts for one
    entry, so the same words sent twice are two messages."""
    said = [(epoch(m.get("ts")), words.strip()) for m in msgs if m.get("role") == "user"
            and (split := owner_words(m.get("text") or "")) is not None and (words := split[1])]
    used: set[int] = set()
    out: set[str] = set()
    for e in sorted(entries, key=lambda x: x["at"]):
        for i, (ts, words) in enumerate(said):
            if i not in used and words == e["text"].strip() and (not ts or ts >= e["at"] - CLOCK_SLACK_S):
                used.add(i)
                out.add(e["id"])
                break
    return out


def pending(store: AgentStore, msgs: list[dict]) -> list[dict]:
    """The owner's messages the transcript does not hold, oldest first; a waiting one says its
    place in the queue (1: next)."""
    entries = store.runtime().get("owner_messages") or []
    gone = held(entries, msgs)
    out, place = [], 0
    for e in sorted(entries, key=lambda x: x["at"]):
        if e["id"] in gone:
            continue
        if e["status"] == "waiting":
            place += 1
            e = {**e, "place": place}
        out.append(e)
    return out


def marked(m: dict) -> dict:
    """A Supervisor transcript message as the chat shows it: a user message with the owner's words
    carries them (``owner``) and Orchestra's context in front of them (``context``)."""
    split = owner_words(m.get("text") or "") if m.get("role") == "user" else None
    return {**m, "owner": split[1], "context": split[0]} if split else m


class Line:
    """The owner's messages are answered in the order the host accepted them: each turn waits
    until the messages accepted before it have had theirs (a lock alone lets any waiter in)."""

    def __init__(self):
        self._cv = threading.Condition()
        self._ids: list[str] = []

    def waiting(self) -> bool:
        """A message of the owner's waits for its turn, or has it: no wake-up turn may start."""
        with self._cv:
            return bool(self._ids)

    @contextmanager
    def turn(self, mid: str) -> Iterator[None]:
        with self._cv:
            self._ids.append(mid)
            self._cv.wait_for(lambda: self._ids[0] == mid)
        try:
            yield
        finally:
            with self._cv:
                self._ids.remove(mid)
                self._cv.notify_all()
