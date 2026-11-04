"""``restart_process <name>``: the Supervisor restarts one of the launcher's processes by its name in
Orchestra's runtime record (a project's ``[[machines]]`` program, or a plugin's process such as
``outbox.sender``), after the cause of its crashes is fixed (T2k s16: the Outbox sender crashed five
times on a configuration the Supervisor had broken; it put the keys back and then said it could not
restart the sender, and SCRUM-47 waited at ``close_ticket``).

It goes through Orchestra, never a signal or a file of the launcher's: when Orchestra's runtime has
a restart call (``runtime.request_restart(project, name, by=, reason=)``, which the launcher acts on
at its next tick: it stops the process if it runs, forgets its crashes and starts it again) that
call is used; until then a project's program is restarted the one way Orchestra's launcher allows,
``restart_machine``'s (its entry out of the owner's overlay and back, ``restart.py``), and a
plugin's process is refused, saying that it starts again when ``orchestra run`` restarts. Either way
the restart is the Supervisor's act on the bus (``process.restarted`` with the reason, or
``machine.restarted``), and in its ledger."""

from __future__ import annotations

import time

from orchestra import runtime

from .config import Project
from .restart import SETTLE_S, restart_machine
from .store import AgentStore, StoreError

RESTARTED = "process.restarted"


def _procs(project: Project) -> dict:
    return runtime.read(project.orchestra).get("processes") or {}


def _started_again(project: Project, name: str, old_pid, timeout: float) -> dict:
    end = time.time() + timeout
    while True:
        r = _procs(project).get(name) or {}
        if r.get("pid") not in (None, old_pid) or time.time() >= end:
            return r
        time.sleep(0.2)


def restart_process(project: Project, store: AgentStore, a: dict, author: str, role: str) -> dict:
    if role != "supervisor":
        raise StoreError("only the supervisor restarts a process")
    name, reason = (a.get("name") or "").strip(), (a.get("reason") or "").strip()
    if len(reason) < 10:
        raise StoreError("give the reason for the restart in a sentence (what caused the crashes and how it was fixed): "
                         "it goes on the bus")
    rec = runtime.read(project.orchestra)
    if runtime.launcher(rec) is None:
        raise StoreError("no `orchestra run` launches this project's processes now, so there is nothing to restart; "
                         "the human starts `orchestra run` in the repository")
    procs = rec.get("processes") or {}
    if name not in procs:
        raise StoreError(f"the launcher has no process named {name!r}; its processes are: {', '.join(sorted(procs)) or 'none'}")
    r = procs[name]
    owner = r.get("owner") or "project"
    ask = getattr(runtime, "request_restart", None)
    if callable(ask):
        old = r.get("pid")
        ask(project.orchestra, name, by=author, reason=reason)
        new = _started_again(project, name, old, float(r.get("stop_grace_s") or 10.0) + SETTLE_S)
        store.events.emit(RESTARTED, name=name, owner=owner, reason=reason, by=author, pid_before=old, pid=new.get("pid"))
        up = new.get("pid") not in (None, old)
        return {"ok": True, "restarted": name, "pid_before": old, "pid": new.get("pid"), "state": new.get("state"),
                "said": (f"{name} was started again by orchestra run (pid {old} -> {new.get('pid')})." if up else
                         f"orchestra run was asked to restart {name}; it had not started it within {SETTLE_S:g} s "
                         f"(state {new.get('state') or 'not recorded'}). Read its log with read_machine_log.")}
    if owner == "project":
        return restart_machine(project, store, {"name": name, "reason": reason}, author, role)
    raise StoreError(f"{name} is the {owner} plugin's process, and Orchestra's launcher has no call yet to restart one "
                     "(asked of Orchestra's coordinator); it is tried again when `orchestra run` restarts, which the "
                     f"owner does, or when a setting it reads changes. Tell the owner that {name} needs `orchestra run` "
                     "restarted, and why it is safe now")
