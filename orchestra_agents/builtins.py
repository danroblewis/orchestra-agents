"""The role registry: every long-running agent kind is declared here.

Adding a role is one entry plus a prompt file in ``prompts/``. The loop keeps each
resident role's Pi session alive, wakes it on the listed events, and on a schedule
when ``every`` is set. Tools are granted by group name; the Pi extension reads the
groups from ORCHESTRA_TOOLS so no TypeScript changes are needed for a new role.

Tool groups (see harness/tools.py):
  tickets      read and write tickets and milestones, project status
  capacity     the runtime worker/reviewer capacity dial (0..configured max)
  context      read and update the project context document
  pipeline     gate runs, pipeline resume, staging status and restart
  escalate     escalate_to_supervisor
  events       read the event log
  team         read the organisation; brief ad hoc agents and message along the tree (leads, supervisor)
  roles        design the organisation: roles, agents, teams (supervisor)
  report_ticket / report_review   the worker and reviewer exit tools
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Role:
    name: str                       # ORCHESTRA_ROLE value, e.g. "coordinator"
    session_id: str                 # session id, e.g. "coordinator"
    prompt: str                     # prompt file stem under agents/prompts/
    tools: tuple[str, ...]          # tool groups
    resident: bool = True           # kept alive for the life of `orchestra-agents run`
    wake_on: tuple[str, ...] = ()   # event types this role is woken for (the host matches them and wakes it)
    triggers: tuple[str, ...] = ()  # conditions that are not events: a free slot, a human message, a threshold
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
    wake_on=("gate.base_broken", "gate.error", "staging.failed", "pipeline.paused"),
    triggers=("a message from the human", "escalate_to_supervisor from any agent"),
    emits=("ticket.created", "ticket.updated", "milestone.created", "milestone.closed", "context.updated",
           "role.created", "agent.created", "team.created", "agent.messaged"),
    description="Talks to the human; translates requests into objectives, verification, and tickets.",
))
register(Role(
    name="coordinator", session_id="coordinator", prompt="coordinator",
    tools=("tickets", "capacity", "escalate"), every=10 * 60,
    wake_on=("ticket.created", "ticket.transitioned:DONE", "ticket.transitioned:BLOCKED", "milestone.created", "milestone.closed"),
    triggers=("a ticket rejected at the review threshold",),
    emits=("ticket.updated", "ticket.transitioned:CANCELLED", "ticket.note_added", "capacity.changed"),
    description="Keeps the plan executable: clarity, size, order, dependencies. Never changes intent.",
))
# Per-ticket roles are not resident; the host starts them on the task system's assignments.
register(Role(name="worker", session_id="worker-{ticket}", prompt="worker", tools=("report_ticket",), resident=False,
              wake_on=("task.assigned", "task.released"),
              triggers=("an OPEN ticket whose dependencies are DONE and whose milestone is open", "a free worker slot"),
              emits=("ticket.transitioned:READY_FOR_REVIEW", "ticket.transitioned:BLOCKED"),
              description="Implements one ticket on its own branch and reports READY_FOR_REVIEW or BLOCKED."))
register(Role(name="reviewer", session_id="reviewer-{ticket}", prompt="reviewer", tools=("report_review",), resident=False,
              wake_on=("task.assigned", "task.released"), triggers=("a free reviewer slot",),
              emits=("ticket.transitioned:APPROVED", "ticket.transitioned:REJECTED"),
              description="Reviews one ticket's diff like a pull request and reports APPROVED or REJECTED."))

# Not agents, but parts of the same event graph (the Agents page's map, web.py eventmap):
SYSTEM_BINDINGS = {
    "task_scheduler": {"description": "The task system's scheduler: offers ready tickets to the roles, applies their reports, "
                                      "keeps the ticket state machine and asks a planner for attention.",
                       "emits": ("task.assigned", "task.released", "task.attention_needed",
                                 "ticket.transitioned:OPEN", "ticket.transitioned:IN_PROGRESS", "ticket.transitioned:IN_REVIEW",
                                 "ticket.transitioned:DONE", "pickup.paused", "pickup.resumed", "kill.requested",
                                 "scheduler.started", "scheduler.stopped"),
                       "wake_on": ("task.accepted", "task.reported")},
    "merge_gate": {"description": "Lands approved branches: build, unit, start, acceptance; then staging.",
                   "emits": ("gate.started", "gate.landed", "gate.rejected", "gate.conflict", "gate.error", "gate.base_broken",
                             "ticket.transitioned:MERGING", "ticket.transitioned:DONE", "ticket.transitioned:REJECTED",
                             "staging.deployed", "staging.failed", "pipeline.paused"),
                   "wake_on": ("ticket.transitioned:APPROVED",)},
    "probes": {"description": "Commands run on an interval; an event on each change of result.",
               "emits": ("probe.failed", "probe.recovered"), "wake_on": ()},
    "agents": {"description": "The agents' host (orchestra-agents run): runs the sessions, cuts turns off at their limits, "
                              "restarts crashed agents and queues a planner's wake-up when the task system asks.",
               "emits": ("session.started", "session.settled", "turn.timed_out", "turn.error", "turn.interrupted",
                         "agent.crashed", "agent.crash_loop", "agent.compacted", "agent.reset"),
               "wake_on": ("task.attention_needed",)},
}

# Future roles slot in here, for example:
#   register(Role(name="product_owner", session_id="product-owner", prompt="product_owner",
#                 tools=("tickets", "context", "escalate"), every=24 * 3600,
#                 description="Daily review of objectives versus landed work."))
#   register(Role(name="researcher", ..., every=7 * 24 * 3600))


def resident_roles() -> list[Role]:
    return [r for r in ROLES.values() if r.resident]
