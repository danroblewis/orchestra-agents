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
  the human, and ``ticket.created``, by which it remembers which tickets were its (landings.py);
- an agent that may commit predictions (the ``predict`` group): Orchestra's verdict,
  ``orchestra.prediction.judged``, which wakes it for its own change (verdicts.py); and, for a
  per-ticket one, the acts that end its ticket, on which a change it said takes effect when the
  ticket lands is announced as it (modeling.announce_landed).

What wakes an agent without an event on the bus (a human's message, a brief or a message from
another agent, a crash loop) is not listed: nothing on the bus causes it. Neither is what the host
reads for its own bookkeeping without any agent acting on it (a finished ticket's sessions go to
the archive)."""

from __future__ import annotations

from .builtins import ROLES as BUILTIN_ROLES
from .org import JUDGED, AgentDef
from .protocol import CREATED, DONE, REFUSED, SUPERVISOR, endings


def listens_for(agent: AgentDef | None, work: str, groups: tuple[str, ...] | list[str] | None = None) -> list[str]:
    """The event types ``agent`` reacts to, as its announcement lists them; ``work`` is the
    project's mode (``code`` or ``tasks``), which decides what ends a ticket; ``groups``, its tool
    groups (``org.effective_tools``; by default a built-in role's own)."""
    if agent is None:
        return []
    if groups is None:
        groups = [g for r in agent.roles if agent.builtin and r in BUILTIN_ROLES for g in BUILTIN_ROLES[r].tools]
    per_ticket = agent.builtin and not agent.resident
    out = {w.split(":")[0] for w in agent.wake_on}
    if not per_ticket:                                           # a woken agent asks the task system as itself
        out |= {DONE, REFUSED}
    if agent.name == SUPERVISOR:
        out |= {CREATED, *endings(work)}
    if "predict" in groups:
        out |= {JUDGED, *(endings(work) if per_ticket else ())}
    return sorted(out)
