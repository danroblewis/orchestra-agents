"""The Supervisor hands the human's requirements to the Coordinator, and the Coordinator reports
the plan back (the owner, 2026-10-28: the human does not make tickets; they talk to the
Supervisor, who understands the requirements and conveys them; the Coordinator makes the
milestones and tickets).

Two events, both the agents' own:

- ``work.requested`` {request, requirements, context, to: "coordinator", by} from the Supervisor
  (``hand_to_coordinator``), its subject the request's id. It wakes the Coordinator
  (``wake_on: work.requested:coordinator``), whose wake-up carries the requirements whole.
- ``work.planned`` {request, to: "supervisor", summary, milestone, tickets [{id, title}], questions,
  by} from the Coordinator (``report_plan``), its causation the request. It wakes the Supervisor
  (``work.planned:supervisor``), which tells the human; the tickets it names are then followed
  until they end, so the Supervisor tells the human when they are done (landings.py).

The tickets a plan names are checked against the task system's record (``list_tickets``) before
it is published: a plan names only tickets that exist."""

from __future__ import annotations

import time
import uuid

from .events import WORK_PLANNED, WORK_REQUESTED
from .protocol import COORDINATOR, SUPERVISOR, TaskClient
from .store import AgentStore, StoreError

REQUESTED, PLANNED = WORK_REQUESTED, WORK_PLANNED
REQUESTS_KEPT = 30             # the newest requests remembered in the Supervisor's state


def hand_to_coordinator(store: AgentStore, a: dict, role: str) -> dict:
    requirements, context = (a.get("requirements") or "").strip(), (a.get("context") or "").strip()
    if len(requirements) < 20:
        raise StoreError("write the requirements out: the outcome wanted, how it is verified, constraints, priority; "
                         "the Coordinator never met the human")
    rid = "r-" + uuid.uuid4().hex[:8]
    ev = store.events.publish(REQUESTED, {"request": rid, "requirements": requirements, "context": context,
                                          "to": COORDINATOR, "by": role}, subject=rid)
    asked = dict(store.agent_state(SUPERVISOR).get("handoffs") or {})
    asked[rid] = {"at": time.time(), "by": role, "requirements": requirements[:300], "event": ev.get("id")}
    store.set_agent_state(SUPERVISOR, handoffs=dict(list(asked.items())[-REQUESTS_KEPT:]))
    return {"ok": True, "request": rid,
            "next": "The Coordinator is woken with it now, plans it into a milestone and tickets, and you are woken with "
                    "its plan (work.planned). Tell the human you handed it on and what happens next; name no ticket yet."}


def report_plan(store: AgentStore, a: dict, role: str) -> dict:
    rid, summary = (a.get("request") or "").strip(), (a.get("summary") or "").strip()
    asked = store.agent_state(SUPERVISOR).get("handoffs") or {}
    if rid not in asked:
        raise StoreError(f"no request {rid!r} was handed to the Coordinator; the wake-up names it (request=r-...)"
                         + (f"; open ones: {', '.join(list(asked)[-5:])}" if asked else ""))
    if asked[rid].get("planned"):
        raise StoreError(f"request {rid} was already reported; report each request once")
    ids = [str(t).strip() for t in a.get("tickets") or [] if str(t).strip()]
    titles: dict[str, str] = {}
    if ids:
        got = TaskClient(store.events, role)("list_tickets", {})
        if got.get("ok") is False:
            raise StoreError(f"cannot check the tickets with the task system: {got.get('error')}")
        known = {t.get("id"): t for t in got.get("tickets") or []}
        missing = [t for t in ids if t not in known]
        if missing:
            raise StoreError(f"no ticket {', '.join(missing)} in the task system: name the tickets you created (create_ticket "
                             "answered with each id)")
        titles = {t: known[t].get("title") or "" for t in ids}
    if not ids and not (a.get("questions") or "").strip():
        raise StoreError("a plan names the tickets you created, or the questions that stop you from creating them")
    tickets = [{"id": t, "title": titles.get(t, "")} for t in ids]
    cause = {"causationid": asked[rid]["event"]} if asked[rid].get("event") else {}
    store.events.publish(PLANNED, {"request": rid, "to": SUPERVISOR, "summary": summary,
                                   "milestone": (a.get("milestone") or "").strip() or None, "tickets": tickets,
                                   "questions": (a.get("questions") or "").strip() or None, "by": role},
                         subject=rid, **cause)
    state = dict(store.agent_state(SUPERVISOR).get("handoffs") or {})
    if rid in state:
        state[rid] = {**state[rid], "planned": time.time()}
        store.set_agent_state(SUPERVISOR, handoffs=state)
    return {"ok": True, "request": rid, "tickets": ids,
            "next": "The Supervisor is woken with your plan and tells the human. Reply with a line on what you planned."}


def wake_reason(ev: dict) -> str | None:
    """The wake-up a hand-off or a plan gives, with the requirements or the plan whole (a wake-up's
    other events are shortened)."""
    if ev.get("type") == REQUESTED:
        return (f"event {REQUESTED} request={ev.get('request')} from {ev.get('by') or ev.get('source')}: plan this into a "
                "milestone and tickets (the requirements follow), then call report_plan with the request id.\n"
                f"requirements: {ev.get('requirements')}\n" + (f"context: {ev.get('context')}\n" if ev.get("context") else ""))
    if ev.get("type") == PLANNED:
        tickets = ", ".join(f"{t.get('id')} ({t.get('title')})" for t in ev.get("tickets") or []) or "none"
        return (f"event {PLANNED} request={ev.get('request')}: the Coordinator planned your hand-off. Plan: {ev.get('summary')} "
                f"Milestone: {ev.get('milestone') or '-'}. Tickets: {tickets}."
                + (f" Questions for the human: {ev.get('questions')}" if ev.get("questions") else "")
                + " Tell the human in a few lines what will be done, by which tickets, and what they must decide.")
    return None


def followed(ev: dict) -> list[tuple[str, str]]:
    """The tickets of a plan, (id, title): followed until they end, like the tickets the Supervisor
    made itself (landings.py)."""
    if ev.get("type") != PLANNED or ev.get("to") != SUPERVISOR:
        return []
    return [(str(t.get("id")), str(t.get("title") or "")) for t in ev.get("tickets") or [] if t.get("id")]
