"""Restarting one of the project's programs (a ``[[machines]]`` entry) the way Orchestra's
launcher allows: it has no restart command, and it stops a machine whose entry is removed from
``orchestra.toml`` and starts one whose entry is added (Orchestra's SPEC §5.1). So the entry is
taken out, the launcher is watched (its runtime record) until it has stopped the program, and the
file is put back exactly as it was, which starts it again; a program it had given up on is tried
again. Nothing is committed, because the file ends as it began; the restart is announced on the
bus (``machine.restarted``).

A plugin's process (``tasks.scheduler``, ``agents.host``) is not the project's entry and is not
restarted here: it restarts with ``orchestra run``. A collector needs no restart: it runs on its
interval, and a changed entry starts it over."""

from __future__ import annotations

import time

import tomlkit
from orchestra import runtime

from .config import Project
from .store import AgentStore, StoreError
from .toml_edit import path, write

RESTARTED = "machine.restarted"
SETTLE_S = 10.0          # beyond a program's stop grace: how long the launcher may take to notice and act


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
    old_pid = rec.get("pid")
    del entries[i]
    without = tomlkit.dumps(doc)
    write(project, without)
    try:
        stopped = _wait(lambda: name not in _record(project), float(rec.get("stop_grace_s") or 10.0) + SETTLE_S)
    finally:
        if path(project).read_text(encoding="utf-8") == without:
            write(project, before)
        else:
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
