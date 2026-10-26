"""The agents' own state, in ``~/.orchestrator/<key>/agents/runtime.json``, behind one file lock:
their sessions (live and archived), the reports an agent's tool hands to the agent that runs it,
wake-ups queued for an agent, and each agent's state (paused, crashes, schedules). Nothing of the
task system's is here: tickets are the task system's, reached over the bus (protocol.TaskClient)."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Project
from .events import Events
from .measures import Measures


class StoreError(Exception):
    """A request the agents cannot carry out (an unknown agent, a role in use, a grant missing)."""


EMPTY = {"sessions": {}, "archived_sessions": {}, "reports": {}, "wakeups": [], "agent_state": {}, "probes": {}}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class AgentStore:
    def __init__(self, project: Project, events: Events | None = None):
        self.project = project
        self.events = events or Events.for_project(project)
        self.measures = Measures(project)
        project.ensure_dirs()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self.project.lock_file.open("a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _r(self) -> dict:
        p = self.project.runtime_file
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        for k, v in EMPTY.items():
            data.setdefault(k, json.loads(json.dumps(v)))
        return data

    def runtime(self) -> dict:
        return self._r()

    def _mutate_runtime(self, fn) -> Any:
        with self._locked():
            data = self._r()
            result = fn(data)
            _write(self.project.runtime_file, data)
            return result

    # -- sessions --------------------------------------------------------------

    def set_session(self, session_id: str, **fields: Any) -> dict:
        def fn(data):
            s = data["sessions"].setdefault(session_id, {"id": session_id, "status": "idle"})
            s.update(fields)
            s["updated_at"] = now()
            return dict(s)
        return self._mutate_runtime(fn)

    def sessions(self) -> list[dict]:
        return list(self._r()["sessions"].values())

    def find_session(self, session_id: str) -> dict | None:
        r = self._r()
        return r["sessions"].get(session_id) or r.get("archived_sessions", {}).get(session_id)

    def purge_ticket_sessions(self, ticket_id: str) -> list[str]:
        """A finished ticket's worker and reviewer sessions leave the live list for the archive."""
        def fn(data):
            for sid in (f"worker-{ticket_id}", f"reviewer-{ticket_id}"):
                rec = data["sessions"].pop(sid, None)
                if rec:
                    rec.update(status="archived", pid=None)
                    data["archived_sessions"][sid] = rec
            return []
        return self._mutate_runtime(fn)

    def retire_session(self, session_id: str) -> str | None:
        """Start a resident agent over: its transcript moves to sessions/archive/ and its record is
        kept under an archived id, so the old conversation stays readable as a run."""
        archived_id = f"{session_id}-reset-{now().replace(':', '-')[:19]}"

        def fn(data):
            rec = data["sessions"].get(session_id)
            if not rec:
                return None
            old = dict(rec)
            f = old.get("session_file")
            if f and Path(f).exists():
                dest = Path(f).parent / "archive"
                dest.mkdir(parents=True, exist_ok=True)
                Path(f).rename(dest / Path(f).name)
                old["session_file"] = str(dest / Path(f).name)
            old.update({"id": archived_id, "status": "archived", "pid": None, "reset_at": now()})
            data["archived_sessions"][archived_id] = old
            rec.update({"session_file": None, "status": "idle", "pid": None, "last_text": "", "phase": "idle", "tool_name": None})
            return archived_id
        return self._mutate_runtime(fn)

    def measure_turn(self, role: str, source: str, started: bool) -> None:
        points = self._mutate_runtime(lambda d: self.measures.turn_points(
            list(d["sessions"].values()), d.setdefault("metric_counts", {}), role=role, source=source, started=started))
        self.measures.write(points)

    # -- what an agent's tool hands to the agent that runs it ------------------

    def set_report(self, ticket_id: str, report: dict) -> None:
        report["ts"] = now()
        self._mutate_runtime(lambda d: d["reports"].__setitem__(ticket_id, report))

    def pop_report(self, ticket_id: str) -> dict | None:
        return self._mutate_runtime(lambda d: d["reports"].pop(ticket_id, None))

    # -- agents: their state and their wake-ups --------------------------------

    def agent_state(self, name: str) -> dict:
        return dict(self._r()["agent_state"].get(name) or {})

    def set_agent_state(self, name: str, **fields) -> dict:
        def fn(d):
            st = d["agent_state"].setdefault(name, {})
            st.update(fields)
            return dict(st)
        return self._mutate_runtime(fn)

    def add_wakeup(self, target: str, reason: str) -> None:
        self._mutate_runtime(lambda d: d["wakeups"].append({"target": target, "reason": reason, "ts": now()}))

    def drain_wakeups(self, target: str) -> list[dict]:
        def fn(data):
            mine = [w for w in data["wakeups"] if w["target"] == target]
            data["wakeups"] = [w for w in data["wakeups"] if w["target"] != target]
            return mine
        return self._mutate_runtime(fn)

    # -- the teams' probes (probes.py) -----------------------------------------

    def probe_state(self, name: str) -> dict:
        return dict(self._r()["probes"].get(name) or {})

    def probes(self) -> list[str]:
        return list(self._r()["probes"])

    def set_probe_state(self, name: str, **fields: Any) -> dict:
        def fn(d):
            st = d["probes"].setdefault(name, {})
            st.update(fields)
            return dict(st)
        return self._mutate_runtime(fn)
