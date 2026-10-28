"""What a configured agent listens for: the event types the host watches on that agent's behalf.
Each agent's announcement (``orchestra.machine.started``, host.py) lists them, and so does its
declaration for the Machines page before it runs (web.py), so Orchestra draws every subscription
to the agent that reacts, never to the host, which is no machine (events.py):

- its own ``wake_on``, each entry's type without its filter;
- the answers to its own requests, ``request.done`` and ``request.refused``: the task system
  publishes them as the asker (its decision D17), and every agent the host wakes asks for a
  snapshot as itself (tooling.snapshot), as its tools do. A per-ticket role (worker, reviewer) is
  never woken and asks nothing: its turn is an offer, answered by the runner;
- the Supervisor's own: the acts that end a ticket it created for the human (``protocol.endings``:
  the gate landed it, in tasks mode its reviewer approved it, a cancel was done), so it can tell
  the human, and ``ticket.created``, by which it remembers which tickets were its (landings.py).

What wakes an agent without an event on the bus (a human's message, a brief or a message from
another agent, a crash loop) is not listed: nothing on the bus causes it. Neither is what the host
reads for its own bookkeeping without any agent acting on it (a finished ticket's sessions go to
the archive)."""

from __future__ import annotations

from .org import AgentDef
from .protocol import CREATED, DONE, REFUSED, SUPERVISOR, endings


def listens_for(agent: AgentDef | None, work: str) -> list[str]:
    """The event types ``agent`` reacts to, as its announcement lists them; ``work`` is the
    project's mode (``code`` or ``tasks``), which decides what ends a ticket."""
    if agent is None:
        return []
    out = {w.split(":")[0] for w in agent.wake_on}
    if not (agent.builtin and not agent.resident):               # a woken agent asks the task system as itself
        out |= {DONE, REFUSED}
    if agent.name == SUPERVISOR:
        out |= {CREATED, *endings(work)}
    return sorted(out)
