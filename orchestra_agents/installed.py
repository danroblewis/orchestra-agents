"""Whether the task system (the tasking plugin, ``orchestra-tasking``) is installed beside the
agents: Orchestra loads every installed plugin (``orchestra.plugins.load``), so installed is
loaded. The built-in team (the Coordinator's tickets, the workers' and reviewers' offers) needs it;
without it the dummy Supervisor and the Agents page say so instead of handing work to a Coordinator
that has no tickets to plan into."""

from __future__ import annotations

from importlib.metadata import entry_points

TASKING = "tasks"            # the tasking plugin's entry point in ``orchestra.plugins``
WORKFLOWS = "workflows"      # the Workflows plugin's, whose floor offers its steps to the same roles


def _has(name: str) -> bool:
    try:
        return any(ep.name == name for ep in entry_points(group="orchestra.plugins"))
    except Exception:  # noqa: BLE001 - a broken metadata read means the plugin is not known
        return False


def tasking() -> bool:
    return _has(TASKING)


def workflows() -> bool:
    return _has(WORKFLOWS)


def plugins() -> dict[str, bool]:
    """For the Agents page: which of the two plugins that start the built-in team's work are installed."""
    return {"tasking": tasking(), "workflows": workflows()}
