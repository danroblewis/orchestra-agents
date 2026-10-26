"""The built-in roles: the Supervisor, the Coordinator, the worker and the reviewer.

Adding a role is one entry plus a prompt file in ``prompts/``. The host keeps each resident
role's session alive, wakes it on the events it listens for and on its schedule, and starts a
worker's or reviewer's session when the task system assigns a ticket. Tools are granted by group
name (``harness/tools.py``); every harness reads the groups from ORCHESTRA_TOOLS.

Tool groups:
  tickets      read and change tickets and milestones, project status (requests to the task system)
  capacity     the task system's worker/reviewer capacity dial
  context      read and update the project context document
  pipeline     the task system's gate runs, pipeline resume and staging status (code mode)
  escalate     escalate_to_supervisor
  events       read the event log, raise a team's declared events
  team         read the organisation; brief ad hoc agents and message along the tree
  roles        design the organisation: roles, agents, teams (the Supervisor)
  report_ticket / report_review   the worker's and the reviewer's exit tools
"""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import ASSIGNED, ATTENTION, RELEASED


@dataclass(frozen=True)
class Role:
    name: str                       # ORCHESTRA_ROLE value, e.g. "coordinator"
    session_id: str                 # session id, e.g. "coordinator"
    prompt: str                     # prompt file stem under prompts/
    tools: tuple[str, ...]          # tool groups
    resident: bool = True           # kept alive for the life of `orchestra-agents run`
    wake_on: tuple[str, ...] = ()   # events this role is woken for (``type`` or ``type:filter``)
    triggers: tuple[str, ...] = ()  # what else wakes it, which is not an event on the bus
    emits: tuple[str, ...] = ()     # event types this role's actions cause, through its tools or the task system
    every: float | None = None      # seconds between scheduled wake-ups, or None
    description: str = ""


ROLES: dict[str, Role] = {}


def register(role: Role) -> Role:
    ROLES[role.name] = role
    return role


register(Role(
    name="supervisor", session_id="supervisor", prompt="supervisor",
    tools=("tickets", "context", "pipeline", "roles", "team", "events"),
    wake_on=(f"{ATTENTION}:supervisor",),
    triggers=("a message from the human", "escalate_to_supervisor from any agent", "a crash loop pausing an agent"),
    emits=("task.requested", "task.change_requested", "milestone.requested", "milestone.close_requested", "context.updated",
           "role.created", "agent.created", "team.created", "agent.messaged"),
    description="Talks to the human; translates requests into objectives, verification, and tickets.",
))
register(Role(
    name="coordinator", session_id="coordinator", prompt="coordinator",
    tools=("tickets", "capacity", "escalate"), every=10 * 60,
    wake_on=(f"{ATTENTION}:coordinator",),
    emits=("task.change_requested", "task.cancel_requested", "task.note_requested", "task.unblock_requested",
           "capacity.change_requested"),
    description="Keeps the plan executable: clarity, size, order, dependencies. Never changes intent.",
))
# Per-ticket roles are not resident; the host starts them on the task system's assignments (runner.py).
register(Role(name="worker", session_id="worker-{ticket}", prompt="worker", tools=("report_ticket",), resident=False,
              wake_on=(ASSIGNED, RELEASED), emits=("task.accepted", "task.working", "task.reported"),
              description="Does one ticket in the workspace the task system prepared and reports READY_FOR_REVIEW or BLOCKED."))
register(Role(name="reviewer", session_id="reviewer-{ticket}", prompt="reviewer", tools=("report_review",), resident=False,
              wake_on=(ASSIGNED, RELEASED), emits=("task.accepted", "task.working", "task.reported"),
              description="Reviews one ticket's result (a diff, or RESULT.md) and reports APPROVED or REJECTED."))

# Not agents, but parts of the same event graph (the Agents page's map, web.py eventmap):
SYSTEM_BINDINGS = {
    "task_scheduler": {"description": "The task system's scheduler: offers ready tickets to the roles, applies their reports, "
                                      "keeps the ticket state machine and asks a planner for attention.",
                       "emits": (ASSIGNED, RELEASED, ATTENTION, "request.done", "request.refused",
                                 "ticket.transitioned:OPEN", "ticket.transitioned:IN_PROGRESS", "ticket.transitioned:IN_REVIEW",
                                 "ticket.transitioned:DONE", "ticket.transitioned:BLOCKED", "scheduler.started", "scheduler.stopped"),
                       "wake_on": ("task.accepted", "task.reported", "task.requested", "task.query_requested")},
    "probes": {"description": "A team's commands, run on an interval; an event on each change of result.",
               "emits": ("probe.failed", "probe.recovered"), "wake_on": ()},
    "agents": {"description": "The agents' host (orchestra-agents run): runs the sessions, cuts turns off at their limits, "
                              "restarts crashed agents and answers the task system's assignments for the roles it serves.",
               "emits": ("session.started", "session.settled", "turn.timed_out", "turn.error", "turn.interrupted",
                         "agent.crashed", "agent.crash_loop", "agent.compacted", "agent.reset"),
               "wake_on": (ASSIGNED, RELEASED)},
}


def resident_roles() -> list[Role]:
    return [r for r in ROLES.values() if r.resident]
