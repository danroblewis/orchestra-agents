"""The Supervisor's ``design`` tools (``docs/proposals/machine-design.md`` §2, orchestra_2): it
declares stocks, effects, targets and machines, finds ways to measure what was declared and starts
recording at once, drafts and refines machines while the owner talks, and records each machine's
expected effect. Orchestra's Dynamics and Machines pages draw the result once Orchestra keeps the
declared layer itself (``intended_api.drawn``); the owner never writes a file.

Every tool here:

- is the Supervisor's alone (checked here, like ``setup``);
- takes a ``reason`` (refused without one, in a sentence) and records provenance itself: the turn
  and the owner's message that began it (the host keeps them, ``host._turn_began``), and an
  optional ``quote`` that must be the owner's words in that message (``quote_in``), so "from your
  message: …" is never invented. A call with a verified quote is the owner's (``author: owner``);
  one answering the owner's page click carries its ``request`` id (``check_request``: the request
  must be one the declared layer noted and holds open, and name the same control, element and
  change; the layer counts it answered once an op verified it, so it backs one call only), and
  stays the Supervisor's answer to it (``author: supervisor``); any other is the Supervisor's
  proposal. ``via`` and ``at`` are never sent: the
  store sets them. The owner's message is kept whole (or, past Orchestra's limit, the part around
  the quote), with anything that looks like a secret hidden (``secret_shapes.redact``);
- writes only through the declared layer (``intended_api.layer``) and ``project.config_edit``
  (``config_change``); calls no model;
- answers with the ids recorded, where they show when a page draws them (``page_line``), and
  where they are kept (``kept_in``).

The tools by side: ``design_dynamics.py`` (stocks, effects, targets, marks, links, measurement
options), ``design_clearance.py`` (whether an option needs the owner's approval),
``design_instruments.py`` (the instrument written and recorded, kept or expired),
``design_machines.py`` (machines, events, expected effects, the draft implementation),
``design_confirm.py`` (confirm and discard, the owner's page requests), ``design_passes.py`` (the
refinement passes)."""

from __future__ import annotations

import re
from typing import Any

from . import intended_api
from .config import Project
from .secret_shapes import redact
from .store import AgentStore, StoreError

MACHINE = "supervisor"
REQUESTED = "orchestra.intent.requested"
OWNER = "owner"
MIN_QUOTE_WORDS, MIN_QUOTE_CHARS = 3, 12      # shorter than both matches almost any message (unless it is the whole message)
MESSAGE_LIMIT = 4000                           # Orchestra's limit on a provenance text


def plural(n: int, word: str, many: str | None = None) -> str:
    """``1 edit``, ``2 edits`` (``many`` for an irregular plural)."""
    return f"{n} {word if n == 1 else many or word + 's'}"


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().casefold()


def _part_at(msg: str, part: str, at: int) -> re.Match | None:
    """``part`` in ``msg`` from ``at``, on word boundaries where its ends are word characters."""
    pre = r"(?<!\w)" if re.match(r"\w", part) else ""
    post = r"(?!\w)" if re.search(r"\w$", part) else ""
    return re.compile(pre + re.escape(part) + post).search(msg, at)


def quote_in(quote: str, message: str) -> bool:
    """Whether ``quote`` is the owner's words: each part between ellipses ("…" or "...") is in the
    message, in order, on word boundaries, ignoring case, spacing and curly quotes. Refused: no
    message, no part, or parts too short to tell (fewer than three words and twelve characters),
    unless the quote is the owner's whole message ("Confirm it.")."""
    msg = _norm(message or "")
    parts = [_norm(p) for p in re.split(r"…|\.\.\.", quote or "")]
    parts = [p for p in parts if p]
    if not msg or not parts:
        return False
    at = 0
    for part in parts:
        m = _part_at(msg, part, at)
        if m is None:
            return False
        at = m.end()
    joined = " ".join(parts)
    whole = joined.strip(" .!?,;:") == msg.strip(" .!?,;:")
    return whole or len(joined.split()) >= MIN_QUOTE_WORDS or len(joined) >= MIN_QUOTE_CHARS


def turn_of(store: AgentStore) -> dict:
    return store.agent_state(MACHINE).get("turn") or {}


def recent_events(store: AgentStore, n: int = 5000) -> list[dict]:
    """The last ``n`` events on the bus, oldest first."""
    end = store.events.end()
    return store.events.read(max(0, end - n), limit=n)[0]


