"""What the agents and a task system say to each other on Orchestra's bus. The names are the
contract (orchestra-tasking: docs/proposals/agents-split.md); nothing is imported from it.

- Assignment: the task system offers a ticket to a role (``task.assigned``); a machine that
  fills the role accepts it (``task.accepted``), says it is still working (``task.working``, at
  least every HEARTBEAT_S, with ``text``: what it is doing now, for whoever shows the ticket)
  and reports (``task.reported``); the task system may take it back (``task.released``). There is
  no machine-sent release in the contract: a stop of the host reports every open assignment as
  ``failed``, which the task system counts like a crash (against the ticket's crash budget).
- Requests: a planner asks the task system to change tickets (``task.requested``,
  ``task.unblock_requested``, …) or to read them (``task.query_requested``, the operation named in
  it); it answers ``request.done`` with the result or ``request.refused`` with why.
- Attention: the task system asks a planner to look at something (``task.attention_needed``,
  to a role, with the reason).

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
ATTENTION = "task.attention_needed"
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
    "scheduler.started", "scheduler.stopped", "probe.failed", "probe.recovered",
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


class Follower:
    """Every event on the bus after an offset, in order (a bus read returns the last ``limit``
    events after ``since``, so it is asked for all of them)."""

    def __init__(self, events, start: int | None = None):
        self.events = events
        self.offset = events.end() if start is None else start

    def new(self) -> list[dict]:
        end = self.events.end()
        if end <= self.offset:
            return []
        evs, self.offset = self.events.read(self.offset, limit=end - self.offset)
        return evs


class TaskClient:
    """The task system, as an agent's tools reach it: a request on the bus and its answer."""

    def __init__(self, events, role: str = ""):
        self.events, self.role = events, role

    def __call__(self, op: str, args: dict | None = None) -> dict:
        args = dict(args or {})
        follow = Follower(self.events)
        rid = uuid.uuid4().hex[:12]
        subject = args.get("id") or args.get("milestone_id")
        data = {"request": rid, "args": args, "by": self.role}
        if op in QUERIES:
            self.events.publish(QUERY, {**data, "op": op}, **({"subject": str(subject)} if subject else {}))
        elif op in REQUESTS:
            self.events.publish(REQUESTS[op], data, **({"subject": str(subject)} if subject else {}))
        else:
            return {"ok": False, "error": f"the task system has no operation {op!r}"}
        end = time.time() + WAIT_S
        while time.time() < end:
            for ev in follow.new():
                if ev.get("request") != rid:
                    continue
                if ev.get("type") == DONE:
                    return ev.get("result") or {"ok": True}
                if ev.get("type") == REFUSED:
                    return {"ok": False, "error": ev.get("error") or "refused"}
            time.sleep(0.25)
        return {"ok": False, "error": f"the task system did not answer within {int(WAIT_S)} s: is its scheduler running?"}
