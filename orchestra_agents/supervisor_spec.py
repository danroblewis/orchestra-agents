"""The Supervisor's starting spec, written into the project the first time the host runs there
(the owner, 2026-10-28: starting duties written into the project, Sonnet by default).

From then on the spec is the project's: the Supervisor grows it with its duty tools and the owner
edits or reverts it like any file. This module only writes the first one, and only when the
project has none (``ensure``); it never overwrites."""

from __future__ import annotations

import time
from pathlib import Path

from . import spec_git
from .config import Project
from .machine_spec import DEFAULT_REVIEW_EVERY, Duty, MachineSpec, load, render

PROMPTS = Path(__file__).parent / "prompts"
DEFAULT_MODEL = "sonnet"
FLOW_LISTENS = ["orchestra.model.stalled", "orchestra.machine.quiet", "orchestra.model.diverged", "gate.base_broken",
                "pipeline.paused", "outbox.rate_limit.unresolvable"]
FLOW_TOOLS = ["observe", "model", "predict", "events", "setup", "tickets", "team", "pipeline", "handoff", "patch"]
BUILTIN_DUTIES = (
    ("talk_to_owner", "talk with the owner: their requests become requirements; their questions get true answers"),
    ("setup", "keep the project set up and able to do what is asked: configuration, measurements, the machines that run"),
    ("hand_to_planner", "hand the owner's requirements to the Coordinator (work.requested) and tell them its plan (work.planned)"),
)


def starting(at: str | None = None) -> MachineSpec:
    at = at or time.strftime("%Y-%m-%d")
    duties = [Duty(name=n, builtin=True, about=a) for n, a in BUILTIN_DUTIES]
    duties.append(Duty(name="keep_flow", prompt="duties/keep_flow.md", listens=list(FLOW_LISTENS), tools=list(FLOW_TOOLS),
                       about="keep the work moving: find blockages, fix what is yours, predict each fix",
                       added_by="orchestra-agents", added_at=at,
                       reason="the owner, 2026-10-28: something that checks for blockages and tries to fix them"))
    duties.append(Duty(name="review_round", prompt="duties/review_round.md", tools=["observe", "model", "predict", "roles"],
                       about="review the system on a schedule; name the one thing that would improve flow most",
                       added_by="orchestra-agents", added_at=at,
                       reason="the owner, 2026-10-28: a schedule where it reviews the current state of the system"))
    return MachineSpec(name="supervisor", engagement="continuous", model=DEFAULT_MODEL, created_by="orchestra-agents",
                       description="Talks with the owner; keeps the project set up and its work moving; grows its own duties.",
                       review_every=DEFAULT_REVIEW_EVERY, duties=duties)


def files(spec: MachineSpec) -> dict[str, str]:
    d = spec.dir
    out = {f"{d}/machine.toml": render(spec), f"{d}/role.md": (PROMPTS / "supervisor_role.md").read_text(encoding="utf-8")}
    for duty in spec.duties:
        if not duty.builtin:
            out[f"{d}/{duty.prompt}"] = (PROMPTS / "duties" / f"{duty.name}.md").read_text(encoding="utf-8")
    out[f"{d}/ledger.md"] = ("# Why each duty exists\n\n"
                             "One line per change: when, the change, the duty, who asked and why, the commit.\n\n"
                             + "".join(f"- {duty.added_at} add `{duty.name}` ({duty.added_by}): {duty.reason}\n"
                                       for duty in spec.duties if not duty.builtin))
    return out


def ensure(project: Project) -> dict | None:
    """Write the Supervisor's starting spec when the project has none; what was done, or None
    when it already had one."""
    if load(project, "supervisor") is not None:
        return None
    # In code work the merge gate lands branches on the base branch, and a commit there while it lands
    # makes the landing fail; the host starts beside the gate and cannot know it is idle, so the
    # first spec goes to the side branch, merged by the Supervisor's first change on a clean checkout.
    busy = "the merge gate may be landing on the base branch (work = code)" if project.config.work == "code" else None
    return spec_git.commit(project, files(starting()), "orchestra-agents: the Supervisor's spec and its starting duties\n\n"
                           "Written the first time the agents ran in this project: keep_flow and review_round, on Sonnet. "
                           "From now on the spec is the project's.", allow_plain=True, not_base=busy)
