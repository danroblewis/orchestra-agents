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
  observe      read the whole system: orchestra.toml, processes, logs, metrics, the model, the snapshot
  setup        change orchestra.toml (roles the AI agents fill, work mode, gate tests, capacity limit,
               machines and collectors) and restart a machine (the Supervisor only)
  report_ticket / report_review   the worker's and the reviewer's exit tools
"""

from __future__ import annotations

from dataclasses import dataclass

from .events import GAVE_UP
from .protocol import (
    ACCEPTED,
    ASSIGNED,
    ATTENTION,
    COORDINATOR,
    RELEASED,
    REPORTED,
    REQUESTS,
    SUPERVISOR,
    WORKING,
)


@dataclass(frozen=True)
class Role:
    name: str                       # ORCHESTRA_ROLE value, e.g. "coordinator"
    prompt: str                     # prompt file stem under prompts/
    tools: tuple[str, ...]          # tool groups
    resident: bool = True           # kept alive for the life of `orchestra-agents run`
    wake_on: tuple[str, ...] = ()   # events this role is woken for (``type`` or ``type:filter``)
    triggers: tuple[str, ...] = ()  # what else wakes it, which is not an event on the bus
    emits: tuple[str, ...] = ()     # event types this role's actions cause, through its tools or the task system
    description: str = ""


ROLES: dict[str, Role] = {}


def register(role: Role) -> Role:
    ROLES[role.name] = role
    return role


register(Role(
    name="supervisor", prompt="supervisor",
    tools=("tickets", "context", "pipeline", "roles", "team", "events", "observe", "setup"),
    wake_on=(f"{ATTENTION}:{SUPERVISOR}", GAVE_UP),
    triggers=("a message from the human", "escalate_to_supervisor from any agent", "a crash loop pausing an agent"),
    emits=(REQUESTS["create_ticket"], REQUESTS["update_ticket"], REQUESTS["create_milestone"], REQUESTS["close_milestone"],
           "context.updated", "role.created", "agent.created", "team.created", "agent.messaged",
           "config.changed", "machine.restarted"),
    description="Talks to the human; translates requests into objectives, verification, and tickets; keeps the "
                "project's setup able to do them.",
))
register(Role(
    name="coordinator", prompt="coordinator",
    tools=("tickets", "capacity", "escalate"),
    wake_on=(f"{ATTENTION}:{COORDINATOR}",),
    emits=(REQUESTS["update_ticket"], REQUESTS["cancel_ticket"], REQUESTS["add_note"], REQUESTS["unblock_ticket"],
           REQUESTS["set_capacity"]),
    description="Keeps the plan executable: clarity, size, order, dependencies. Never changes intent.",
))
# Per-ticket roles are not resident; the host starts them on the task system's assignments (runner.py).
register(Role(name="worker", prompt="worker", tools=("report_ticket",), resident=False,
              wake_on=(ASSIGNED, RELEASED), emits=(ACCEPTED, WORKING, REPORTED),
              description="Does one ticket in the workspace the task system prepared and reports READY_FOR_REVIEW or BLOCKED."))
register(Role(name="reviewer", prompt="reviewer", tools=("report_review",), resident=False,
              wake_on=(ASSIGNED, RELEASED), emits=(ACCEPTED, WORKING, REPORTED),
              description="Reviews one ticket's result (a diff, or RESULT.md) and reports APPROVED or REJECTED."))

PER_TICKET_ROLES = tuple(r.name for r in ROLES.values() if not r.resident)   # the task system's roles the agents can fill
