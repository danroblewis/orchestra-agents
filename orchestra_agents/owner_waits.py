"""What waits on the owner now, for ``read_events`` with ``since`` (``waiting_on_owner``), so "what
changed since yesterday?" names it (T2f s16: the Slack token the Machines card asks for and the
go-ahead the Supervisor itself asked for on the 09:00 schedule were left out):

- ``machines``: each machine whose announcement says it is disabled, with what it says it needs
  (``list_machines``' ``disabled``: the card's words, passed on as said);
- ``outbox``: each Outbox draft waiting for the owner's approval (``outbox_read.fetch``);
- ``questions``: what the Supervisor asked in its last messages to the human (its transcript's last
  turns, ``session_turns``), each with what the owner said next, so a question the next message
  does not answer is named as still waiting. Nothing here judges whether it was answered.

Each part that could not be read says so, never as nothing waiting. Nothing is written."""

from __future__ import annotations

import re

from . import attention, outbox_read
from .session_turns import HUMAN, current_turn, messages, split
from .store import AgentStore, StoreError

TURNS = 6                 # the Supervisor's last messages to the human looked at
PER_TURN = 3              # questions kept of one message
TEXT = 200
_SENTENCE = re.compile(r"[^.!?\n]*\?")


def _cut(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TEXT else text[:TEXT] + "…"


def questions_in(reply: str) -> list[str]:
    """The questions a reply asks: its sentences that end in a question mark, markdown stripped."""
    out = []
    for m in _SENTENCE.finditer(reply or ""):
        q = re.sub(r"[*_`#>]+", "", m.group(0)).strip(" -:")
        if len(q) > 3:
            out.append(_cut(q))
    return out[-PER_TURN:]


def owner_message(turn: dict) -> str:
    p = turn.get("prompt") or ""
    return _cut(p.split(HUMAN, 1)[1] if HUMAN in p else p)


def _supervisor_turns(store: AgentStore) -> list[tuple[dict, bool]]:
    """The Supervisor's turns across its sessions, oldest first, each with whether it is the one
    running now."""
    every = [s for s in store.sessions(include_archived=True)
             if "supervisor" in (s.get("kind"), (s.get("instance") or "").split("/")[0]) and s.get("session_file")]
    out: list[tuple[dict, bool]] = []
    for s in sorted(every, key=lambda x: x.get("started_at") or 0):
        turns = split(messages(s))
        live = current_turn(s, turns)
        out += [(t, n == live) for n, t in enumerate(turns)]
    out.sort(key=lambda x: x[0].get("started") or 0)
    return out


def questions(store: AgentStore) -> list[dict]:
    turns = _supervisor_turns(store)
    asked = []
    for i, (t, live) in enumerate(turns):
        if live or not attention.for_the_human(t):
            continue
        qs = questions_in(t["reply"])
        if not qs:
            continue
        nxt = next(((u, lv) for u, lv in turns[i + 1:] if u["kind"] == "chat"), None)
        said = (None if nxt is None else
                {"message": owner_message(nxt[0]), **({"this_turn": True} if nxt[1] else {})})
        asked.append({"asked_at": t.get("started"), "questions": qs, "owner_said_next": said})
    return asked[-TURNS:]


def machines(store: AgentStore) -> list[str] | str:
    from . import machines_read
    try:
        got = machines_read.list_machines(store)
    except Exception as e:  # noqa: BLE001 - a failed read is said so, never as nothing waiting
        return f"not read ({type(e).__name__})"
    return got["disabled"] if got.get("ok") else f"not read: {got.get('error')}"


def outbox(project) -> list[dict] | str:
    try:
        st = outbox_read.fetch(project)
    except StoreError as e:
        return "not installed" if "not installed" in str(e) else f"not read: {e}"
    if not st.get("ok", True):
        return f"not read: {st.get('error') or 'the Outbox state did not answer'}"
    return [outbox_read.row(d) for d in st.get("waiting") or []]


NOTE = ("what waits on the owner now: machines, each disabled machine and what its announcement says it needs from the "
        "owner; outbox, each draft waiting for the owner's approval on the Outbox page; questions, what you asked in your "
        "last messages to the owner, each with what the owner said next (this_turn: the message you are answering now): "
        "a question that message does not answer still waits on the owner. A part that says 'not read' was not read: "
        "say so, never that nothing waits")


def waiting_on_owner(project, store: AgentStore) -> dict:
    try:
        asked: list[dict] | str = questions(store)
    except Exception as e:  # noqa: BLE001 - a transcript that cannot be read is said so
        asked = f"not read ({type(e).__name__})"
    return {"machines": machines(store), "outbox": outbox(project), "questions": asked, "note": NOTE}
