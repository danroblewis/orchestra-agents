"""``[agents] ask``: when the Supervisor asks the owner before it acts (the owner, 2026-11-04: "I
don't really care about the 'ask before widening'. I want the Supervisor to do most things without
asking me").

- ``outside`` (the default): the Supervisor decides and acts, then tells the owner in one line what
  it did and why. A tool that took the owner's quoted words takes its ``reason`` instead, and what
  it records is the Supervisor's decision (``by: supervisor`` with the reason), never the owner's
  words: no quote is made up for it.
- ``always``: the behaviour before 2026-11-04: every change the owner's words did not ask for is
  asked first, in one sentence, and made on their quoted yes.

Held in both modes, because each is a write outside this machine or a query that could take a
store down (the owner's two hard lines):

- an Outbox draft (Jira, Slack, an email: anything the Outbox sends), orchestra-scm's push and pull
  request (``supervisor_pr``: the provider's own configuration and the Outbox approve it), any
  ``git push`` (``push_guard``), and a kit that writes to the system it connects
  (``approve_integration``: the owner's quoted yes);
- the store's ``explore`` (the owner's alone: never written here), the refusal of a match-everything
  selector, and the store's rate and daily caps (Orchestra's ``sources``), and the owner's limits on
  a store before its first read (``source_limits.check``).

Also held in both modes, by Orchestra's contract rather than this setting: confirming or discarding
an element of the declared layer, a machine included, is the owner's (their quoted words or their
click on a page, ``orchestra.intent.requested`` from ``owner``). Orchestra's declared layer refuses
a proposal settled by its own author (``orchestra/sd/intended/consent.py``), and its rule 9 makes a
page's confirm a request the Supervisor answers; under ``outside`` the Supervisor proposes and says
the confirm is the owner's, on the Machines page or in the chat, without asking a question."""

from __future__ import annotations

import time

MODES = ("outside", "always")
DEFAULT = "outside"
OWNER = "owner"
SAY = "tell the owner in one line what you did and why"


def mode(project) -> str:
    """The project's ``[agents] ask`` (an agents ``Project``, or anything with its ``config``)."""
    return getattr(getattr(project, "config", None), "ask", DEFAULT) or DEFAULT


def acts(project) -> bool:
    """Whether the Supervisor decides and acts without asking first (``ask = "outside"``)."""
    return mode(project) == "outside"


def decided(project, prov: dict) -> bool:
    """Whether a call the owner's quoted words do not back goes ahead as the Supervisor's own decision:
    under ``outside``, with its reason (``design.provenance`` refuses a call without one). A call with
    the owner's words is theirs in both modes."""
    return prov.get("author") == OWNER or (acts(project) and len(str(prov.get("reason") or "").strip()) >= 10)


def decision(prov: dict) -> dict:
    """What is recorded of the Supervisor's own decision: by whom, why, in which turn, when."""
    return {"by": "supervisor", "reason": str(prov.get("reason") or "").strip(), "turn": prov.get("turn"), "at": time.time()}
