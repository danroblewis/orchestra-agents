"""Whether the owner's words confirm a machine (``confirm``, machine-design.md §2.4): bootstrap run 7's
Supervisor confirmed Jira, GitHub and Slack as machines on "Start tracking.", words about tracking
that never spoke of a machine.

A quote backs confirming machine ``<id>`` only when the owner's message it is from both says yes
(confirm, approve, accept, yes, ok, go ahead, do it) and says to what: the machine by its id, its
title or what the owner calls its system ("GitHub", "git hub"), or all of them ("all three",
"both", "each", "them"). "It", "this" or "that" with a yes confirms one machine in a turn: their
"Confirm it." answers one question about one machine, never three. Anything else is refused and
nothing is confirmed: the Supervisor asks in one sentence and confirms on the answer. A click on
the page (a request) is checked by ``design.check_request`` instead."""

from __future__ import annotations

import re

from .design import MACHINE, quoted_message, turn_of
from .store import AgentStore, StoreError

YES = re.compile(r"\b(?:confirm\w*|approv\w*|accept\w*|yes|yep|yeah|ok|okay|sure|agreed?|go ahead|do it|please do)\b", re.IGNORECASE)
ALL = re.compile(r"\b(?:all|both|each|every|everything|them|those|these)\b", re.IGNORECASE)
ONE = re.compile(r"\b(?:it|this|that)\b", re.IGNORECASE)
KEY = "confirmed_as_it"                      # the turn and the machine a bare "it" confirmed


def _words(mid: str, m: dict) -> set[str]:
    from .design_integrate import SAID_AS
    out = {mid, mid.replace("_", " ")}
    if m.get("title"):
        out.add(str(m["title"]))
    out |= set(SAID_AS.get(mid, ()))
    return {w.lower() for w in out if w and w.strip()}


def names(message: str, mid: str, m: dict) -> bool:
    text = (message or "").lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text) for w in _words(mid, m))


def check(store: AgentStore, prov: dict, mid: str, m: dict) -> None:
    """Refused unless the owner's words behind ``prov`` confirm machine ``mid`` (``m``, its declaration)."""
    if prov.get("request") or not prov.get("quote"):
        return                                           # a page request, or no quote: checked elsewhere
    turn = turn_of(store)
    message = quoted_message(store, prov["quote"], turn.get("message") or "") or prov.get("message") or ""
    said = message.strip()
    if YES.search(said) and (names(said, mid, m) or ALL.search(said)):
        return
    if YES.search(said) and ONE.search(said):
        got = store.agent_state(MACHINE).get(KEY) or {}
        if got.get("turn") != turn.get("id") or got.get("machine") in (None, mid):
            store.set_agent_state(MACHINE, **{KEY: {"turn": turn.get("id"), "machine": mid}})
            return
        raise StoreError(f"refused confirm, nothing confirmed: the owner's \"{said[:120]}\" confirms one machine "
                         f"({got['machine']}, confirmed on it already this turn), not {mid} as well: ask them to name each "
                         "machine, or all of them, and confirm on that answer")
    raise StoreError(f"refused confirm, nothing confirmed: the owner's words (\"{said[:120]}\") do not confirm machine {mid}: "
                     f"confirming a machine takes their yes naming it ({m.get('title') or mid}) or all of them (\"confirm all "
                     "three\"). Ask in one sentence (\"Shall I confirm the … machines?\") and confirm on their answer")
