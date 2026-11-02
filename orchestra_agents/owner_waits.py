"""What waits on the owner now, for ``read_events`` with ``since`` (``waiting_on_owner``), so "what
changed since yesterday?" names it (T2f s16: the Slack token the Machines card asks for and the
go-ahead the Supervisor itself asked for on the 09:00 schedule were left out):

- ``machines``: each machine whose announcement says it is disabled, with what it says it needs
  (``list_machines``' ``disabled``: the card's words, passed on as said);
- ``outbox``: each Outbox draft waiting for the owner's approval (``outbox_read.fetch``);
- ``changes``: each pending change on a declared machine, waiting for the owner's confirm on the
  Machines page (the declared layer's ``pending``: its id and the fields it sets, adds or removes;
  T2g s16: the Review Nudger's zone, c8.0, was only hedged);
- ``questions``: each question the Supervisor asked the human that the owner has not answered
  since, across the session's turns (``session_turns``), with what the owner said next. A question
  is a sentence ending in a question mark or a choice list (a numbered list after a lead-in that
  offers a choice: "Your choices:", "pick one:"; T2h s10 asked which copy to write that way, with
  no question mark, and was left out). A later owner message answers it only when it names one of
  the offered choices (its quoted words, its first word, or its number) or shares a word of the
  question's subject; otherwise it stays open (T2h: "This machine's clock." and "Remind the
  reviewer on #38" left the copy question open). A question asked again (the same choices, or the
  same words) is kept once, as last asked.

Each part that could not be read says so, never as nothing waiting. Nothing is written."""

from __future__ import annotations

import re

from . import attention, outbox_read
from .session_turns import HUMAN, current_turn, messages, split
from .store import AgentStore, StoreError

TURNS = 6                 # open questions kept, the latest
PER_TURN = 3              # questions kept of one message
TEXT = 200
_SENTENCE = re.compile(r"[^.!?\n]*\?")
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.+)$")
_OFFER = re.compile(r"\b(choices?|choose|pick|options?|one of|either|prefer|which)\b", re.IGNORECASE)
_QUOTED = re.compile(r"[\"“]([^\"”]{3,})[\"”]")
_WORD = re.compile(r"[a-z0-9][a-z0-9'’_-]*")
_MARKUP = re.compile(r"[*_`#>]+")
STOP = frozenset([
    "about", "after", "again", "also", "before", "being", "cannot", "change", "could", "does", "done", "each",
    "either", "from", "have", "here", "into", "just", "like", "long", "make", "many", "more", "need", "needs",
    "only", "other", "over", "please", "shall", "should", "since", "some", "still", "such", "than", "that",
    "their", "them", "then", "there", "these", "they", "this", "those", "through", "until", "very", "want",
    "well", "were", "what", "when", "where", "which", "while", "will", "with", "would", "your", "yours",
    "you're", "it's", "i'll", "i'm", "let's", "say", "tell",
])


