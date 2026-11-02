"""Which Workflows floor roles the agents serve, and as which agent (floor.py, floor_runner.py).

- A **confirmed Universal Machine** (machine-design.md §2.4): a machine spec with ``role`` whose
  name is a custom agent of the project (``org_store``; confirm creates its role and its agent).
  That agent answers the floor's offers for the role, in a session per item, and is granted the
  ``report_outcome`` exit tool for them. A spec whose name is no agent is ignored, as every spec
  is; when two name one role, the first by name serves it.
- The built-in **worker** and **reviewer**, for the roles in ``[agents] serves`` that no spec
  takes: the same roles they fill for the task system, so one host answers both plugins."""

from __future__ import annotations

from .builtins import PER_TICKET_ROLES
from .config import Project
from .floor import OUTCOME_TOOL


def spec_roles(project: Project) -> dict[str, str]:
    """Floor role -> the agent of the confirmed Universal Machine that serves it."""
    from .machine_spec import load_all
    from .org_store import all_agents
    specs = {n: s for n, s in load_all(project).items() if s.role}
    if not specs:
        return {}
    agents, out = all_agents(project), {}
    for name, spec in sorted(specs.items()):
        if name in agents and not agents[name].builtin and spec.role not in out:
            out[spec.role] = name
    return out


def served(project: Project) -> dict[str, str]:
    """Floor role -> the agent (by name) that answers its offers here."""
    out = spec_roles(project)
    for r in PER_TICKET_ROLES:
        if r in (project.config.serves or []) and r not in out:
            out[r] = r
    return out


def floor_role(project: Project, agent: str) -> str | None:
    """The floor role ``agent`` serves as a confirmed Universal Machine, if any."""
    return next((r for r, a in spec_roles(project).items() if a == agent), None)


def extra_groups(project: Project, agent: str) -> tuple[str, ...]:
    """The tool groups serving a floor role adds to an agent's own: its exit tool. A built-in worker
    or reviewer serving the floor holds it too, for an offer whose outcomes its own tool does not
    report (floor.report_tool); ``report_outcome`` answers only a session holding an offer."""
    builtin = agent in PER_TICKET_ROLES and agent in (project.config.serves or [])
    return (OUTCOME_TOOL,) if builtin or floor_role(project, agent) else ()
