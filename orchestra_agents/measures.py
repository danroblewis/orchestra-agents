"""The agents' measurements, written to Orchestra's metrics store, named for what they measure:

- ``agents_working{role}``: agent sessions of a role in a turn now;
- ``agent_turns_started_total{role}`` and ``agent_turns_finished_total{role}``.

The level and the counters that move it come from the same change, at the same instant, so they
balance; counters are kept in the agents' runtime record, so they carry on across restarts. A
metrics store that cannot be reached loses the points, never a turn."""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING

from orchestra.metrics import Point
from orchestra.metrics import for_project as metrics_for


if TYPE_CHECKING:
    from .config import Project


def machine_of(source: str | None) -> str:
    """``worker/1`` is the machine ``worker``."""
    return (source or "unknown").partition("/")[0]


class Measures:
    def __init__(self, project: Project):
        self.project = project
        self._store = None

    def _metrics(self):
        if self._store is None:
            self._store = metrics_for(self.project.orchestra)
        return self._store

    def write(self, points: list[Point]) -> None:
        if not points:
            return
        try:
            self._metrics().write(points)
        except Exception as e:  # noqa: BLE001 - measuring never breaks the work
            print(f"orchestra-agents: metrics not written ({type(e).__name__}: {e})", file=sys.stderr)

    def _labels(self, **labels: str) -> dict[str, str]:
        return {"project": self.project.key, **labels}

    @staticmethod
    def count(counts: dict, name: str, labels: dict) -> int:
        """Add one to a cumulative counter kept in ``counts`` (a dict saved under the store's lock)."""
        key = name + "|" + "|".join(f"{k}={v}" for k, v in sorted(labels.items()))
        counts[key] = counts.get(key, 0) + 1
        return counts[key]

    def turn_points(self, sessions: list[dict], counts: dict, *, role: str, source: str, started: bool) -> list[Point]:
        """The points for an agent turn starting or finishing."""
        t = time.time()
        labels = {"role": role, "machine": machine_of(source)}   # role names the series (discovery reads machine as attribution only)
        name = "agent_turns_started_total" if started else "agent_turns_finished_total"
        working = sum(1 for s in sessions if s.get("kind") == role and s.get("status") == "running")
        return [Point(name, float(self.count(counts, name, labels)), t, self._labels(**labels)),
                Point("agents_working", float(working), t, self._labels(role=role, machine=machine_of(source)))]
