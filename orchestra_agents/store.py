"""The agents' own state, in ``~/.orchestrator/<key>/agents/runtime.json``, behind one file lock:
their sessions (live and archived, each with the host's markers for its transcript), the reports
an agent's tool hands to the agent that runs it, wake-ups queued for an agent, each agent's state
(paused, crashes, schedules, the changes its tools made), the grants of the live sessions' tokens (access.py), the probes' states, the control endpoint, the metric counters and
when the human last read the Supervisor. Nothing of the task system's is here: tickets are the
task system's, reached over the bus (protocol.TaskClient)."""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .clock import now
from .config import Project
from .events import Events
from .measures import Measures


class StoreError(Exception):
    """A request the agents cannot carry out (an unknown agent, a role in use, a grant missing)."""


EMPTY = {"sessions": {}, "archived_sessions": {}, "reports": {}, "wakeups": [], "agent_state": {}, "probes": {}, "grants": {}}
MARKERS_KEPT = 50         # the host's markers kept per session, newest last


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

    def runtime(self) -> dict:
        p = self.project.runtime_file
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        for k, v in EMPTY.items():
            data.setdefault(k, json.loads(json.dumps(v)))
        return data

    def _mutate_runtime(self, fn) -> Any:
        with self._locked():
            data = self.runtime()
            result = fn(data)
            _write(self.project.runtime_file, data)
            return result

    def set_session(self, session_id: str, **fields: Any) -> dict:
        def fn(data):
            s = data["sessions"].setdefault(session_id, {"id": session_id, "status": "idle"})
            s.update(fields)
            s["updated_at"] = now()
            return dict(s)
        return self._mutate_runtime(fn)

    def sessions(self, include_archived: bool = False) -> list[dict]:
        """The live sessions, and with ``include_archived`` the archived ones after them, newest first."""
        rt = self.runtime()
        out = list(rt["sessions"].values())
        if include_archived:
            out += sorted(rt["archived_sessions"].values(), key=lambda s: s.get("started_at") or 0, reverse=True)
        return out

    def find_session(self, session_id: str) -> dict | None:
        r = self.runtime()
        return r["sessions"].get(session_id) or r.get("archived_sessions", {}).get(session_id)

    def add_marker(self, session_id: str, event: dict) -> None:
        """One of the host's events about a session (a turn start, a cut-off, a reset), kept on
        the session's record so its transcript can show it without reading the bus."""
        def fn(data):
            rec = data["sessions"].get(session_id) or data["archived_sessions"].get(session_id)
            if rec is not None:
                rec["markers"] = (rec.get("markers") or [])[-(MARKERS_KEPT - 1):] + [event]
        self._mutate_runtime(fn)

    def purge_ticket_sessions(self, ticket_id: str) -> None:
        """A finished ticket's worker and reviewer sessions leave the live list for the archive."""
        def fn(data):
            for sid in (f"worker-{ticket_id}", f"reviewer-{ticket_id}"):
                rec = data["sessions"].pop(sid, None)
                if rec:
                    rec.update(status="archived", pid=None)
                    data["archived_sessions"][sid] = rec
        self._mutate_runtime(fn)

    def retire_session(self, session_id: str, archived_file: str | None) -> str | None:
        """Start a resident agent over: its record is kept under an archived id, naming where the
        harness left the old transcript (``AgentSession.retire``), so the old conversation stays
        readable as a run; the live record starts empty."""
        archived_id = f"{session_id}-reset-{now().replace(':', '-')[:19]}"

        def fn(data):
            rec = data["sessions"].get(session_id)
            if not rec:
                return None
            old = dict(rec)
            old.update({"id": archived_id, "status": "archived", "pid": None, "reset_at": now(), "session_file": archived_file})
            data["archived_sessions"][archived_id] = old
            rec.update({"session_file": None, "status": "idle", "pid": None, "last_text": "", "phase": "idle", "tool_name": None,
                        "markers": []})
            return archived_id
        return self._mutate_runtime(fn)

    def measure_idle(self, roles: list[str]) -> None:
        self.measures.write(self.measures.idle_points(roles))

    def measure_turn(self, role: str, source: str, started: bool) -> None:
        points = self._mutate_runtime(lambda d: self.measures.turn_points(
            list(d["sessions"].values()), d.setdefault("metric_counts", {}), role=role, source=source, started=started))
        self.measures.write(points)

    def set_report(self, ticket_id: str, report: dict) -> None:
        """What an agent's exit tool hands to the session that runs it; it goes on the bus as is."""
        self._mutate_runtime(lambda d: d["reports"].__setitem__(ticket_id, report))

    def pop_report(self, ticket_id: str) -> dict | None:
        return self._mutate_runtime(lambda d: d["reports"].pop(ticket_id, None))

    def agent_state(self, name: str) -> dict:
        return dict(self.runtime()["agent_state"].get(name) or {})

    def set_agent_state(self, name: str, **fields) -> dict:
        def fn(d):
            st = d["agent_state"].setdefault(name, {})
            st.update(fields)
            return dict(st)
        return self._mutate_runtime(fn)

    def add_action(self, name: str, action: dict, keep: int) -> None:
        """One change an agent's tool made (ledger.py), kept in its state, the newest ``keep``."""
        def fn(d):
            st = d["agent_state"].setdefault(name, {})
            st["actions"] = (st.get("actions") or [])[-(keep - 1):] + [action]
        self._mutate_runtime(fn)

    def add_wakeup(self, target: str, reason: str) -> None:
        self._mutate_runtime(lambda d: d["wakeups"].append({"target": target, "reason": reason, "ts": now()}))

    def drain_wakeups(self, target: str) -> list[dict]:
        """The wake-ups queued for an agent, removed from the queue. Nothing is written when
        there are none: the host asks every second for every agent."""
        with self._locked():
            data = self.runtime()
            mine = [w for w in data["wakeups"] if w["target"] == target]
            if mine:
                data["wakeups"] = [w for w in data["wakeups"] if w["target"] != target]
                _write(self.project.runtime_file, data)
            return mine

    def probe_state(self, name: str) -> dict:
        return dict(self.runtime()["probes"].get(name) or {})

    def probes(self) -> list[str]:
        return list(self.runtime()["probes"])

    def set_probe_state(self, name: str, **fields: Any) -> dict:
        def fn(d):
            st = d["probes"].setdefault(name, {})
            st.update(fields)
            return dict(st)
        return self._mutate_runtime(fn)

    def set_grant(self, session_id: str, grant: dict) -> None:
        """The digest of the token a session was started with, and the role and ticket it was
        issued for (access.py); one per session id, so a new session of that id replaces it."""
        self._mutate_runtime(lambda d: d["grants"].__setitem__(session_id, grant))

    def drop_grant(self, session_id: str) -> None:
        self._mutate_runtime(lambda d: d["grants"].pop(session_id, None))

    def clear_grants(self) -> None:
        """At the host's start: no session of an earlier host is honoured."""
        self._mutate_runtime(lambda d: d["grants"].clear())

    def grants(self) -> dict[str, dict]:
        return dict(self.runtime()["grants"])

    def set_control(self, port: int, pid: int) -> None:
        """Where the running host serves the Supervisor chat (control.py)."""
        self._mutate_runtime(lambda d: d.__setitem__("control", {"port": port, "pid": pid}))

    def clear_control(self, pid: int) -> None:
        """Only the host that recorded the endpoint clears it: a replacement may already be up."""
        self._mutate_runtime(lambda d: d.pop("control", None) if (d.get("control") or {}).get("pid") == pid else None)

    def mark_supervisor_seen(self) -> float:
        """The human has read what the Supervisor last said; returns when."""
        now_t = time.time()
        self._mutate_runtime(lambda d: d.__setitem__("supervisor_seen_at", now_t))
        return now_t