def check_request(project: Project, store: AgentStore, prov: dict, control: str, part: str, eid: str,
                  change: str | None = None) -> None:
    """The owner's backing of one call: their quote, or the request ``prov`` names, which must be
    one the declared layer noted from the owner's page and holds open (its row carries the control,
    the element and the change asked), for this ``control`` on ``part:eid`` (and this pending
    ``change``). Refused otherwise; the layer counts a request answered once an op verified it."""
    rid = prov.get("request")
    if not rid:
        if prov.get("author") == OWNER:
            return
        raise StoreError(f"{control} is the owner's to ask: quote their words (quote) or pass the request id of their click "
                         "(request)")
    row = intended_api.layer(project).open_requests().get(rid)
    if row is None:
        raise StoreError(f"request {rid} is not one the declared layer noted from the owner's page and holds open (answered "
                         "already, or never noted; read_design lists the open requests): an event on the bus alone backs nothing")
    asked = {"control": row.get("control"), "element": f"{row.get('part')}:{row.get('id')}", "change": row.get("change") or None}
    given = {"control": control, "element": f"{part}:{eid}", "change": change or None}
    if wrong := [k for k in asked if asked[k] != given[k]]:
        raise StoreError(f"request {rid} asked {asked['control']} on {asked['element']}"
                         + (f" (change {asked['change']})" if asked["change"] else "")
                         + f", not {control} on {part}:{eid}" + (f" (change {change})" if change else "")
                         + f": it does not back this call ({', '.join(wrong)} differ)")


def provenance(store: AgentStore, a: dict) -> dict:
    """The call's provenance as the caller gives it to the store: never ``via`` or ``at``."""
    reason = (a.get("reason") or "").strip()
    if len(reason) < 10:
        raise StoreError("give the reason in a sentence: it is recorded with the change and shown with it")
    turn = turn_of(store)
    message = turn.get("message") or ""
    quote = (a.get("quote") or "").strip()
    if quote and not message:
        raise StoreError("this turn has no message from the owner (a wake-up), so nothing can be quoted: leave quote out, "
                         "or pass the request id of their click")
    if quote and not quote_in(quote, message):
        raise StoreError(f"the quote {quote[:80]!r} is not the owner's words in their message of this turn (quote at least "
                         "three words exactly, or their whole message), or leave quote out (the change is then yours, a "
                         "proposal)")
    rid = (a.get("request") or "").strip()
    prov = {"author": OWNER if quote else MACHINE, "source": MACHINE, "turn": turn.get("id"),
            "message": kept_message(message, quote) or None, "quote": redact(quote) or None, "reason": reason,
            "request": rid or None}
    return {k: v for k, v in prov.items() if v is not None}


def kept_message(message: str, quote: str = "") -> str:
    """The owner's message as provenance keeps it: whole, or past Orchestra's limit the part that
    holds the quote's first words; anything that looks like a secret hidden."""
    text = redact(message or "")
    if len(text) <= MESSAGE_LIMIT:
        return text
    words = re.split(r"…|\.\.\.", redact(quote or ""))[0].split()[:4]
    m = re.search(r"\s+".join(map(re.escape, words)), text, re.IGNORECASE) if words else None
    room = MESSAGE_LIMIT - 2
    start = max(0, min(m.start() - room // 4, len(text) - room)) if m else 0
    return ("…" if start else "") + text[start:start + room] + ("…" if start + room < len(text) else "")


def apply(project: Project, store: AgentStore, ops: list[dict], prov: dict) -> dict:
    return intended_api.layer(project).apply(ops, prov)


def page_line(project: Project, drawn: str, page: str | None = None) -> str | None:
    """Where the owner sees it, when the installed Orchestra has the page that draws it (``page``:
    Machines for a machine, Dynamics for a stock, effect or flow; read from ``drawn`` when not
    given); otherwise nothing (where it is kept is the answer's ``kept_in``)."""
    page = page or ("Machines" if "Machines page" in drawn else "Dynamics")
    return drawn if intended_api.drawn(project, page) else None


def answer(project: Project, done: dict, lines: list[str | None], **extra: Any) -> dict:
    ids = [c["change"] for c in done.get("changes") or []]
    return {"ok": True, "version": done.get("version"), "changes": ids, "said": [x for x in lines if x],
            "kept_in": intended_api.where(project), **extra}


def _modules():
    from . import (
        design_confirm,
        design_dynamics,
        design_instruments,
        design_machines,
    )
    return design_dynamics, design_instruments, design_machines, design_confirm


def handle(project: Project, store: AgentStore, command: str, a: dict, role: str) -> dict:
    for mod in _modules():
        if command in mod.COMMANDS:
            if command in getattr(mod, "READS", ()):              # read_design: any role with `observe`
                with intended_api.one_call():
                    return mod.COMMANDS[command](project, store, a)
            if role != MACHINE:
                raise StoreError(f"refused {command}: designing the system is the Supervisor's alone (the caller is "
                                 f"{role or 'none'!r})")
            project.reload_config()
            with intended_api.one_call():
                return mod.COMMANDS[command](project, store, a, provenance(store, a))
    raise StoreError(f"unknown design command {command!r}")


def commands() -> frozenset[str]:
    return frozenset().union(*(m.COMMANDS for m in _modules()))
