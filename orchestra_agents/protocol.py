"""What the agents and a task system say to each other on Orchestra's bus. The names are the
contract: the table in orchestra-tasking's tasking/assignments.py and the request names in
tasking/requests.py; nothing is imported from it.

- Assignment (the owner's decision D17: the task system is the record, not the hub). Work passes
  directly between the machines that do it. An offer opens a turn: ``task.assigned`` from the task
  system's queue, ``review.requested`` from the worker that finished (for any reviewer), or
  ``changes.requested`` from the reviewer that rejected (``to`` the worker instance that did the
  work, which takes it back in the same session). A machine claims an offer
  (``task.accepted``); the first valid claim on the bus holds it, and the claimant knows by
  reading its claim back (``holder``): no confirmation comes from the task system. It says it is
  still working (``task.working``, at least every HEARTBEAT_S, with ``text``: what it is doing
  now), and ends the turn by passing the work on (``review.requested``, ``changes.requested``,
  ``review.approved`` for the task system's gate) or with ``task.reported`` when it passes to no
  machine (a worker's BLOCKED, or no report). Every event of a turn names the offer as its
  ``causationid``. The task system listens, records, and may take a turn back
  (``task.released``: a silent holder, an offer nobody took, rejected work its worker did not
  take back within the return timeout, the operator, a restart). There is no machine-sent
  release in the contract: a stop of the host reports every open assignment as ``failed``. The
  task system counts a worker's failed report against the ticket's crash budget (the ticket goes
  back to OPEN); a reviewer's is a review that ended without a verdict (the review is redone, up
  to its no-verdict budget).
- Requests: a planner asks the task system to change tickets (``task.requested``,
  ``task.unblock_requested``, …) or to read them (``task.query_requested``, the operation named in
  it); it answers ``request.done`` with the result or ``request.refused`` with why.
- Attention: the task system asks a planner to look at something (``task.attention_needed``,
  to a role, with the reason).

The names: ``task.*`` is the protocol between the task system and the machines that fill roles
(assignment, requests, attention); ``ticket.*`` and ``milestone.*`` are the facts the task
system records about its own objects (``TASK_EVENTS``). So a request to create a ticket is
``task.requested`` and the fact is ``ticket.created``.

The snapshot (the ``snapshot`` query's answer) is read by the prompts (prompting.py) in these
fields and no others: ``counts``; ``tickets[]`` with ``id``, ``state``, ``title``, ``priority``,
``priority_info.effective``, ``milestone_id``, ``ready``, ``why_waiting``, ``blocked_reason``,
``review_rounds``, ``objective``; ``milestones[]`` with ``id``, ``status``, ``name``;
``capacity`` with ``workers``, ``reviewers``, ``max_workers``, ``max_reviewers``, ``note``;
``pipeline`` with ``paused``, ``reason``; ``assignments[]`` with ``role``, ``instance``, ``ticket``."""

from __future__ import annotations

import time
import uuid

ASSIGNED, ACCEPTED, WORKING, REPORTED, RELEASED = "task.assigned", "task.accepted", "task.working", "task.reported", "task.released"
REVIEW_REQUESTED, CHANGES_REQUESTED, APPROVED = "review.requested", "changes.requested", "review.approved"
OFFERS = (ASSIGNED, REVIEW_REQUESTED, CHANGES_REQUESTED)     # the events that offer a turn
ATTENTION = "task.attention_needed"
# The task system's planner role names, the ``to`` of an attention event (its tasking/events.py).
COORDINATOR, SUPERVISOR = "coordinator", "supervisor"
PLANNER_ROLES = (COORDINATOR, SUPERVISOR)
HEARTBEAT_S = 30.0
TRANSITIONED = "ticket.transitioned"
# The task system's ticket states (its ``State``), copied by hand like the event names below.
STATES = ("OPEN", "BLOCKED", "IN_PROGRESS", "READY_FOR_REVIEW", "IN_REVIEW", "REJECTED", "APPROVED", "MERGING", "DONE", "CANCELLED")
TERMINAL = STATES[-2:]                # DONE and CANCELLED: a transition to one of these ends the ticket; the runner archives its sessions

