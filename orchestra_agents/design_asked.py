"""How many machines the owner's message of this turn asks for, and how many the Supervisor
declared in it: ``declare_machine`` declares no more than were asked (the design prompt's
"declare only what they asked for in this message", held by the tool, not by the model).

A message asks for a machine by naming it ("a Universal Machine called Notes Improver", "agents
named Triage and Release Notes") or by asking for one without a name ("make an agent that triages
issues", "add two machines", "and a second one"). A loop described in passing ("a feedback loop where once per day an
agent runs") asks for none: the owner names the machine in a later message. The turn is the one
the host recorded (``design.turn_of``): its message, and the machines declared in it so far, which
the next turn's record clears. A wake-up has no message and no limit (the refinement passes
propose machines then); an external machine (``managed: external``, a system known by its events)
is not counted, since the owner's words may need its events without naming it."""

from __future__ import annotations

import re

from .design import MACHINE, turn_of
from .store import AgentStore, StoreError

MACHINE_WORDS = r"(?:machines?|agents?|programs?|bots?|workers?|jobs?)"
NAME = r"[\"“']?[A-Z][\w-]*(?:\s+[A-Z][\w-]*)*[\"”']?"
NAMED = re.compile(rf"\b(?:called|named)\s+({NAME}(?:(?:\s*,\s*|\s+and\s+|\s*,\s*and\s+){NAME})*)")
NUMBERS = {"a": 1, "an": 1, "one": 1, "another": 1, "two": 2, "three": 3, "four": 4, "five": 5, "both": 2}
ASKED = re.compile(rf"\b(?:create|make|add|declare|build|set\s+up|spin\s+up|write|define|design|draft|want|need)\s+"
                   rf"(?:me\s+)?(a|an|one|another|two|three|four|five|both)\s+(?:new\s+)?(?:[\w-]+\s+){{0,2}}?{MACHINE_WORDS}\b",
                   re.IGNORECASE)
ANOTHER = re.compile(rf"\b(?:(?:a|an)\s+(?:second|third|fourth|fifth|other)|another)\s+(?:new\s+)?(?:one|{MACHINE_WORDS})\b",
                     re.IGNORECASE)


def names_in(message: str) -> list[str]:
    """The machines' names the message gives after "called" or "named", in order."""
    out: list[str] = []
    for m in NAMED.finditer(message or ""):
        for part in re.split(r"\s*,\s*and\s+|\s*,\s*|\s+and\s+", m.group(1)):
            if name := part.strip(" \"“”'"):
                out.append(name)
    return list(dict.fromkeys(out))


def asked_for(message: str) -> int:
    """How many machines ``message`` asks for: the names it gives, or else the machines it asks
    for without one ("an agent", "two machines")."""
    named = len(names_in(message))
    unnamed = sum(NUMBERS[m.group(1).lower()] for m in ASKED.finditer(message or ""))
    unnamed = max(unnamed, len(ANOTHER.findall(message or "")))
    return max(named, unnamed)


def check(store: AgentStore, mid: str, managed: str) -> None:
    """Refuse a declaration past what this turn's message asked for."""
    turn = turn_of(store)
    message = turn.get("message")
    if not message or managed == "external":
        return
    done = [m for m in turn.get("machines") or [] if m != mid]
    asked = asked_for(message)
    if len(done) < asked:
        return
    names = names_in(message)
    said = (f"asks for {asked} machine{'s' if asked != 1 else ''}" + (f" ({', '.join(names)})" if names else "")
            if asked else "asks for no machine")
    raise StoreError(f"not declared: the owner's message this turn {said}"
                     + (f", and {', '.join(done)} is declared already" if done else "")
                     + ". Declare only what they asked for in this message; say in your reply what you would propose "
                       "(a machine for the loop they described, say), and declare it when they ask for it by name")


def note(store: AgentStore, mid: str) -> None:
    """Record ``mid`` as declared in this turn."""
    def add(turn):
        turn = turn or {}
        turn["machines"] = list(dict.fromkeys([*(turn.get("machines") or []), mid]))
        return turn
    store.update_agent_state(MACHINE, "turn", add)


# What the owner expects a machine to change, as their message says it: a direction ("notes files to
# rise", "the error rate falls"), a factor ("up 1.2 times", "double", "20% more") or an effect
# ("I expect fewer rejections"). ``confirm`` refuses a machine none of it was recorded for.
DIRECTION = (r"(?:rise|rises|rising|grow|grows|increase|increases|go(?:es)?\s+up|climb|climbs|fall|falls|drop|drops|"
             r"decrease|decreases|shrink|shrinks|go(?:es)?\s+down|decline|declines|improve|improves|double|doubles|halve|halves)")
UPTO = r"(?:[^.;!?]|\.\d)"                       # within a sentence (1.2 is not its end)
EXPECTS = (
    re.compile(rf"\b(?:expect|expecting|predict|should\s+see|hope)\b{UPTO}*?\b{DIRECTION}\b{UPTO}*", re.IGNORECASE),
    re.compile(rf"\b(?:up|down|by)\s+\d+(?:\.\d+)?\s*(?:times|x|×|%|percent)(?!\w){UPTO}*", re.IGNORECASE),
    re.compile(rf"\b\d+(?:\.\d+)?\s*(?:times|x|×|%|percent)\s+(?:more|less|fewer|higher|lower|faster|slower)\b{UPTO}*",
               re.IGNORECASE),
    re.compile(rf"\b(?:expect|expecting|predict)\s+(?:more|fewer|less|higher|lower|faster|slower)\b{UPTO}*", re.IGNORECASE),
)


def expectations_in(message: str) -> list[str]:
    """The owner's expectations in ``message``, each as the words that say it, in order."""
    found: list[tuple[int, str]] = []
    for pat in EXPECTS:
        for m in pat.finditer(message or ""):
            if not any(a <= m.start() < a + len(t) for a, t in found):
                found.append((m.start(), m.group(0).strip(" ,")))
    return [t for _, t in sorted(found)]


def check_expectation_recorded(store: AgentStore, m: dict) -> None:
    """Refuse to confirm machine ``m`` while the owner's message this turn states an expectation and
    no expected effect is recorded for it."""
    heard = expectations_in(turn_of(store).get("message") or "")
    if heard and not ((m.get("expected_effect") or {}).get("edits")):
        raise StoreError(f"not confirmed: the owner's message states what they expect ({'; '.join(heard)}), and "
                         f"{m.get('id')} has no expected effect recorded. Record it first (declare_expected_effect, read_model "
                         "for the flows: a rate is factor on a flow, a level rises or falls on a stock), then confirm, so "
                         "the prediction is published as it is confirmed. Nothing was confirmed")
