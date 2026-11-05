"""``machine_ages``: each machine heard on the bus, in its age (the guide's "A machine's three
ages"), and the counts. Read only.

- **agent**: it announced its kind as ``agent`` (an AI agent machine: a prompt, tools, its events);
- **program in .orchestra/**: a program whose ``[[machines]]`` command runs a file under the
  project's ``.orchestra/``, being proven there;
- **graduated**: a program whose ``[[machines]]`` command is the project's own, outside
  ``.orchestra/`` (``npm run relator``, ``python3 -m acme.relator``);
- **unknown**: a program the project holds no file or command for (it runs elsewhere, or a plugin
  runs it); a person or a service is none of these (``other``).

**A machine joined to no entry, whose directory ``.orchestra/machines/<id>/`` the project holds**
(a workflow's program, started by the Workflows plugin's program host) is in the age its directory
says (``by_dir``), never the middle age for the directory alone: a ``machine.json`` of kind
``universal`` or ``agent``, or an agent spec (``prompt``/``tools`` in its ``spec``, or a
``prompt.md``/``tools.*`` file) is an **agent**; a ``spec.command`` that runs a file under
``.orchestra/`` is a **program in .orchestra/**; a command into the repository's tree (``npm run …``,
``cargo run …``, ``python -m …``, ``./scripts/…``: anything that resolves outside ``.orchestra/``,
run from the repository root) is **graduated**, the directory holding only the declaration; a
``machine.json`` with no command beside the program's own files is a program there; no
``machine.json``, no spec and no program file is **unknown**. Every row says why in ``reason``.

**How a machine is joined to its ``[[machines]]`` entry**: by the launcher's process that started
it, first (the machines view's ``processes``, from each announcement's ``process``, which the
launcher gives every process it starts as ``ORCHESTRA_PROCESS``): a name the project's
``[[machines]]`` holds is that entry, and the machine is in the age its command says (graduated
when it runs outside ``.orchestra/``); a ``<plugin>.<name>`` is a plugin's process, which is never a
machine's age (the provider is never the machine, Orchestra's AGENTS.md rule 2), so the machine is
joined to no entry by it. An announcement without ``process`` (a machine the launcher did not start,
or one on an older core) falls back to the log it announced when that is the launcher's log for an
entry (``<state>/logs/machines/<entry>.log``, ``ORCHESTRA_MACHINE_LOG``), else the entry's name being
the machine's id, else the entry's command naming ``.orchestra/machines/<id>/``; the row says it fell
back (``by: fallback``) and the answer lists them (``fell_back``). A machine joined by none is said so.

**Due to graduate**: a program in ``.orchestra/`` first heard ``DUE_AFTER_DAYS`` ago or more, in the
events read (the newest ``machines_read.SCAN``; an older start is not seen, so the age is a lower
bound). Whether an agent is a program waiting to be written (its last ten runs took the same
steps) is not measured: the answer says so rather than guess."""

from __future__ import annotations

import json
import shlex
import time
from pathlib import Path

from . import machines_read, people
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
    """Whether the command runs something under the project's ``.orchestra/``: a path it names that
    exists there, or a word that names ``.orchestra/`` (a file not written yet is still there)."""
    dot = (project.repo_root / ".orchestra").resolve()
    if any(p == dot or dot in p.parents for p in paths_in(project, command)):
        return True
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    return any(w.startswith(".orchestra/") or "/.orchestra/" in w for w in words)


def command_age(project: Project, command: str) -> tuple[str, str]:
    if in_orchestra(project, command):
        return "program in .orchestra/", f"its command runs a file under .orchestra/ ({command})"
    return "graduated", f"its command is the project's own, outside .orchestra/ ({command})"


AGENT_KINDS = ("universal", "agent")
AGENT_SPEC = ("prompt", "tools")
DECLARATION_FILES = ("machine.json", "AGENTS.md", "README.md")


def _agent_files(mdir: Path) -> list[str]:
    return sorted(f.name for f in mdir.iterdir() if f.is_file() and (f.name == "prompt.md" or f.stem == "tools"))


def _program_files(mdir: Path) -> list[str]:
    return sorted(str(f.relative_to(mdir)) for f in mdir.rglob("*")
                  if f.is_file() and f.name not in DECLARATION_FILES and "__pycache__" not in f.parts)


def dir_age(project: Project, mid: str) -> tuple[str, str] | None:
    """The age ``.orchestra/machines/<mid>/`` says, and why in words; None when there is no such
    directory. Decided by what its ``machine.json`` declares and what the directory holds."""
    mdir = project.repo_root / ".orchestra" / "machines" / mid
    if not mdir.is_dir():
        return None
    where = f".orchestra/machines/{mid}/"
    f = mdir / "machine.json"
    body: dict | None = None
    if f.is_file():
        try:
            body = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return "unknown", f"{where}machine.json cannot be read ({e})"
        if not isinstance(body, dict):
            return "unknown", f"{where}machine.json is not an object"
    spec = body.get("spec") if body and isinstance(body.get("spec"), dict) else {}
    kind = str((body or {}).get("kind") or "")
    if kind in AGENT_KINDS:
        return "agent", f"{where}machine.json declares it {kind}: an agent spec"
    if (keys := [k for k in AGENT_SPEC if spec.get(k)]) or (files := _agent_files(mdir)):
        said = f"its spec has {' and '.join(keys)}" if keys else f"{where} holds {', '.join(files)}"
        return "agent", f"{said}: an agent spec"
    cmd = spec.get("command")
    if isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd):
        cmd = shlex.join(cmd)
    if isinstance(cmd, str) and cmd.strip():
        age, why = command_age(project, cmd)
        if age == "graduated":
            why += f"; {where} holds only its declaration"
        return age, f"{where}machine.json: {why}"
    if code := _program_files(mdir):
        what = "machine.json names no command" if body is not None else "no machine.json"
        return "program in .orchestra/", f"{where} holds the program ({', '.join(code[:3])}); {what}"
    if body is not None:
        return "unknown", f"{where}machine.json names no command and no agent spec, and the directory holds no program"
    return "unknown", f"{where} holds no machine.json, no agent spec and no program"


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


