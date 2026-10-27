"""Telling the human when what they asked for is done. The tickets the Supervisor created
(``ticket.created`` with ``by`` = supervisor: the human's requests, as it wrote them) are
remembered in its agent state; when one reaches DONE, the Supervisor is woken to leave the human
one short message, which the human sees as a message waiting (static/attention.js). Tickets that
end CANCELLED are forgotten. Tickets another planner made from the human's (a Coordinator's
split) are not followed: the Supervisor sees those in its snapshot."""

from __future__ import annotations

from .protocol import SUPERVISOR, TRANSITIONED

CREATED = "ticket.created"


class Landings:
    def __init__(self, store):
        self.store = store

    def see(self, ev: dict, cfg) -> str | None:
        """Remember or settle one event; the Supervisor's wake-up reason when one of the human's
        tickets is DONE."""
        t, tid = ev.get("type"), ev.get("ticket")
        created = t == CREATED and ev.get("by") == SUPERVISOR
        if not tid or not (created or (t == TRANSITIONED and ev.get("to") in ("DONE", "CANCELLED"))):
            return None
        asked = dict(self.store.agent_state(SUPERVISOR).get("asked_tickets") or {})
        if created:
            asked[tid] = ev.get("title") or ""
            self.store.set_agent_state(SUPERVISOR, asked_tickets=asked)
            return None
        if tid not in asked:
            return None
        title = asked.pop(tid)
        self.store.set_agent_state(SUPERVISOR, asked_tickets=asked)
        if ev["to"] != "DONE":
            return None
        how = f"it landed on {cfg.base_branch}" if cfg.work == "code" else "its result was approved"
        return (f"ticket {tid} ({title}), which you created for the human, is DONE: {how}. "
                "Leave the human one short message (a line or two): what now works and where they can see it.")