# The task system's other event types, which an agent may wake on. Copied by hand; the truth is
# orchestra-tasking's emit calls (nothing is imported from it); tests/unit/test_protocol_drift.py
# checks the copy against its source when it is installed. Both packages emit probe.failed and
# probe.recovered: the task system for its [[probes]], the agents' host for a team's.
TASK_EVENTS = (
    "ticket.created", "ticket.updated", TRANSITIONED, "ticket.note_added",
    "milestone.created", "milestone.updated", "milestone.closed",
    "gate.started", "gate.landed", "gate.rejected", "gate.conflict", "gate.error", "gate.base_broken",
    "staging.deployed", "staging.failed", "staging.restarted",
    "pipeline.paused", "pipeline.resumed", "pickup.paused", "pickup.resumed", "capacity.changed", "kill.requested",
    "scheduler.stopped", "probe.failed", "probe.recovered",
)

REQUESTS = {"create_ticket": "task.requested", "update_ticket": "task.change_requested", "add_note": "task.note_requested",
            "cancel_ticket": "task.cancel_requested", "unblock_ticket": "task.unblock_requested",
            "create_milestone": "milestone.requested", "update_milestone": "milestone.change_requested",
            "close_milestone": "milestone.close_requested", "set_capacity": "capacity.change_requested",
            "resume_pipeline": "pipeline.resume_requested"}
QUERY = "task.query_requested"
QUERIES = ("list_tickets", "get_ticket", "project_status", "list_milestones", "get_capacity", "snapshot",
           "list_gate_runs", "get_gate_run", "get_staging_status")
DONE, REFUSED = "request.done", "request.refused"
WAIT_S = 30.0


def valid_claim(ev: dict, offer: dict) -> bool:
    """A claim (``task.accepted``, flat) that may hold ``offer``: its instance, if sent, is its
    source, and an offer addressed ``to`` one instance is claimed by that instance. The rule is
    the task system's (its tasking/assignments.py), copied by hand."""
    src = ev.get("source")
    return (ev.get("type") == ACCEPTED and ev.get("assignment") == offer.get("assignment") and bool(src)
            and ev.get("instance") in (None, src) and offer.get("to") in (None, src))


def holder(evs: list[dict], offer: dict) -> str | None:
    """Who holds ``offer`` by the bus's order: the source of its first valid claim among ``evs``
    (flat events in bus order, read from the offer on), or None while nobody has claimed it."""
    return next((ev["source"] for ev in evs if valid_claim(ev, offer)), None)


class TaskClient:
    """The task system, as an agent's tools reach it: a request on the bus and its answer."""

    def __init__(self, events, role: str = "", source: str | None = None, wait_s: float | None = None):
        self.events, self.role, self.source = events, role, source     # source: the machine the requests come from (default: the bus's)
        self.wait_s = wait_s                                            # how long to wait for the answer (default WAIT_S)

    def __call__(self, op: str, args: dict | None = None) -> dict:
        args = dict(args or {})
        offset = self.events.end()                  # from now: the answer comes after the request
        rid = uuid.uuid4().hex[:12]
        subject = args.get("id") or args.get("milestone_id")
        data = {"request": rid, "args": args, "by": self.role}
        if op in QUERIES:
            self.events.publish(QUERY, {**data, "op": op}, source=self.source)     # a read acts on no ticket: no subject
        elif op in REQUESTS:
            self.events.publish(REQUESTS[op], data, source=self.source, **({"subject": str(subject)} if subject else {}))
        else:
            return {"ok": False, "error": f"the task system has no operation {op!r}"}
        wait = WAIT_S if self.wait_s is None else self.wait_s
        end = time.time() + wait
        while time.time() < end:
            evs, offset = self.events.after(offset)
            for ev in evs:
                if ev.get("request") != rid:
                    continue
                if ev.get("type") == DONE:
                    return ev.get("result") or {"ok": True}
                if ev.get("type") == REFUSED:
                    return {"ok": False, "error": ev.get("error") or "refused"}
            time.sleep(0.25)
        return {"ok": False, "error": f"the task system did not answer within {wait:g} s: is its scheduler running?"}
