"""Whether the task system (the tasking plugin, ``orchestra-tasking``) is installed beside the
agents: Orchestra loads every installed plugin (``orchestra.plugins.load``), so installed is
loaded. The built-in team (the Coordinator's tickets, the workers' and reviewers' offers) needs it;
without it the dummy Supervisor and the Agents page say so instead of handing work to a Coordinator
that has no tickets to plan into."""

from __future__ import annotations

from importlib.metadata import entry_points

TASKING = "tasks"            # the tasking plugin's entry point in ``orchestra.plugins``
TASKING_PACKAGE = "orchestra-tasking"


def tasking() -> bool:
    try:
        return any(ep.name == TASKING for ep in entry_points(group="orchestra.plugins"))
    except Exception:  # noqa: BLE001 - a broken metadata read means no tasking known
        return False
