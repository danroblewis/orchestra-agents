"""What the Supervisor reads to see the system before it concludes anything: the project's
``orchestra.toml`` and its history, the processes Orchestra's launcher keeps running (its runtime
record), the collectors the file declares, any machine's log, and a summary of the setup (which
machines fill the task system's roles, the work mode, what runs). The metrics and the model are in
``observe_model.py``; the event stream is ``read_events`` and the task system's picture is its
``snapshot`` query (``tooling.py``).

Everything here reads, through Orchestra as a library (``orchestra.runtime``, ``orchestra.collect``,
``orchestra.web.machine_logs``) or the files Orchestra names; nothing is written. A log is read
only within the project's repository or its state directory, as Orchestra's web server reads one."""

from __future__ import annotations

import time
import tomllib
from pathlib import Path

from orchestra import runtime
from orchestra.collect import spec as collect_spec
from orchestra.events import machine_and_instance
from orchestra.web.machine_logs import readable, tail

from .config import Project
from .fillers import Fillers
from .secret_shapes import redact
from .store import AgentStore, StoreError
from .toml_edit import FILE, git, path, why_not_committable

STARTED = "orchestra.machine.started"
LOOK_BACK = 5000


def read_config(project: Project) -> dict:
    """The file as it is, with anything that looks like a secret hidden (``secret_shapes``: a
    session's transcript is no place for one), whether it can take a change now, and its last
    commits."""
    log = git(project, "log", "-8", "--format=%h %ad %s", "--date=format:%Y-%m-%d %H:%M", "--", FILE).stdout.splitlines()
    return {"ok": True, "path": str(path(project)), "text": redact(path(project).read_text(encoding="utf-8")),
            "can_change": why_not_committable(project) or "yes: the root is on the base branch and the file is committed",
            "recent_commits": log}


def _collectors(project: Project) -> tuple[list[dict], list[str]]:
    raw = tomllib.loads(path(project).read_text(encoding="utf-8"))
    found, errors = collect_spec.from_toml(raw)
    out = []
    for c in found:
        log = project.orchestra.logs_dir / "machines" / f"{c.name}.log"
        last = tail(log, 1) if log.exists() else []
        out.append({"name": c.name, "kind": c.kind, "target": c.target, "every_s": c.every_s,
                    "last_log_line": last[-1] if last else "(no log yet: it has not run)"})
    return out, errors


def list_processes(project: Project) -> dict:
    """The launcher's runtime record: who launches, and every process with its state, restarts,
    last exit code and log; and the collectors the file declares, with the last line of each log."""
    rec = runtime.read(project.orchestra)
    holder = runtime.launcher(rec)
    now = time.time()
    procs = [{"name": n, "owner": r.get("owner"), "state": r.get("state"), "pid": r.get("pid"),
              "up_s": round(now - r["since"]) if r.get("since") and r.get("state") == "running" else None,
              "restarts": r.get("restarts"), "last_exit_code": r.get("code"), "command": r.get("command"), "log": r.get("log")}
             for n, r in sorted((rec.get("processes") or {}).items())]
    collectors, errors = _collectors(project)
    return {"ok": True,
            "launcher": (f"orchestra run (pid {holder['pid']}) launches this project's processes" if holder else
                         "no orchestra run launches this project's processes now: nothing declared runs"),
            "processes": procs, "collectors": collectors, "entries_left_out": errors}


def _announced_log(store: AgentStore, name: str) -> str | None:
    end = store.events.end()
    evs, _ = store.events.read(max(0, end - LOOK_BACK), LOOK_BACK)
    for ev in reversed(evs):
        if ev.get("type") == STARTED and name in (ev.get("source"), machine_and_instance(ev.get("source"))[0]) and ev.get("log"):
            return ev["log"]
    return None


def read_machine_log(project: Project, store: AgentStore, a: dict) -> dict:
    """The end of a machine's log: a launched process's or a collector's (``logs/machines/<name>.log``),
    Orchestra's host's (``orchestra-host``), or the log a machine announced when it started."""
    name, lines = (a.get("name") or "").strip(), max(1, min(int(a.get("lines") or 80), 400))
    o = project.orchestra
    candidates = [o.logs_dir / "orchestra-host.log"] if name in ("orchestra-host", "orchestra") else []
    candidates.append(o.logs_dir / "machines" / f"{name}.log")
    if announced := _announced_log(store, name):
        candidates.append(announced)
    why = "no log"
    for c in candidates:
        real, why = readable(str(c), [project.repo_root, o.state_dir])
        if real is not None:
            return {"ok": True, "name": name, "log": str(real), "lines": tail(real, lines)}
    known = sorted(p.stem for p in (o.logs_dir / "machines").glob("*.log"))
    raise StoreError(f"no readable log for {name!r} ({why}); the machines with logs: {', '.join(known) or 'none'}, "
                     "and orchestra-host")


def _not_launched(project: Project, fillers: Fillers, procs: dict) -> list[str]:
    """The programs seen filling a role that Orchestra's launcher does not run: no ``[[machines]]``
    entry and no plugin's process announced their log (the launcher gives each the log it keeps,
    ``ORCHESTRA_MACHINE_LOG``). Someone started them by hand; the Supervisor can neither restart
    nor change them, and they take tickets they may not be written for."""
    def real(p: str) -> str:
        q = Path(p)
        return str((q if q.is_absolute() else project.repo_root / q).resolve())
    launched = {real(x["log"]) for x in procs["processes"] if x.get("log")}
    out = []
    for role, inst in fillers.programs():
        log = fillers.logs.get(inst)
        if log and real(log) in launched:
            continue
        out.append(f"{inst} fills the {role} role but is not a [[machines]] entry or a plugin's process (started by hand"
                   + (f", its log {log}" if log else ", it announced no log") + "): you cannot restart or change it with your "
                   "tools; tell the human which process to stop, or have it run from [[machines]] (set_machine)")
    return out


def check_setup(project: Project, store: AgentStore) -> dict:
    """Whether the system can do what is asked of it: which machines fill the task system's roles
    (and which of them Orchestra does not run), the kind of work, the gate's tests, the capacity,
    and what the launcher runs."""
    raw = tomllib.loads(path(project).read_text(encoding="utf-8"))
    p, t, g = raw.get("project") or {}, raw.get("tasks") or {}, raw.get("gate") or {}
    procs = list_processes(project)
    fillers = Fillers(store.events)
    return {"ok": True,
            "work": p.get("work", "tasks") + (" (a branch per ticket, reviewed and landed through the merge gate)"
                                              if p.get("work") == "code" else
                                              " (a scratch folder per ticket; nothing a worker makes lands in the repository)"),
            "roles": fillers.lines(project.config.serves),
            "not_launched": _not_launched(project, fillers, procs),
            "gate_tests": [{"name": u.get("name"), "command": u.get("command")} for u in g.get("unit") or []],
            "capacity_limit": {"workers": t.get("workers", 1), "reviewers": t.get("reviewers", 1)},
            "launcher": procs["launcher"],
            "processes": [f"{x['name']}: {x['state']}" + (f", last exit {x['last_exit_code']}" if x["last_exit_code"] else "")
                          for x in procs["processes"]],
            "collectors": [f"{c['name']}: {c['last_log_line']}" for c in procs["collectors"]],
            "entries_left_out": procs["entries_left_out"],
            "config_can_change": why_not_committable(project) or "yes"}
