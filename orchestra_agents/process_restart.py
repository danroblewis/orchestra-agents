"""``restart_process <name>``: the Supervisor restarts one of the launcher's processes by its name in
Orchestra's runtime record (a project's ``[[machines]]`` program, or a plugin's process such as
``outbox.sender``), after the cause of its crashes is fixed (T2k s16: the Outbox sender crashed five
times on a configuration the Supervisor had broken; it put the keys back and then said it could not
restart the sender, and SCRUM-47 waited at ``close_ticket``).

It goes through Orchestra, never a signal or a file of the launcher's: the request is recorded with
``runtime.request_restart(project, name, by="supervisor", reason=)``, and at its next tick the
launcher stops the process if it runs, forgets its crashes, starts it again and publishes
``orchestra.process.restart_requested``. A ``[[machines]]`` program is a launcher process like a
plugin's, so the one call serves both. The tool does not wait for the tick: it says the restart is
requested. The request is the Supervisor's act on the bus (``process.restart_requested`` with the
reason) and in its ledger; a refusal (no ``orchestra run``, no such process) is said in the tool's
words."""

from __future__ import annotations

from orchestra import runtime

from .config import Project
from .store import AgentStore, StoreError

REQUESTED = "process.restart_requested"


def _refused(project: Project, name: str, why: ValueError) -> StoreError:
    rec = runtime.read(project.orchestra)
    if runtime.launcher(rec) is None:
        return StoreError("no `orchestra run` launches this project's processes now, so there is nothing to restart; "
                          "the human starts `orchestra run` in the repository")
    procs = rec.get("processes") or {}
    if name not in procs:
        return StoreError(f"the launcher has no process named {name!r}; its processes are: {', '.join(sorted(procs)) or 'none'}")
    return StoreError(f"Orchestra refused the restart of {name}: {why}")


def restart_process(project: Project, store: AgentStore, a: dict, author: str, role: str) -> dict:
    if role != "supervisor":
        raise StoreError("only the supervisor restarts a process")
    name, reason = (a.get("name") or "").strip(), (a.get("reason") or "").strip()
    if len(reason) < 10:
        raise StoreError("give the reason for the restart in a sentence (what caused the crashes and how it was fixed): "
                         "it goes on the bus")
    try:
        runtime.request_restart(project.orchestra, name, by="supervisor", reason=reason)
    except ValueError as e:
        raise _refused(project, name, e) from None
    r = (runtime.read(project.orchestra).get("processes") or {}).get(name) or {}
    owner = r.get("owner") or "project"
    store.events.emit(REQUESTED, name=name, owner=owner, reason=reason, by=author, pid=r.get("pid"))
    whose = "a program of the project's" if owner == "project" else f"the {owner} plugin's process"
    return {"ok": True, "requested": name, "owner": owner, "pid": r.get("pid"), "state": r.get("state"),
            "said": (f"The restart of {name} ({whose}) is requested: orchestra run stops it if it runs, forgets its "
                     "crashes and starts it again at its next tick, a second or so from now. Check it with "
                     "list_processes, and read its log with read_machine_log if it stops again.")}
