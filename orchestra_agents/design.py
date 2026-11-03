"""The Supervisor's ``design`` tools (``docs/proposals/machine-design.md`` §2, orchestra_2): it
declares stocks, effects, targets and machines, finds ways to measure what was declared and starts
recording at once, drafts and refines machines while the owner talks, and records each machine's
expected effect. Orchestra's Dynamics and Machines pages draw the result; the owner never writes a
file.

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
- answers with the ids recorded, the page that draws them, and where they are kept (``kept_in``).

The tools by side: ``design_dynamics.py`` (stocks, effects, targets, marks, links, measurement
options), ``design_clearance.py`` (whether an option needs the owner's approval),
``design_instruments.py`` (the instrument written and recorded, kept or expired),
``design_machines.py`` (machines, events, expected effects, the draft implementation),
``design_confirm.py`` (confirm and discard, the owner's page requests), ``design_names.py`` (the
owner's own name for a discovered stock, flow or effect), ``design_subsystems.py`` (the owner's
subsystems in words: a label's value named, parts gathered, one dropped), ``design_passes.py`` (the
refinement passes), ``design_integrate.py`` (connecting an outside system: Orchestra's
``docs/INTEGRATING.md`` as a flow, on the bridges plugin's kits)."""

from __future__ import annotations

import re
import time
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


SESSION_SAID = 30          # the owner's messages kept for the session: a request made a few turns back still counts


def begin_turn(store: AgentStore, message: str | None, n: int | None = None) -> dict:
    """A Supervisor turn begins (host._turn_began): its id and the owner's message (None for a
    wake-up), and the message kept with the session's earlier ones (``said``, cleared when the
    session is reset), so a machine the owner asked for a few turns back is still asked for and
    their words there can still be quoted."""
    st = store.agent_state(MACHINE)
    n = n if n is not None else int((st.get("turn") or {}).get("n") or 0) + 1
    turn = {"n": n, "id": f"sessions/supervisor/{n}", "message": message, "at": time.time()}
    said = list(st.get("said") or [])
    if message:
        said = [*said, {"turn": turn["id"], "message": message}][-SESSION_SAID:]
    store.set_agent_state(MACHINE, turn=turn, said=said)
    return turn


def said_in_session(store: AgentStore) -> list[dict]:
    """The owner's messages of this session, oldest first: ``{"turn", "message"}`` each."""
    return list(store.agent_state(MACHINE).get("said") or [])


def quoted_message(store: AgentStore, quote: str, message: str) -> str | None:
    """The owner's message ``quote`` is from: this turn's, else the latest earlier one of the session
    that holds it; None when none does."""
    if quote_in(quote, message):
        return message
    for row in reversed(said_in_session(store)):
        if quote_in(quote, row.get("message") or ""):
            return row["message"]
    return None


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
    quoted = quoted_message(store, quote, message) if quote else message
    if quote and quoted is None:
        raise StoreError(f"the quote {quote[:80]!r} is not the owner's words in any of their messages in this session (quote "
                         "at least three words exactly, or a whole message), or leave quote out (the change is then yours, a "
                         "proposal)")
    rid = (a.get("request") or "").strip()
    prov = {"author": OWNER if quote else MACHINE, "source": MACHINE, "turn": turn.get("id"),
            "message": kept_message(quoted or "", quote) or None, "quote": redact(quote) or None, "reason": reason,
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


def apply(project: Project, ops: list[dict], prov: dict) -> dict:
    return intended_api.layer(project).apply(ops, prov)


def answer(project: Project, done: dict, lines: list[str | None], **extra: Any) -> dict:
    ids = [c["change"] for c in done.get("changes") or []]
    return {"ok": True, "version": done.get("version"), "changes": ids, "said": [x for x in lines if x],
            "kept_in": intended_api.where(project), **extra}


def _modules():
    from . import (
        design_confirm,
        design_dynamics,
        design_instruments,
        design_integrate,
        design_machines,
        design_names,
        design_passthrough,
        design_subsystems,
        machine_graduate,
    )
    return (design_dynamics, design_instruments, design_machines, design_confirm, design_integrate, design_passthrough,
            design_names, design_subsystems, machine_graduate)


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
