"""A team's probes: conditions the agents' host checks on an interval and turns into events.

A probe is a command with a schedule, declared by a team (``probes`` in its record), run in the
team's space with the team chain's tools on PATH. Exit 0 means the condition holds. The host
remembers each probe's last state and emits ``probe.failed`` and ``probe.recovered`` only on the
transition, so a failing probe wakes the agents subscribed to it once, not every interval. Agents
never run probes; the host supplies the clock and the event, the team supplies the check. The host
is no machine (events.py): the event is published as the agent responsible for the team, its lead,
or the Supervisor when the team has none (``source``).
(The probes in ``orchestra.toml``, ``[[probes]]``, are the task system's.)"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from orchestra.config import ConfigError

from .clock import parse_interval
from .config import Project
from .events import Events
from .org import team_chain
from .store import AgentStore

OUTPUT_TAIL = 2000


@dataclass(frozen=True)
class ProbeSpec:
    name: str
    command: str
    every: float                 # seconds
    timeout: float = 120.0
    cwd: Path | None = None
    team: str | None = None
    path_dirs: tuple[str, ...] = ()
    source: str = "supervisor"   # the agent whose responsibility the probe is: the team's lead, or the Supervisor


def _spec(d: dict, project: Project, team=None, path_dirs: tuple[str, ...] = ()) -> ProbeSpec:
    every = d.get("every", "5m")
    secs = parse_interval(every) if isinstance(every, str) else float(every)
    cwd = team.space_dir(project) if team is not None else project.repo_root
    return ProbeSpec(name=d["name"], command=d["command"], every=secs, timeout=float(d.get("timeout", 120)),
                     cwd=cwd, team=team.name if team is not None else None, path_dirs=path_dirs,
                     source=(team.lead if team is not None and team.lead else "supervisor"))


def team_probes(project: Project, teams: dict) -> list[ProbeSpec]:
    """Every team's probes, run in the team's space with the team chain's tools on PATH."""
    out = []
    for t in teams.values():
        dirs = tuple(str(x.space_dir(project) / "tools") for x in team_chain(teams, t.name))
        for d in t.probes:
            try:
                out.append(_spec(d, project, t, dirs))
            except (KeyError, ConfigError, ValueError):
                continue
    return out


class Prober:
    """Runs due probes in the background and records their state. One thread per probe
    at a time; a probe that is still running when it is next due is simply skipped."""

    def __init__(self, project: Project, store: AgentStore, events: Events):
        self.project, self.store, self.events = project, store, events
        self.running: dict[str, threading.Thread] = {}

    def tick(self, specs: list[ProbeSpec]) -> None:
        now_t = time.time()
        for k in [k for k, th in self.running.items() if not th.is_alive()]:
            self.running.pop(k)
        known = {s.name for s in specs}
        for s in specs:
            st = self.store.probe_state(s.name)
            if s.name in self.running or (st.get("last_run") and now_t - st["last_run"] < s.every):
                continue
            th = threading.Thread(target=self._run, args=(s,), daemon=True, name=f"probe-{s.name}")
            self.running[s.name] = th
            th.start()
        for name in self.store.probes():
            if name not in known and not self.store.probe_state(name).get("removed"):
                self.store.set_probe_state(name, removed=True)

    def _run(self, s: ProbeSpec) -> None:
        env = {**os.environ, "ORCHESTRA_REPO": str(self.project.repo_root), "ORCHESTRA_PROBE": s.name}
        if s.path_dirs:
            env["PATH"] = os.pathsep.join([*s.path_dirs, env.get("PATH", "")])
        started = time.time()
        try:
            p = subprocess.run(s.command, shell=True, cwd=str(s.cwd or self.project.repo_root), env=env,
                               capture_output=True, text=True, timeout=s.timeout, check=False)
            ok, output = p.returncode == 0, (p.stdout + p.stderr)[-OUTPUT_TAIL:]
        except subprocess.TimeoutExpired as e:
            ok, output = False, f"timed out after {s.timeout:.0f}s\n" + ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))[-OUTPUT_TAIL:]
        except OSError as e:
            ok, output = False, f"could not run: {e}"
        status = "ok" if ok else "failed"
        prev = self.store.probe_state(s.name).get("status")
        fields = {"status": status, "last_run": started, "duration": time.time() - started, "output": output,
                  "every": s.every, "team": s.team, "command": s.command, "removed": False}
        if prev != status:
            fields["since"] = started
        self.store.set_probe_state(s.name, **fields)
        if status == "failed" and prev != "failed":
            self.events.emit("probe.failed", src=s.source, probe=s.name, team=s.team, output=output[-600:])
        elif status == "ok" and prev == "failed":
            self.events.emit("probe.recovered", src=s.source, probe=s.name, team=s.team)
