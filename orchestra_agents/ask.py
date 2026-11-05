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

Confirming or discarding an element of the declared layer, a machine included, is the owner's by
Orchestra's contract (their quoted words or their click on a page, ``orchestra.intent.requested``
from ``owner``), with one exception the owner gives Orchestra itself: the standing consent
``[intended] settle_own = ["supervisor"]`` in their private overlay (Orchestra's rule 9 as amended
2026-11-04; only the owner writes it, Orchestra's ``config_edit`` refuses to). Under ``outside``
with it (``settles_own``), the Supervisor confirms and discards its own proposals and pending
changes with its reason, recorded by Orchestra as ``confirmed.by: supervisor`` with the consent,
never as the owner's, and says in one line what it confirmed, why, and that the owner can discard
it; what the owner declared or confirmed stays theirs (Orchestra refuses the rest). Without it, or
under ``always``, the Supervisor proposes and says the confirm is the owner's, on the Machines page
or in the chat, without asking a question."""

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


def settles_own(project) -> str | None:
    """Where the owner gave Orchestra the standing consent that the Supervisor settles its own
    proposals (``[intended] settle_own``, read by Orchestra from the private overlay only), when the
    Supervisor acts on its own (``outside``); None otherwise, and with an Orchestra too old to know it."""
    if not acts(project):
        return None
    cfg = getattr(getattr(project, "orchestra", None), "config", None)
    if "supervisor" not in tuple(getattr(cfg, "settle_own", ()) or ()):
        return None
    return getattr(cfg, "settle_own_from", None) or "[intended] settle_own"


def settled_line(where: str, what: str) -> str:
    """What the Supervisor says after settling its own proposal under the owner's standing consent."""
    return (f"{what} by you, under the owner's standing consent ({where}): Orchestra records it as yours "
            "(confirmed.by: supervisor), never theirs. Tell the owner in one line what you did and why, and that they "
            "can discard it on the Machines page or in the chat")


def own_next(project, el: dict | None, change: str | None = None) -> str | None:
    """The line that tells the Supervisor to settle its own proposal (or pending ``change``) itself:
    only under the standing consent, and only for what is its own, not the owner's."""
    where = settles_own(project)
    el = el or {}
    if where is None or (el.get("provenance") or {}).get("author") == OWNER or (el.get("confirmed") or {}).get("by") == OWNER:
        return None
    what = f"confirm with change {change}" if change else "confirm"
    return (f"the owner's standing consent is on ({where}): when the draft is complete (its expected effect recorded, "
            f"or why none can be said), {what} it yourself in this turn with your reason, and tell the owner in one line "
            "what you confirmed and why, and that they can discard it; no question")
