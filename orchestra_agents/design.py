"""The Supervisor's ``design`` tools (``docs/proposals/machine-design.md`` §2, orchestra_2): it
declares stocks, effects, targets and machines, finds ways to measure what was declared and starts
recording at once, drafts and refines machines while the owner talks, and records each machine's
expected effect. The pages draw the result (the Dynamics and Machines pages); the owner never
writes a file.

Every tool here:

- is the Supervisor's alone (checked here, like ``setup``);
- takes a ``reason`` (refused without one, in a sentence) and records provenance itself: the turn
  and the owner's message that began it (the host keeps them, ``host._supervisor_turn``), and an
  optional ``quote`` that must be in that message, so "from your message: …" is never invented.
  A call with a verified quote is the owner's (``author: owner``: Orchestra applies it at once); one
  answering the owner's page click carries its ``request`` id; any other is the Supervisor's
  proposal;
- writes only through the declared layer (``intended_api.layer``: Orchestra's ``project.intended``,
  or the stand-in until it exists) and ``project.config_edit`` (``config_change``); calls no model;
- answers with the ids recorded and where they show.

The tools by side: ``design_dynamics.py`` (stocks, effects, targets, marks, links, measurement
options), ``design_instruments.py`` (the instrument written and recorded, kept or expired),
``design_machines.py`` (machines, events, expected effects, the draft implementation),
``design_confirm.py`` (confirm and discard, the owner's page requests), ``design_passes.py`` (the
refinement passes)."""

from __future__ import annotations

import re
import time
from typing import Any

from . import intended_api
from .config import Project
from .store import AgentStore, StoreError

MACHINE = "supervisor"
REQUESTED = "orchestra.intent.requested"


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().casefold()


def quote_in(quote: str, message: str) -> bool:
    """Whether ``quote`` is the owner's words: each part between ellipses ("…" or "...") is in the
    message, in order, ignoring case, spacing and curly quotes."""
    msg, at = _norm(message), 0
    for part in [p for p in re.split(r"…|\.\.\.", quote) if p.strip()]:
        i = msg.find(_norm(part), at)
        if i < 0:
            return False
        at = i + len(_norm(part))
    return bool(quote.strip())


def turn_of(store: AgentStore) -> dict:
    return store.agent_state(MACHINE).get("turn") or {}


def owner_request(store: AgentStore, rid: str) -> dict:
    """The owner's page request ``rid`` (``orchestra.intent.requested`` from ``owner``), read back
    from the bus; refused when there is none."""
    end = store.events.end()
    evs, _ = store.events.read(max(0, end - 5000), limit=5000)
    ev = next((e for e in reversed(evs) if e.get("id") == rid), None)
    if ev is None or ev.get("type") != REQUESTED or str(ev.get("source") or "").split("/")[0] != "owner":
        raise StoreError(f"no request {rid!r} from the owner on the bus (orchestra.intent.requested, source owner)")
    return ev


def provenance(store: AgentStore, a: dict) -> dict:
    reason = (a.get("reason") or "").strip()
    if len(reason) < 10:
        raise StoreError("give the reason in a sentence: it is recorded with the change and shown on the page")
    turn = turn_of(store)
    message = turn.get("message") or ""
    quote = (a.get("quote") or "").strip()
    if quote and not quote_in(quote, message):
        raise StoreError(f"the quote {quote[:80]!r} is not in the owner's message of this turn; quote their words exactly, "
                         "or leave quote out (the change is then yours, a proposal)")
    rid = (a.get("request") or "").strip()
    if rid:
        owner_request(store, rid)
    return {"author": "owner" if quote else MACHINE, "via": "plugin", "source": MACHINE, "at": time.time(),
            "turn": turn.get("id"), "message": message[:500] or None, "quote": quote or None, "reason": reason,
            **({"request": rid} if rid else {})}


def apply(project: Project, store: AgentStore, ops: list[dict], prov: dict) -> dict:
    return intended_api.layer(project).apply(ops, prov)


def answer(project: Project, done: dict, lines: list[str], **extra: Any) -> dict:
    ids = [c["change"] for c in done.get("changes") or []]
    return {"ok": True, "version": done.get("version"), "changes": ids, "said": lines,
            "kept_in": intended_api.where(project), **extra}


def handle(project: Project, store: AgentStore, command: str, a: dict, role: str) -> dict:
    from . import design_confirm, design_dynamics, design_instruments, design_machines
    for mod in (design_dynamics, design_instruments, design_machines, design_confirm):
        if command in mod.COMMANDS:
            if command in getattr(mod, "READS", ()):              # read_design: any role with `observe`
                return mod.COMMANDS[command](project, store, a)
            if role != MACHINE:
                raise StoreError(f"refused {command}: designing the system is the Supervisor's alone (the caller is "
                                 f"{role or 'none'!r})")
            project.reload_config()
            return mod.COMMANDS[command](project, store, a, provenance(store, a))
    raise StoreError(f"unknown design command {command!r}")


def commands() -> frozenset[str]:
    from . import design_confirm, design_dynamics, design_instruments, design_machines
    return frozenset().union(*(m.COMMANDS for m in (design_dynamics, design_instruments, design_machines, design_confirm)))
