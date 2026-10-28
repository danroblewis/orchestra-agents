"""The built-in roles: the Supervisor, the Coordinator, the worker and the reviewer.

Adding a role is one entry plus a prompt file in ``prompts/``. The host keeps each resident
role's session alive, wakes it on the events it listens for and on its schedule, and starts a
worker's or reviewer's session when the task system assigns a ticket. Tools are granted by group
name (``harness/tools.py``); every harness reads the groups from ORCHESTRA_TOOLS.

Tool groups:
  tickets      read tickets and milestones and project status; update, note, cancel and unblock a ticket
               (queries and requests to the task system)
  planning     create tickets and milestones, change and close milestones, and report a plan back to the
               Supervisor (the Coordinator's: it plans what the Supervisor hands it)
  handoff      hand the human's requirements to the Coordinator (the Supervisor's)
  guides       the operator's guides to configuring Orchestra and each installed plugin (read only)
  capacity     the task system's worker/reviewer capacity dial
  context      read and update the project context document
  pipeline     the task system's gate runs, pipeline resume and staging status (code mode)
  escalate     escalate_to_supervisor
  events       read the event log, raise a team's declared events
  team         read the organisation; brief ad hoc agents and message along the tree
  roles        design the organisation: roles, agents, teams (the Supervisor)
  observe      read the whole system: the configuration, processes, logs, metrics, instruments, other
               time-series stores ([[sources]]), the model, the snapshot
  setup        change the configuration through Orchestra, in orchestra.toml or the owner's private
               overlay (roles the AI agents fill, work mode, gate tests, capacity limit, machines,
               collectors, any key), try a collector once, and restart a machine (the Supervisor only)
  model        read the system dynamics model, series, eras, observability, experiments, runs,
               predictions and their verdicts; the modeling guide (modeling.py)
  experiments  make, edit, run forward and inverse, and discard one's own experiments
  replay       replay any experiment over a past window
  predict      commit one's own experiment as the prediction of one's own change, and announce it
  report_ticket / report_review   the worker's and the reviewer's exit tools
"""

from __future__ import annotations

from dataclasses import dataclass

from .events import GAVE_UP
from .events import WORK_PLANNED as PLANNED
from .events import WORK_REQUESTED as REQUESTED
from .protocol import (
    ACCEPTED,
    APPROVED,
    ASSIGNED,
    ATTENTION,
    CHANGES_REQUESTED,
    COORDINATOR,
    RELEASED,
    REPORTED,
    REQUESTS,
    REVIEW_REQUESTED,
    SUPERVISOR,
    WORKING,
)


@dataclass(frozen=True)
class Role:
    name: str                       # ORCHESTRA_ROLE value, e.g. "coordinator"
    prompt: str                     # prompt file stem under prompts/
    tools: tuple[str, ...]          # tool groups
    resident: bool = True           # kept alive for the life of `orchestra agents run`
    wake_on: tuple[str, ...] = ()   # events this role is woken for (``type`` or ``type:filter``)
    triggers: tuple[str, ...] = ()  # what else wakes it, which is not an event on the bus
    emits: tuple[str, ...] = ()     # event types this role's actions cause, through its tools or the task system
    description: str = ""
    read_only: bool = False         # reads the checkout, never edits a file (the harness withholds its editing tools)


ROLES: dict[str, Role] = {}


def register(role: Role) -> Role:
    ROLES[role.name] = role
    return role


register(Role(
    name="supervisor", prompt="supervisor",
    tools=("tickets", "handoff", "guides", "context", "pipeline", "roles", "team", "events", "observe", "setup", "model",
           "experiments", "replay", "predict"),
    wake_on=(f"{ATTENTION}:{SUPERVISOR}", GAVE_UP, f"{PLANNED}:{SUPERVISOR}"),
    triggers=("a message from the human", "escalate_to_supervisor from any agent", "a crash loop pausing an agent",
              "the project not set up yet, when the host starts (first_run.py)"),
    emits=(REQUESTED, REQUESTS["update_ticket"], REQUESTS["cancel_ticket"], REQUESTS["unblock_ticket"],
           "context.updated", "role.created", "agent.created", "team.created", "agent.messaged",
           "config.changed", "instruments.changed", "machine.restarted"),
    read_only=True,
    description="Talks to the human; sets the project up; translates requests into requirements and hands them to the "
                "Coordinator; keeps the project's setup able to do them.",
))
register(Role(
    name="coordinator", prompt="coordinator",
    tools=("tickets", "planning", "guides", "capacity", "escalate", "model", "experiments", "replay"),
    wake_on=(f"{ATTENTION}:{COORDINATOR}", f"{REQUESTED}:{COORDINATOR}"),
    emits=(REQUESTS["create_milestone"], REQUESTS["create_ticket"], PLANNED, REQUESTS["update_ticket"],
           REQUESTS["cancel_ticket"], REQUESTS["add_note"], REQUESTS["unblock_ticket"], REQUESTS["set_capacity"]),
    read_only=True,
    description="Plans what the Supervisor hands it into milestones and tickets, and keeps the plan executable: clarity, "
                "size, order, dependencies. Never changes intent.",
))
# Per-ticket roles are not resident; the host starts them on the offers of work (runner.py): the task
# system's queue, and the machines that pass work on directly (a review requested, changes requested).
register(Role(name="worker", prompt="worker", tools=("report_ticket", "model", "experiments", "replay", "predict"), resident=False,
              wake_on=(ASSIGNED, CHANGES_REQUESTED, RELEASED), emits=(ACCEPTED, WORKING, REVIEW_REQUESTED, REPORTED),
              description="Does one ticket in the workspace it is handed and asks the reviewers, or reports BLOCKED."))
register(Role(name="reviewer", prompt="reviewer", tools=("report_review", "model", "replay"), resident=False,
              wake_on=(ASSIGNED, REVIEW_REQUESTED, RELEASED), emits=(ACCEPTED, WORKING, CHANGES_REQUESTED, APPROVED, REPORTED),
              description="Reviews one ticket's result (a diff, or RESULT.md): approves it for the gate, or sends it back to its worker."))

PER_TICKET_ROLES = tuple(r.name for r in ROLES.values() if not r.resident)   # the task system's roles the agents can fill
