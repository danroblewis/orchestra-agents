"""The facts around a Supervisor turn, read fresh by the host: what goes in front of the turn and
what its reply is checked against afterwards (truth.py).

- Before: the changes its tools made since its previous turn began (ledger.py) and the state, read
  from the task system just now, of the tickets it is dealing with: the ones it or the human named
  in recent turns and the ones it created for the human. A compaction of its context, or a
  digest that lists only live tickets, can no longer leave it to remember where a ticket is.
- After: the reply against the same record and the tickets it names, read again. What the reply
  gets wrong is corrected by Orchestra (``truth.correction``)."""

from __future__ import annotations

import time
from collections.abc import Callable

from . import ledger, truth
from .protocol import SUPERVISOR

NAMED_KEPT = 12           # the tickets of recent turns whose state goes in front of the next one
CHECK_WAIT_S = 5.0        # how long the check after a reply waits for the task system's snapshot


class SupervisorFacts:
    def __init__(self, store, ticket_prefix: str, snapshot: Callable[[float], dict]):
        self.store, self.prefix, self.snapshot = store, ticket_prefix, snapshot   # snapshot(wait_s), as the Supervisor asks it

    def before(self, snap: dict, message: str = "") -> tuple[str, float]:
        """The facts a turn's prompt carries (the changes since the previous turn began), and when
        this turn began, which is recorded."""
        now_t = time.time()
        st = self.store.agent_state(SUPERVISOR)
        named = list(dict.fromkeys([*(st.get("tickets_named") or []), *(st.get("asked_tickets") or {}),
                                    *truth.ticket_ids(message, self.prefix)]))
        did = ledger.since(self.store, SUPERVISOR, float(st.get("turn_began") or now_t))
        self.store.set_agent_state(SUPERVISOR, turn_began=now_t)
        return truth.facts_line(truth.states(snap, named), did), now_t

    def after(self, reply: str, began: float, message: str = "") -> str | None:
        """Orchestra's correction to the reply of the turn that began at ``began``, or None; the
        tickets the turn named are remembered for the next one."""
        did = ledger.since(self.store, SUPERVISOR, began)
        ids = truth.ticket_ids(reply, self.prefix)
        st = self.store.agent_state(SUPERVISOR)
        named = [*(st.get("tickets_named") or []), *truth.ticket_ids(message, self.prefix),
                 *truth.ticket_ids("\n".join(did), self.prefix), *ids]
        self.store.set_agent_state(SUPERVISOR, tickets_named=list(dict.fromkeys(reversed(named)))[:NAMED_KEPT][::-1])
        now = truth.states(self.snapshot(CHECK_WAIT_S), ids) if ids else {}
        return truth.correction(reply, did, now)
