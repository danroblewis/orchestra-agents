"""``machine_ages``: each machine heard on the bus, in its age (the guide's "A machine's three
ages"), and the counts. Read only.

- **agent**: it announced its kind as ``agent`` (an AI agent machine: a prompt, tools, its events);
- **program in .orchestra/**: a program whose ``[[machines]]`` command runs a file under the
  project's ``.orchestra/`` (or whose directory ``.orchestra/machines/<id>/`` the project holds), being
  proven there;
- **graduated**: a program whose ``[[machines]]`` command is the project's own, outside
  ``.orchestra/`` (``npm run relator``, ``python3 -m acme.relator``);
- **unknown**: a program the project holds no file or command for (it runs elsewhere, or a plugin
  runs it); a person or a service is none of these (``other``).

**How a machine is joined to its ``[[machines]]`` entry**, since an announcement does not name the
launcher's process: by the log it announced when that is the launcher's log for an entry
(``<state>/logs/machines/<entry>.log``, which the launcher gives each process as
``ORCHESTRA_MACHINE_LOG``), else by the entry's name being the machine's id, else by the entry's
command naming ``.orchestra/machines/<id>/``. A machine joined by none of them is said so.

**Due to graduate**: a program in ``.orchestra/`` first heard ``DUE_AFTER_DAYS`` ago or more, in the
events read (the newest ``machines_read.SCAN``; an older start is not seen, so the age is a lower
bound). Whether an agent is a program waiting to be written (its last ten runs took the same
steps) is not measured: the answer says so rather than guess."""

from __future__ import annotations

import shlex
import time
from pathlib import Path

from . import machines_read
from .bus_reads import recent
from .config import Project
from .store import AgentStore

DUE_AFTER_DAYS = 7
ANNOUNCED = "orchestra.machine.started"
AGES = ("agent", "program in .orchestra/", "graduated", "unknown")
NOT_MEASURED = ("not measured: whether an agent's last ten runs took the same steps (a program waiting to be written) "
                "is not computed yet")


def entries(project: Project) -> dict[str, str]:
    """The project's ``[[machines]]`` (orchestra.toml with the owner's overlay): name -> command."""
    raw = project.orchestra.config.raw or {}
    return {e["name"]: str(e.get("command") or "") for e in raw.get("machines") or []
            if isinstance(e, dict) and e.get("name") and e.get("enabled") is not False}


def paths_in(project: Project, command: str) -> list[Path]:
    """The paths a command names that exist in the project, resolved against the repository."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    out = []
    for w in words:
        p = Path(w).expanduser()
        p = p if p.is_absolute() else project.repo_root / p
        if p.exists():
            out.append(p.resolve())
    return out


def in_orchestra(project: Project, command: str) -> bool:
    dot = (project.repo_root / ".orchestra").resolve()
    return any(p == dot or dot in p.parents for p in paths_in(project, command))


def announced_logs(store: AgentStore) -> dict[str, str]:
    """machine id -> the log path its instances last announced (the events read flat: ``data`` merged in)."""
    out: dict[str, str] = {}
    for e in recent(store, machines_read.SCAN):
        log = e.get("log", (e.get("data") or {}).get("log") if isinstance(e.get("data"), dict) else None)
        if e.get("type") == ANNOUNCED and isinstance(log, str) and log:
            out[str(e.get("source") or "").split("/")[0]] = log
    return out


def entry_of(project: Project, mid: str, log: str | None, ents: dict[str, str]) -> tuple[str | None, str]:
    """The ``[[machines]]`` entry that runs machine ``mid``, and how it was joined."""
    logs = (project.orchestra.state_dir / "logs" / "machines").resolve()
    if log:
        p = Path(log).expanduser().resolve()
        if p.parent == logs and p.stem in ents:
            return p.stem, "by the log it announced"
    if mid in ents:
        return mid, "by name"
    mdir = (project.repo_root / ".orchestra" / "machines" / mid).resolve()
    for n, cmd in ents.items():
        if any(p == mdir or mdir in p.parents for p in paths_in(project, cmd)):
            return n, f"by its command naming .orchestra/machines/{mid}/"
    return None, ""


def age_of(project: Project, m: dict, log: str | None, ents: dict[str, str]) -> dict:
    mid, kind = m["id"], str(m.get("kind") or "")
    row = {"id": mid, "kind": kind or None}
    if "agent" in kind:
        return {**row, "age": "agent"}
    if kind in ("person", "service"):
        return {**row, "age": "other"}
    name, how = entry_of(project, mid, log, ents)
    if name is not None:
        cmd = ents[name]
        age = "program in .orchestra/" if in_orchestra(project, cmd) else "graduated"
        return {**row, "age": age, "entry": name, "command": cmd, "joined": how}
    if (project.repo_root / ".orchestra" / "machines" / mid).is_dir():
        return {**row, "age": "program in .orchestra/", "dir": f".orchestra/machines/{mid}/",
                "joined": "by its directory (no [[machines]] entry runs it: a workflow's program, or nothing yet)"}
    return {**row, "age": "unknown", "joined": "it announces from no file the project holds (it runs elsewhere, or a plugin runs it)"}


def due(m: dict, row: dict, now: float) -> bool:
    first = m.get("first_seen")
    return row["age"] == "program in .orchestra/" and first is not None and now - float(first) >= DUE_AFTER_DAYS * 86_400


def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def line(counts: dict, due_ids: list[str]) -> str:
    said = ", ".join(x for x in (_n(counts["agent"], "agent", "agents"),
                                 _n(counts["program in .orchestra/"], "program in .orchestra/", "programs in .orchestra/"),
                                 _n(counts["graduated"], "graduated program", "graduated programs"),
                                 f"{counts['unknown']} unknown" if counts["unknown"] else "") if x)
    return (f"Machines by age: {said}; due to graduate: {', '.join(due_ids) or 'none'}; agents due to become programs: "
            "not measured.")


def machine_ages(project: Project, store: AgentStore, a: dict | None = None) -> dict:
    v = machines_read.view(store)
    if v.get("ok") is False:
        return {"ok": False, "error": v.get("error") or "the machines view did not answer"}
    ents, logs, now = entries(project), announced_logs(store), time.time()
    rows, due_ids = [], []
    for m in v.get("machines") or []:
        if m.get("bootstrap"):
            continue
        r = age_of(project, m, logs.get(m["id"]), ents)
        if due(m, r, now):
            r["due_to_graduate"] = True
            due_ids.append(m["id"])
        rows.append(r)
    counts = {k: sum(r["age"] == k for r in rows) for k in (*AGES, "other")}
    programs = counts["program in .orchestra/"] + counts["graduated"]
    share = (f"{counts['program in .orchestra/']} of {programs} programs live in .orchestra/"
             + (": the majority, so the system lives mostly in .orchestra/; graduate the proven ones (graduate_machine)"
                if programs and counts["program in .orchestra/"] * 2 > programs else ""))
    return {"ok": True, "machines": rows, "counts": counts, "due_to_graduate": due_ids, "share": share,
            "agents_due_to_become_programs": NOT_MEASURED, "line": line(counts, due_ids),
            "note": (f"due to graduate: a program in .orchestra/ first heard {DUE_AFTER_DAYS} days ago or more in the events "
                     "read (a lower bound); graduate_machine with dry_run first shows where it goes and its command. Say "
                     "the line as it is when the owner asks how the system is made")}
