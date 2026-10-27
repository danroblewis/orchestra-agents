"""Restarting one of the project's programs (a ``[[machines]]`` entry) the way Orchestra's
launcher allows: it has no restart command, and it stops a machine whose entry is removed from
``orchestra.toml`` and starts one whose entry is added (Orchestra's SPEC §5.1). So the entry is
taken out, the launcher is watched (its runtime record) until it has stopped the program, and the
file is put back exactly as it was, which starts it again; a program it had given up on is tried
again. Nothing is committed, because the file ends as it began; the restart is announced on the
bus (``machine.restarted``).

Putting the entry back must survive the agents being killed in between (a session's timeout, an
interrupt, the host stopped): otherwise the program stays stopped and the uncommitted deletion
makes every later setup change refuse ("someone is editing it"). So before the file is written
without the entry, a record of the restart (the file before and without it, the name, the time)
is saved in the agents' state directory (``pending-restart.json``) and dropped once the file is
back. ``recover``, run at the start of every setup tool and of ``restart_machine`` and when the
agents' host starts, puts the file back when a record is left and the file is still the text
without the entry, then drops the record.

A plugin's process (``tasks.scheduler``, ``agents.host``) is not the project's entry and is not
restarted here: it restarts with ``orchestra run``. A collector needs no restart: it runs on its
interval, and a changed entry starts it over. Nothing restarts while the task system's gate is
landing a branch (``gate_busy``): the edited file would make the landing fail."""

from __future__ import annotations

import json
import os
import time

import tomlkit
from orchestra import runtime

from .config import Project
from .gate_busy import landing
from .store import AgentStore, StoreError
from .toml_edit import path, write

RESTARTED = "machine.restarted"
SETTLE_S = 10.0          # beyond a program's stop grace: how long the launcher may take to notice and act


def _pending(project: Project):
    return project.state_dir / "pending-restart.json"


def _save_pending(project: Project, name: str, before: str, without: str) -> None:
    p = _pending(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"name": name, "before": before, "without": without, "at": time.time()}), encoding="utf-8")
    os.replace(tmp, p)


def _drop_pending(project: Project) -> None:
    _pending(project).unlink(missing_ok=True)


def recover(project: Project) -> str | None:
    """Put back an entry a restart took out and never restored (the agents were killed while
    waiting), when the file is still as the restart left it; drop the record either way. Says what
    it did, or None when there was nothing to do."""
    p = _pending(project)
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        _drop_pending(project)
        return None
    try:
        now = path(project).read_text(encoding="utf-8")
    except OSError:
        return None
    name = rec.get("name")
    if now == rec.get("without") and isinstance(rec.get("before"), str):
        write(project, rec["before"])
        _drop_pending(project)
        return f"put back the [[machines]] entry {name}, which a restart interrupted before it was restored"
    _drop_pending(project)
    return None


def _record(project: Project) -> dict:
    return runtime.read(project.orchestra).get("processes") or {}


def _wait(until, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if until():
            return True
        time.sleep(0.2)
    return until()


def restart_machine(project: Project, store: AgentStore, a: dict, author: str, role: str) -> dict:
    if role != "supervisor":
        raise StoreError("only the supervisor restarts a machine")
    name, reason = (a.get("name") or "").strip(), (a.get("reason") or "").strip()
    if len(reason) < 10:
        raise StoreError("give the reason for the restart in a sentence: it goes on the bus")
    recover(project)
    if runtime.launcher(runtime.read(project.orchestra)) is None:
        raise StoreError("no `orchestra run` launches this project's processes now, so there is nothing to restart; "
                         "the human starts `orchestra run` in the repository, which starts every [[machines]] entry")
    before = path(project).read_text(encoding="utf-8")
    doc = tomlkit.parse(before)
    entries = doc.get("machines") or []
    i = next((k for k, e in enumerate(entries) if isinstance(e, dict) and e.get("name") == name), None)
    rec = _record(project).get(name) or {}
    if i is None:
        if rec.get("owner") and rec["owner"] != "project":
            raise StoreError(f"{name} is the {rec['owner']} plugin's process, not a [[machines]] entry: it restarts when "
                             "`orchestra run` restarts, which the human does")
        if any(isinstance(e, dict) and e.get("name") == name for k in ("collect", "scrape") for e in doc.get(k) or []):
            raise StoreError(f"{name} is a collector: it runs on its interval by itself, and set_collector with a changed "
                             "entry starts it over")
        names = [e.get("name") for e in entries if isinstance(e, dict)]
        raise StoreError(f"there is no [[machines]] entry named {name!r}; the entries are: {', '.join(names) or 'none'}")
    if why := landing(store, role):
        raise StoreError(why)
    old_pid = rec.get("pid")
    del entries[i]
    without = tomlkit.dumps(doc)
    _save_pending(project, name, before, without)
    write(project, without)
    try:
        stopped = _wait(lambda: name not in _record(project), float(rec.get("stop_grace_s") or 10.0) + SETTLE_S)
    finally:
        restored = path(project).read_text(encoding="utf-8") == without
        if restored:
            write(project, before)
        _drop_pending(project)
        if not restored:
            raise StoreError(f"orchestra.toml was changed by someone else while {name} was being restarted, so its entry "
                             "was not put back; read the file (read_config) and add the entry again with set_machine")
    if not stopped:
        raise StoreError(f"the launcher did not stop {name} within its stop grace; its entry is back as it was. Read its "
                         "log (read_machine_log) and the processes (list_processes)")
    started = _wait(lambda: (r := _record(project).get(name)) is not None and r.get("pid") not in (None, old_pid), SETTLE_S)
    new = _record(project).get(name) or {}
    store.events.emit(RESTARTED, name=name, reason=reason, by=author, pid_before=old_pid, pid=new.get("pid"))
    return {"ok": True, "restarted": name, "pid_before": old_pid, "pid": new.get("pid"), "state": new.get("state"),
            "said": (f"{name} was stopped and started again by orchestra run (pid {old_pid} -> {new.get('pid')})." if started else
                     f"{name} was stopped and its entry put back; orchestra run had not started it again within "
                     f"{SETTLE_S:g} s (state {new.get('state') or 'not recorded'}). Read its log with read_machine_log.")}
