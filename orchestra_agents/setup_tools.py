"""The Supervisor's configuration tools: the setup of the project in ``orchestra.toml``, changed
one entry at a time, each change committed with its reason (``toml_edit.change``), announced on
the bus (``config.changed``) and answered with what applies now and what restarts.

What they set, and who reads it (none of it is implemented here; each reader re-reads the file):

- ``[agents] serves``: which of the task system's roles the AI agents fill. The agents' host reads
  it every second.
- ``[project] work`` (tasks | code), ``[[gate.unit]]`` (what a branch must pass to land) and
  ``[tasks] workers``/``reviewers`` (the most tickets offered at once): the task system's, read
  by its scheduler on every tick (its SPEC and ``tasking/config.py`` own their meaning).
- ``[[machines]]``, ``[[collect]]``, ``[[scrape]]``: Orchestra's launcher and collectors, which
  read the file again when it changes (Orchestra's SPEC §5.1 and §5.2): an entry added starts, one
  removed stops, a machine whose command or restart changed restarts, a collector changed starts
  over.

Only the Supervisor calls them (the human asks it in the chat; Orchestra's rule 9). They change
configuration, never code: a program or a collector script is a worker's, through a ticket."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import tomlkit
from orchestra import runtime
from orchestra.config import ConfigError, parse_duration

from . import prompting
from .builtins import PER_TICKET_ROLES
from .config import Project
from .fillers import KEEP_S, Fillers
from .gate_busy import landing
from .org_store import all_agents
from .restart import recover
from .store import AgentStore, StoreError
from .toml_edit import change, on_document
from .toml_entries import entries, remove, set_key, table, upsert

SETUP_COMMANDS = frozenset({"set_agent_roles", "set_work_mode", "set_gate_unit", "set_capacity_limit",
                            "set_machine", "set_collector", "remove_entry"})
SECTIONS = ("machines", "collect", "scrape", "gate.unit")
CHANGED = "config.changed"


@dataclass
class Plan:
    section: str                                  # what the bus event names
    edit: Callable[[str], tuple[str, str]]        # the file's text -> what it changed, the new text
    applies: Callable[[], str]                    # after the commit: what applies now, what restarts
    then: Callable[[], None] | None = None        # after the commit: what the agents do about it


def _duration(a: dict, key: str) -> str | None:
    v = a.get(key)
    if not v:
        return None
    try:
        parse_duration(v, 0.0)
    except ConfigError as e:
        raise StoreError(f"{key}: {e}; write a duration like 30s, 5m or 1h") from e
    return v


def _unless_launching(project: Project) -> str:
    if runtime.launcher(runtime.read(project.orchestra)) is not None:
        return ""
    return " No `orchestra run` launches this project's processes now, so it applies when the human starts it."


def _set_agent_roles(project: Project, store: AgentStore, a: dict) -> Plan:
    serves = list(dict.fromkeys(a.get("serves") or []))
    bad = [r for r in serves if r not in PER_TICKET_ROLES]
    if bad:
        raise StoreError(f"serves names {bad}; the roles the AI agents can fill are {', '.join(PER_TICKET_ROLES)}")
    others = [r for r in PER_TICKET_ROLES if r not in serves]

    @on_document
    def edit(doc):
        set_key(table(doc, "agents"), "serves", serves)
        return f"the AI agents fill {', '.join(serves) or 'no task role'} ([agents] serves)"

    def applies():
        f, now = Fillers(store.events), time.time()
        filled = {role for (role, inst), at in f.seen.items() if f.kinds.get(inst) != "agent" and now - at < KEEP_S}
        unfilled = [r for r in others if r not in filled]
        return ("Live: the agents' host reads [agents] every second; from now on it answers the task system's offers for "
                + (", ".join(serves) or "no role") + (f" and leaves {', '.join(others)} to other machines" if others else "")
                + ". Sessions already on a ticket finish. Nothing restarts."
                + (f" No other machine has been seen filling {', '.join(unfilled)}: offers for it wait until one does."
                   if unfilled else ""))
    return Plan("agents", edit, applies)


def _set_work_mode(project: Project, store: AgentStore, a: dict) -> Plan:
    work = a.get("work")
    if work not in ("tasks", "code"):
        raise StoreError("work is tasks (a scratch folder per ticket, nothing merged) or code (a branch per ticket, "
                         "reviewed and landed on the base branch through the merge gate)")
    residents = sorted(n for n, ag in all_agents(project).items() if ag.resident and any(
        (prompting.PROMPTS_DIR / f"{r}.tasks.md").exists() for r in ag.roles))     # their prompt depends on the mode

    @on_document
    def edit(doc):
        set_key(table(doc, "project"), "work", work)
        return f"work is {work} ([project] work)"

    def applies():
        no_gate = work == "code" and not entries(tomlkit.parse((project.repo_root / "orchestra.toml").read_text()), "gate.unit")
        return ("Live: the task system's scheduler reads [project] on every tick; tickets offered from now on get "
                + ("a branch, a review of its diff and the merge gate" if work == "code" else "a scratch folder, and are done when approved")
                + "; tickets already assigned keep what they have."
                + (f" The resident sessions of {', '.join(residents)} restart after their current turn, to take the "
                   f"prompts for {work} work." if residents else "")
                + (" There is no [[gate.unit]]: the gate lands a branch without running any test; set one with "
                   "set_gate_unit." if no_gate else ""))

    def then():
        for n in residents:
            store.set_agent_state(n, restart_requested=True)
    return Plan("project", edit, applies, then)


def _set_gate_unit(project: Project, store: AgentStore, a: dict) -> Plan:
    name, command = (a.get("name") or "unit").strip(), (a.get("command") or "").strip()
    if not command:
        raise StoreError("command is the shell command a branch must pass, run in the branch's checkout")
    fields: dict[str, Any] = {"command": command}
    if t := _duration(a, "timeout"):
        fields["timeout"] = t

    @on_document
    def edit(doc):
        how = "added" if upsert(doc, "gate.unit", name, fields) is None else "changed"
        return f"{how} the gate's test {name}: {command} ([[gate.unit]])"
    return Plan("gate.unit", edit, lambda: "Live: the task system's next gate run uses it (its scheduler reads [gate] on every tick).")


def _set_capacity_limit(project: Project, store: AgentStore, a: dict) -> Plan:
    given = {k: a[k] for k in ("workers", "reviewers") if a.get(k) is not None}
    if not given:
        raise StoreError("give workers, reviewers or both: the most tickets offered at once to each role")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in given.values()):
        raise StoreError("workers and reviewers are whole numbers, 0 or more")

    @on_document
    def edit(doc):
        t = table(doc, "tasks")
        for k, v in given.items():
            set_key(t, k, v)
        return "at most " + " and ".join(f"{v} {k}" for k, v in given.items()) + " at once ([tasks])"
    return Plan("tasks", edit, lambda: ("Live: the task system's scheduler reads [tasks] on every tick. A ceiling the operator "
                                        "set on the Dashboard wins over this one, and a planner's dial (set_capacity) stays at "
                                        "or below it."))


def _set_machine(project: Project, store: AgentStore, a: dict) -> Plan:
    name, command = (a.get("name") or "").strip(), (a.get("command") or "").strip()
    if not name or not command:
        raise StoreError("a machine needs a name and the command that runs it (a program in the repository)")
    fields: dict[str, Any] = {"command": command}
    for k in ("restart", "stop_grace"):
        if a.get(k) is not None:
            fields[k] = a[k]
    said: dict = {}

    @on_document
    def edit(doc):
        before = said["before"] = upsert(doc, "machines", name, fields)
        return f"{'added' if before is None else 'changed'} the machine {name}: {command} ([[machines]])"

    def applies():
        b = said["before"]
        if b is None:
            return "orchestra run starts it within a second." + _unless_launching(project)
        if b.get("command") != command or b.get("restart", "always") != fields.get("restart", b.get("restart", "always")):
            return "orchestra run restarts it within a second (its command or restart changed)." + _unless_launching(project)
        return "Nothing restarts: the new stop grace applies the next time it is stopped."
    return Plan("machines", edit, applies)


def _set_collector(project: Project, store: AgentStore, a: dict) -> Plan:
    name = (a.get("name") or "").strip()
    command, url = (a.get("command") or "").strip(), (a.get("url") or "").strip()
    if not name or bool(command) == bool(url):
        raise StoreError("a collector needs a name and either a command (a script under machines/ that prints Prometheus "
                         "text: [[collect]]) or a url that serves it ([[scrape]]), not both")
    section = "collect" if command else "scrape"
    fields: dict[str, Any] = {"command": command} if command else {"url": url}
    for k in ("every", "timeout"):
        if v := _duration(a, k):
            fields[k] = v
    said: dict = {}

    @on_document
    def edit(doc):
        before = said["before"] = upsert(doc, section, name, fields)
        return (f"{'added' if before is None else 'changed'} the collector {name}: {command or url}, every "
                f"{fields.get('every') or (before or {}).get('every') or '60s'} ([[{section}]])")

    def applies():
        what = "runs from now on, on its interval" if said["before"] is None else "starts over with the new entry"
        return (f"orchestra run reads it within a second: it {what}, storing what it prints with machine={name}; a "
                f"failed round says why in its log, logs/machines/{name}.log." + _unless_launching(project))
    return Plan(section, edit, applies)


def _remove_entry(project: Project, store: AgentStore, a: dict) -> Plan:
    section, name = a.get("section"), (a.get("name") or "").strip()
    if section not in SECTIONS:
        raise StoreError(f"section is one of {', '.join(SECTIONS)}")

    def edit(text):
        return f"removed {name} from [[{section}]]", remove(text, section, name)
    said = {"machines": "orchestra run stops it within a second.",
            "collect": "orchestra run stops running it; a round under way finishes and its points are dropped.",
            "scrape": "orchestra run stops reading it; a program it read is not stopped by this.",
            "gate.unit": "Live: the task system's next gate run leaves it out."}[section]
    return Plan(section, edit, lambda: said + ("" if section == "gate.unit" else _unless_launching(project)))


HANDLERS: dict[str, Callable[[Project, AgentStore, dict], Plan]] = {
    "set_agent_roles": _set_agent_roles, "set_work_mode": _set_work_mode, "set_gate_unit": _set_gate_unit,
    "set_capacity_limit": _set_capacity_limit, "set_machine": _set_machine, "set_collector": _set_collector,
    "remove_entry": _remove_entry}


def handle_setup(project: Project, store: AgentStore, command: str, a: dict[str, Any], author: str, role: str) -> dict:
    if role != "supervisor":
        raise StoreError("only the supervisor changes orchestra.toml")
    recovered = recover(project)                  # an entry an interrupted restart left out
    plan = HANDLERS[command](project, store, a)
    if why := landing(store, role):
        raise StoreError(why)
    done = change(project, plan.edit, a.get("reason") or "")
    if recovered:
        done["recovered"] = recovered
    if done["commit"] is None:
        return {"ok": True, **done, "applies": "Nothing changed, so nothing applies or restarts."}
    if plan.then:
        plan.then()
    applies = plan.applies()
    store.events.emit(CHANGED, section=plan.section, change=done["changed"], reason=a["reason"].strip(),
                      commit=done["commit"], applies=applies, by=author)
    return {"ok": True, **done, "applies": applies,
            "next": "Tell the human what you changed and why, the commit, and what applies now."}
