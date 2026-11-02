"""The Workflows floor's names, its claim rule and its withdrawal rule, and how an agent's exit tool
maps to an offer's outcome. The source is orchestra-workflows' ``docs/machine-contract.md``, copied
here by hand (nothing is imported from that plugin); tests/unit/test_floor.py checks the copy
against the plugin's ``contract.py``, name by name and on a shared table of event sequences."""

from __future__ import annotations

MADE, CLAIMED, ALIVE, FINISHED, WITHDRAWN = "offer.made", "offer.claimed", "offer.alive", "offer.finished", "offer.withdrawn"
LISTENS_FOR = (MADE, WITHDRAWN)          # what the host watches on behalf of an agent that serves a floor role
ATTENTION_NEEDED = "attention.needed"    # the floor asks a planner (``to``) to look: a guard passed
CLOSED = "item.closed"                   # an item's end, by anyone; nothing waits on a closed item
CLOSED_BY_FLOOR = ("reason", "outcome", "workflow", "last_step")
"""What the floor's own ``item.closed`` carries (as ``<planner>/dispatch``, ``reason: finished``): when a
role-only item's one step finishes, the turn's ``outcome``; when a workflow's item reaches the end of
every workflow it entered, also that ``workflow`` and its ``last_step`` (the step it left for the end)."""
BLOCKED = "blocked"
HEARTBEAT_S = 30.0                       # when an offer names none
REPORT_TOOL = {"worker": "report_ticket", "reviewer": "report_review"}   # a built-in role's exit tool; any other: report_outcome
OUTCOME_TOOL = "report_outcome"


def for_role(ev: dict, role: str) -> bool:
    """An ``offer.made`` (flat) for ``role``."""
    return ev.get("type") == MADE and ev.get("role") == role and bool(ev.get("offer")) and bool(ev.get("item"))


def winner(evs: list[dict], offer: str) -> str | None:
    """Who holds ``offer`` by these flat events in bus order (read from before its ``offer.made``):
    the first claim after it and before any withdrawal. Every earlier claim counts as a rival's, as
    the contract's cautious reading does."""
    made = None
    for ev in evs:
        if ev.get("offer") != offer:
            continue
        if ev.get("type") == MADE:
            made = ev
        elif made is not None and ev.get("type") == WITHDRAWN:
            return None
        elif made is not None and ev.get("type") == CLAIMED and made.get("to") in (None, "", ev.get("source")):
            return ev.get("source")
    return None


def stops(ev: dict, offer: str) -> bool:
    """An ``offer.withdrawn`` that names this offer (every sender names it; its ``instance`` says nothing more)."""
    return ev.get("type") == WITHDRAWN and ev.get("offer") == offer


def wake_reason(ev: dict) -> str | None:
    """What a planner's wake-up says of the floor's ``attention.needed``; None for any other event.
    The guard's key is left out: it names the limit that passed, never what the item waits for
    (T3: a planner read a CI guard's key as the cause). What ends the wait is the item's to read."""
    if ev.get("type") != ATTENTION_NEEDED:
        return None
    where = " ".join(f"{k} {ev[k]}" for k in ("workflow", "step") if ev.get(k))
    then = f"; then it moves to {ev['then']}" if ev.get("then") else ""
    return (f"event {ATTENTION_NEEDED}: the Workflows floor asks the {ev.get('to') or 'planner'} to look"
            + (f" at {ev.get('subject')}" if ev.get("subject") else "") + (f" ({where})" if where else "")
            + f": {ev.get('reason') or 'no reason given'}{then}"
            + ". explain_item says what it waits for and what ends the wait; act through the item's facts or a "
              "request, never by editing the record.")


def workspace(offer: dict) -> str | None:
    """The handed worktree's path, if the offer hands one."""
    for r in offer.get("resources") or []:
        if isinstance(r, dict) and r.get("kind") == "worktree" and r.get("handle"):
            return str(r["handle"])
    return None


def outcome_of(report: dict | None, outcomes: list[str] | None) -> tuple[str, str]:
    """The (outcome, summary) an exit tool's report finishes the offer with. No report, or an
    outcome the offer does not allow, is ``blocked`` with why (the BLOCKED rule)."""
    if not report:
        return BLOCKED, "the agent's turn ended without reporting an outcome"
    summary = str(report.get("summary") or "")
    if "outcome" in report:
        outcome = str(report["outcome"] or "")
    elif "state" in report:                                   # report_ticket
        outcome = "ready" if report["state"] == "READY_FOR_REVIEW" else BLOCKED
        if outcome == BLOCKED:
            summary = str(report.get("blocked_reason") or summary)
    elif "verdict" in report:                                 # report_review
        outcome = "approved" if report["verdict"] == "APPROVED" else "rejected"
        if report.get("feedback"):
            summary = f"{summary}\n\n{report['feedback']}".strip()
    else:
        outcome = ""
    allowed = [str(o) for o in outcomes or []]
    if not outcome or (allowed and outcome != BLOCKED and outcome not in allowed):
        return BLOCKED, f"the agent reported {outcome or 'no outcome'!r}, not one of {', '.join(allowed) or 'any'}: {summary}".strip()
    return outcome, summary


def task_text(offer: dict, role: str, path: str | None) -> tuple[str, str]:
    """The turn's prompt and the nudge when the agent stopped without its outcome."""
    allowed = [str(o) for o in offer.get("outcomes") or []]
    tool = REPORT_TOOL.get(role, OUTCOME_TOOL)
    if tool == "report_ticket":
        how = ("call `report_ticket`: READY_FOR_REVIEW when your work is done (committed, if you were handed a worktree), "
               "otherwise BLOCKED with the reason.")
    elif tool == "report_review":
        how = "call `report_review`: APPROVED, or REJECTED with your feedback."
    else:
        how = (f"call `report_outcome` with `outcome` one of {', '.join(f'`{o}`' for o in allowed)} or `blocked`, "
               if allowed else "call `report_outcome` with the `outcome` your work came to (or `blocked`), ") + \
              "and a `summary` of what you did; `blocked` with the reason when you cannot do it."
    where = (f"Your workspace is {path}, a worktree on the item's branch: work and commit there; leave it committed and clean."
             if path else "No workspace was handed to you: you are in the repository, read only.")
    lines = [f"# Your offer: {offer.get('item')}, step {offer.get('step') or '?'}"
             + (f" of workflow {offer['workflow']}" if offer.get("workflow") else ""),
             "", f"Offer: {offer.get('offer')}", where, "", "## Brief", "", str(offer.get("brief") or "(no brief)"), "",
             f"When you are finished, {how} Call it exactly once; after it you are done."]
    return "\n".join(lines) + "\n", f"You stopped without reporting the outcome. Now {how}"
