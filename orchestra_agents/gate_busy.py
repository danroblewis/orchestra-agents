"""Whether the task system's merge gate is landing a branch now, asked before the base branch is
touched.

The gate lands a branch by fast-forwarding the base branch, and refuses when the base moved during
the run or the repository root has uncommitted changes (``tasking/git.py``, ``land``); the refusal
pauses the pipeline and wakes the Supervisor. A Supervisor commit of ``orchestra.toml``, or
``restart_machine``'s moment with the file edited and uncommitted, during a gate run would do just
that. So before either, the agents ask the task system (its ``list_gate_runs`` query,
``protocol.QUERIES``) whether a run is ``running``, and refuse while one is. When no task system
has been seen on the bus lately, or it does not answer, the agents know of nothing landing, and the
change goes ahead."""

from __future__ import annotations

from orchestra.events import machine_and_instance

from .protocol import TaskClient

SCHEDULER = "task_scheduler"      # the task system's machine on the bus
LOOK_BACK = 5000                  # events read back for a sign of the task system
ASK_S = 10.0                      # how long to wait for its answer


def landing(store, role: str) -> str | None:
    """Why a change to the base branch must wait (a gate run is landing a branch now), or None."""
    end = store.events.end()
    evs, _ = store.events.read(max(0, end - LOOK_BACK), LOOK_BACK)
    if not any(machine_and_instance(ev.get("source"))[0] == SCHEDULER for ev in evs):
        return None
    got = TaskClient(store.events, role, source=role, wait_s=ASK_S)("list_gate_runs")
    run = next((r for r in got.get("runs") or [] if r.get("status") == "running"), None) if got.get("ok") else None
    if run is None:
        return None
    return (f"a gate run ({run.get('id')}) is landing a branch now; a commit to the base branch or an edit of orchestra.toml "
            "would make it fail and pause the pipeline: try again when it ends")