def by_process(m: dict, ents: dict[str, str]) -> tuple[str | None, list[str]]:
    """The ``[[machines]]`` entry the launcher started machine ``m`` as, and the plugins' processes
    among its ``processes`` (``<plugin>.<name>``: never an entry, never a machine's age)."""
    procs = [p for p in m.get("processes") or [] if isinstance(p, str) and p]
    entry = next((p for p in procs if p in ents), None)
    return entry, [p for p in procs if p not in ents and "." in p]


def age_of(project: Project, m: dict, log: str | None, ents: dict[str, str]) -> dict:
    mid, kind = m["id"], str(m.get("kind") or "")
    row = {"id": mid, "kind": kind or None}
    if "agent" in kind:
        return {**row, "age": "agent", "reason": "it announced its kind as agent"}
    if kind in ("person", "service"):
        return {**row, "age": "other", "reason": f"it announced its kind as {kind}"}
    name, plugins = by_process(m, ents)
    if name is not None:
        how, by = f"by the launcher's process: [[machines]] {name}", "process"
    elif plugins:
        how, by = "", "process"
        row["plugin_process"] = plugins[0]
    else:                     # no process, or one the configuration no longer holds: the old matching
        name, how = entry_of(project, mid, log, ents)
        by, why = "fallback", (f"its process {', '.join(m['processes'])} is no [[machines]] entry now"
                               if m.get("processes") else "its announcement names no process")
        how = how and f"{how} (fallback: {why})"
    row["by"] = by
    if name is not None:
        cmd = ents[name]
        age, reason = command_age(project, cmd)
        return {**row, "age": age, "entry": name, "command": cmd, "joined": how,
                "reason": f"[[machines]] {name}: {reason}"}
    said = dir_age(project, mid)
    if said is not None:
        age, reason = said
        started = (f"started by the plugin's process {plugins[0]}, not a [[machines]] entry" if plugins
                   else "no [[machines]] entry runs it: a workflow's program, or nothing yet")
        return {**row, "age": age, "dir": f".orchestra/machines/{mid}/", "by_dir": True,
                "joined": f"by its directory ({started})", "reason": reason}
    if plugins:
        why = f"started by the plugin's process {plugins[0]}: a plugin runs it (the provider is never the machine)"
        return {**row, "age": "unknown", "joined": why, "reason": why}
    why = "it announces from no file the project holds (it runs elsewhere, or a plugin runs it)"
    return {**row, "age": "unknown", "joined": why, "reason": why}


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
    rows, due_ids, fell_back = [], [], []
    shown, big = people.split([m for m in v.get("machines") or [] if not m.get("bootstrap")])
    for m in shown:
        r = age_of(project, m, logs.get(m["id"]), ents)
        if r.get("by") == "fallback":
            fell_back.append(m["id"])
        if due(m, r, now):
            r["due_to_graduate"] = True
            due_ids.append(m["id"])
        rows.append(r)
    counts = {k: sum(r["age"] == k for r in rows) for k in (*AGES, "other")}
    counts["other"] += sum(len(ms) for ms in big.values())      # a relayed system's people: announced kind person
    groups = [{"group": g["group"], "people": g["people"], "age": "other",
               "reason": f"{g['people']} people announced their kind as person ({g['pattern']}); list_machines with "
                         f"people=\"{g['group']}\" names them"} for g in people.summaries(big)]
    programs = counts["program in .orchestra/"] + counts["graduated"]
    share = (f"{counts['program in .orchestra/']} of {programs} programs live in .orchestra/"
             + (": the majority, so the system lives mostly in .orchestra/; graduate the proven ones (graduate_machine)"
                if programs and counts["program in .orchestra/"] * 2 > programs else ""))
    return {"ok": True, "machines": rows, **({"people": groups} if groups else {}), "counts": counts, "due_to_graduate": due_ids, "share": share,
            "agents_due_to_become_programs": NOT_MEASURED, "line": line(counts, due_ids),
            "fell_back": fell_back,
            "note": (f"due to graduate: a program in .orchestra/ first heard {DUE_AFTER_DAYS} days ago or more in the events "
                     "read (a lower bound); graduate_machine with dry_run first shows where it goes and its command. Say "
                     "the line as it is when the owner asks how the system is made"
                     + (f"; joined without the launcher's process (by log, name or directory: the fallback): "
                        f"{', '.join(fell_back)}" if fell_back else ""))}