def _cut(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TEXT else text[:TEXT] + "…"


def _clean(text: str) -> str:
    return _MARKUP.sub("", text).strip(" -:")


def questions_in(reply: str) -> list[str]:
    """The questions a reply asks: its sentences that end in a question mark, markdown stripped."""
    out = []
    for m in _SENTENCE.finditer(reply or ""):
        q = _clean(m.group(0))
        if len(q) > 3:
            out.append(_cut(q))
    return out[-PER_TURN:]


def choice_lists(reply: str) -> list[dict]:
    """The choice lists a reply offers: a numbered list of two or more after a lead-in line that
    offers a choice, each as ``{"question": the lead-in, "choices": [...]}``."""
    lines, out, i = (reply or "").splitlines(), [], 0
    while i < len(lines):
        if not _NUMBERED.match(lines[i]):
            i += 1
            continue
        j, items = i, []
        while j < len(lines) and (m := _NUMBERED.match(lines[j])):
            items.append(_cut(_clean(m.group(2))))
            j += 1
        lead = next((_clean(x) for x in reversed(lines[:i]) if x.strip()), "")
        if len(items) >= 2 and _OFFER.search(lead):
            out.append({"question": _cut(lead), "choices": items})
        i = j
    return out


def words(text: str) -> set[str]:
    """The words of a text that can name a subject: four letters or more, not a common one."""
    return {w.strip("'’") for w in _WORD.findall((text or "").lower().replace("’", "'"))
            if len(w) >= 4 and w not in STOP}


def keys(choice: str) -> list[str]:
    """What names a choice: its quoted words when it has them, else its first word."""
    quoted = [q.lower() for q in _QUOTED.findall(choice)]
    first = _WORD.findall(choice.lower())[:1]
    return quoted or first


def answers(message: str, ask: dict) -> bool:
    """Whether an owner message answers an ask: it names one of its choices (quoted words, first
    word or number) or shares a word of its subject (the question's words, beyond the choices')."""
    said = " ".join(message.lower().split())
    for n, c in enumerate(ask.get("choices") or [], 1):
        if any(k and re.search(rf"\b{re.escape(k)}\b", said) for k in keys(c)):
            return True
        if re.fullmatch(rf"(option |choice |number )?#?{n}[.!]?", said):
            return True
    return bool(words(ask["question"]) & words(message))


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


def _same(a: dict, b: dict) -> bool:
    if a.get("choices") and b.get("choices"):
        return [keys(c) for c in a["choices"]] == [keys(c) for c in b["choices"]]
    return not a.get("choices") and not b.get("choices") and a["question"].lower() == b["question"].lower()


def open_questions(turns: list[tuple[dict, bool]]) -> list[dict]:
    """Every question asked in a turn for the human that no later owner message answers (``answers``),
    oldest first, one per question asked again; each with what the owner said next."""
    asked: list[dict] = []
    for i, (t, live) in enumerate(turns):
        if live or not attention.for_the_human(t):
            continue
        asks = [{"question": q} for q in questions_in(t["reply"])] + choice_lists(t["reply"])
        later = [(u, lv) for u, lv in turns[i + 1:] if u["kind"] == "chat"]
        left = [a for a in asks if not any(answers(owner_message(u), a) for u, _ in later)]
        if not left:
            continue
        nxt = later[0] if later else None
        said = (None if nxt is None else
                {"message": owner_message(nxt[0]), **({"this_turn": True} if nxt[1] else {})})
        for a in left:
            asked = [b for b in asked if not _same(a, b)]
            asked.append({"asked_at": t.get("started"), **a, "owner_said_next": said})
    return asked[-TURNS:]


def questions(store: AgentStore) -> list[dict]:
    return open_questions(_supervisor_turns(store))


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


def _field(k: str, v) -> str:
    if isinstance(v, dict):
        inner = ", ".join(f"{a} {b}" for a, b in v.items() if isinstance(b, str | int | float | bool))
        return f"{k} ({inner})" if inner else k
    return f"{k} {v}" if isinstance(v, str | int | float | bool) else k


def changes(project) -> list[dict] | str:
    from . import intended_api
    try:
        lay = intended_api.current(project)
    except Exception as e:  # noqa: BLE001 - a layer that cannot be read is said so, never as nothing waiting
        return f"not read ({type(e).__name__})"
    out = []
    for mid, m in sorted((lay.get("machines") or {}).items()):
        for p in (m or {}).get("pending") or []:
            what = [f"{op} {', '.join(_field(k, v) for k, v in sorted(p[op].items()))}"
                    for op in ("set", "add", "remove") if isinstance(p.get(op), dict) and p[op]]
            out.append({"machine": mid, "change": p.get("change"), "what": "; ".join(what) or "no fields"})
    return out


NOTE = ("what waits on the owner now: machines, each disabled machine and what its announcement says it needs from the "
        "owner; outbox, each draft waiting for the owner's approval on the Outbox page; changes, each pending change on a "
        "declared machine waiting for the owner's confirm; questions, each question you asked the "
        "owner (a question mark or a list of choices) that no later message of the owner's answers, with what the owner "
        "said next (this_turn: the message you are answering now): each still waits on the owner. A part that says 'not read' was not read: "
        "say so, never that nothing waits")


def waiting_on_owner(project, store: AgentStore) -> dict:
    try:
        asked: list[dict] | str = questions(store)
    except Exception as e:  # noqa: BLE001 - a transcript that cannot be read is said so
        asked = f"not read ({type(e).__name__})"
    return {"machines": machines(store), "outbox": outbox(project), "changes": changes(project), "questions": asked,
            "note": NOTE}
