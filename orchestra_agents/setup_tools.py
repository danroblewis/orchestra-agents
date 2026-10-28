"""The Supervisor's configuration tools: the setup of the project, changed one entry at a time
through Orchestra's own objects (``project.orchestra.config_edit``; ``config_change.change``),
announced on the bus (``config.changed``) and answered with what applies now and what restarts.

Each tool takes a ``target``: ``repo`` (the default), the repository's ``orchestra.toml``, where
the project's settings go, each change committed with its reason; or ``overlay``, the owner's
private overlay outside the repository (never committed), where their own or their company's
settings go: identities, channels, templates. ``set_setting`` sets any key, and must be told which.

What they set, and who reads it (none of it is implemented here; each reader reads the
configuration again through Orchestra):

- ``[agents] serves``: which of the task system's roles the AI agents fill. The agents' host reads
  it every second.
- ``[project] work`` (tasks | code), ``[[gate.unit]]`` (what a branch must pass to land) and
  ``[tasks] workers``/``reviewers`` (the most tickets offered at once): the task system's, read
  by its scheduler on every tick (its SPEC and ``tasking/config.py`` own their meaning).
- ``[[machines]]``, ``[[collect]]``, ``[[scrape]]``: Orchestra's launcher and collectors, which
  read the configuration again when either file changes (Orchestra's SPEC §5.1 and §5.2): an entry
  added starts, one removed stops, a machine whose command or restart changed restarts, a
  collector changed starts over.

Only the Supervisor calls them (the human asks it in the chat; Orchestra's rule 9). They change
configuration, never code: a program or a collector script is a worker's, through a ticket."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from orchestra import runtime
from orchestra.config import ConfigError, parse_duration
from orchestra.config_edit import Change

from . import prompting
from .builtins import PER_TICKET_ROLES
from .config import Project
from .config_change import Edit, change, target_of
from .fillers import KEEP_S, Fillers
from .gate_busy import landing
from .modeling import claim_change, own_prediction
from .org_store import all_agents
from .restart import recover
from .store import AgentStore, StoreError

SETUP_COMMANDS = frozenset({"set_agent_roles", "set_work_mode", "set_gate_unit", "set_capacity_limit",
                            "set_machine", "set_collector", "remove_entry", "set_setting"})
SECTIONS = ("machines", "collect", "scrape", "gate.unit")
CHANGED = "config.changed"


@dataclass
class Plan:
    section: str                                  # what the bus event names
    edit: Edit                                    # Orchestra's editor and its arguments -> the change
    what: Callable[[Change], str]                 # the change -> one line saying what it does (the commit's title)
    applies: Callable[[], str]                    # after it is made: what applies now, what restarts
    then: Callable[[], None] | None = None        # after it is made: what the agents do about it


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


def _entry(project: Project, section: str, name: str) -> dict | None:
    """The entry ``name`` of ``[[section]]`` in the configuration as it is (the overlay merged)."""
    v: Any = project.orchestra.config.raw
    for k in section.split("."):
        v = v.get(k) if isinstance(v, dict) else None
    return next((dict(e) for e in v or [] if isinstance(e, dict) and e.get("name") == name), None) if isinstance(v, list) else None


def _how(c: Change) -> str:
    return "added" if c.what.startswith("added") else "changed"


def _set_agent_roles(project: Project, store: AgentStore, a: dict) -> Plan:
    serves = list(dict.fromkeys(a.get("serves") or []))
    bad = [r for r in serves if r not in PER_TICKET_ROLES]
    if bad:
        raise StoreError(f"serves names {bad}; the roles the AI agents can fill are {', '.join(PER_TICKET_ROLES)}")
    others = [r for r in PER_TICKET_ROLES if r not in serves]

    def applies():
        f, now = Fillers(store.events), time.time()
        filled = {role for (role, inst), at in f.seen.items() if f.kinds.get(inst) != "agent" and now - at < KEEP_S}
        unfilled = [r for r in others if r not in filled]
        return ("Live: the agents' host reads [agents] every second; from now on it answers the task system's offers for "
                + (", ".join(serves) or "no role") + (f" and leaves {', '.join(others)} to other machines" if others else "")
                + ". Sessions already on a ticket finish. Nothing restarts."
                + (f" No other machine has been seen filling {', '.join(unfilled)}: offers for it wait until one does."
                   if unfilled else ""))
    return Plan("agents", lambda ed, kw: ed.set("agents.serves", serves, **kw),
                lambda c: f"the AI agents fill {', '.join(serves) or 'no task role'} ([agents] serves)", applies)


def _set_work_mode(project: Project, store: AgentStore, a: dict) -> Plan:
    work = a.get("work")
    if work not in ("tasks", "code"):
        raise StoreError("work is tasks (a scratch folder per ticket, nothing merged) or code (a branch per ticket, "
                         "reviewed and landed on the base branch through the merge gate)")
    residents = sorted(n for n, ag in all_agents(project).items() if ag.resident and any(
        (prompting.PROMPTS_DIR / f"{r}.tasks.md").exists() for r in ag.roles))     # their prompt depends on the mode

    def applies():
        no_gate = work == "code" and not ((project.orchestra.config.raw.get("gate") or {}).get("unit"))
        return ("Live: the task system's scheduler reads [project] on every tick; tickets offered from now on get "
                + ("a branch, a review of its diff and the merge gate" if work == "code" else "a scratch folder, and are done when approved")
                + "; tickets already assigned keep what they have."
                + (f" The resident sessions of {', '.join(residents)} restart after their current turn, to take the "
                   f"prompts for {work} work." if residents else "")
                + (" There is no [[gate.unit]]: branches land untested. Ask the human which test a branch must pass; "
                   "do not guess one." if no_gate else ""))

    def then():
        for n in residents:
            store.set_agent_state(n, restart_requested=True)
    return Plan("project", lambda ed, kw: ed.set("project.work", work, **kw),
                lambda c: f"work is {work} ([project] work)", applies, then)


def _set_gate_unit(project: Project, store: AgentStore, a: dict) -> Plan:
    name, command = (a.get("name") or "unit").strip(), (a.get("command") or "").strip()
    if not command:
        raise StoreError("command is the shell command a branch must pass, run in the branch's checkout")
    entry: dict[str, Any] = {"name": name, "command": command}
    if t := _duration(a, "timeout"):
        entry["timeout"] = t
    return Plan("gate.unit", lambda ed, kw: ed.add_entry("gate.unit", entry, **kw),
                lambda c: f"{_how(c)} the gate's test {name}: {command} ([[gate.unit]])",
                lambda: "Live: the task system's next gate run uses it (its scheduler reads [gate] on every tick).")


def _set_capacity_limit(project: Project, store: AgentStore, a: dict) -> Plan:
    given = {k: a[k] for k in ("workers", "reviewers") if a.get(k) is not None}
    if not given:
        raise StoreError("give workers, reviewers or both: the most tickets offered at once to each role")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in given.values()):
        raise StoreError("workers and reviewers are whole numbers, 0 or more")

    return Plan("tasks", lambda ed, kw: ed.set_many({f"tasks.{k}": v for k, v in given.items()}, **kw),
                lambda c: "at most " + " and ".join(f"{v} {k}" for k, v in given.items()) + " at once ([tasks])",
                lambda: ("Live: the task system's scheduler reads [tasks] on every tick. A ceiling the operator "
                         "set on the Dashboard wins over this one, and a planner's dial (set_capacity) stays at "
                         "or below it."))


def _set_machine(project: Project, store: AgentStore, a: dict) -> Plan:
    name, command = (a.get("name") or "").strip(), (a.get("command") or "").strip()
    if not name or not command:
        raise StoreError("a machine needs a name and the command that runs it (a program in the repository)")
    entry: dict[str, Any] = {"name": name, "command": command}
    for k in ("restart", "stop_grace"):
        if a.get(k) is not None:
            entry[k] = a[k]
    before = _entry(project, "machines", name)

    def applies():
        if before is None:
            return "orchestra run starts it within a second." + _unless_launching(project)
        if before.get("command") != command or before.get("restart", "always") != entry.get("restart", before.get("restart", "always")):
            return "orchestra run restarts it within a second (its command or restart changed)." + _unless_launching(project)
        return "Nothing restarts: the new stop grace applies the next time it is stopped."
    return Plan("machines", lambda ed, kw: ed.add_entry("machines", entry, **kw),
                lambda c: f"{_how(c)} the machine {name}: {command} ([[machines]])", applies)


def _set_collector(project: Project, store: AgentStore, a: dict) -> Plan:
    name = (a.get("name") or "").strip()
    command, url = (a.get("command") or "").strip(), (a.get("url") or "").strip()
    if not name or bool(command) == bool(url):
        raise StoreError("a collector needs a name and either a command (a script under machines/ that prints Prometheus "
                         "text: [[collect]]) or a url that serves it ([[scrape]]), not both")
    section = "collect" if command else "scrape"
    entry: dict[str, Any] = {"name": name, **({"command": command} if command else {"url": url})}
    for k in ("every", "timeout"):
        if v := _duration(a, k):
            entry[k] = v
    before = _entry(project, section, name)

    def applies():
        what = "runs from now on, on its interval" if before is None else "starts over with the new entry"
        return (f"orchestra run reads it within a second: it {what}, storing what it prints with machine={name}; a "
                f"failed round says why in its log, logs/machines/{name}.log." + _unless_launching(project))
    return Plan(section, lambda ed, kw: ed.add_entry(section, entry, **kw),
                lambda c: (f"{_how(c)} the collector {name}: {command or url}, every "
                           f"{entry.get('every') or (before or {}).get('every') or '60s'} ([[{section}]])"), applies)


def _remove_entry(project: Project, store: AgentStore, a: dict) -> Plan:
    section, name = a.get("section"), (a.get("name") or "").strip()
    if section not in SECTIONS:
        raise StoreError(f"section is one of {', '.join(SECTIONS)}")
    said = {"machines": "orchestra run stops it within a second.",
            "collect": "orchestra run stops running it; a round under way finishes and its points are dropped.",
            "scrape": "orchestra run stops reading it; a program it read is not stopped by this.",
            "gate.unit": "Live: the task system's next gate run leaves it out."}[section]
    return Plan(section, lambda ed, kw: ed.remove_entry(section, name, **kw), lambda c: f"removed {name} from [[{section}]]",
                lambda: said + ("" if section == "gate.unit" else _unless_launching(project)))


def _set_setting(project: Project, store: AgentStore, a: dict) -> Plan:
    key = (a.get("key") or "").strip()
    if not key or "value" not in a:
        raise StoreError("give the key (section.name, as desk.channels) and its value as JSON (null removes the key)")
    value = a["value"]
    if isinstance(value, str):                    # JSON in a string (the tool's schema); a bare word is a string
        try:
            value = json.loads(value)
        except ValueError:
            pass
    head = key.split(".")[0]

    def edit(ed, kw):
        return ed.unset(key, **kw) if value is None else ed.set(key, value, **kw)
    return Plan(head, edit, lambda c: f"{key} {'removed' if value is None else 'set'}",
                lambda: (f"Whatever reads [{head}] sees it on its next read: Orchestra's host and launcher within a "
                         "second, a plugin when it next reloads its configuration."))


HANDLERS: dict[str, Callable[[Project, AgentStore, dict], Plan]] = {
    "set_agent_roles": _set_agent_roles, "set_work_mode": _set_work_mode, "set_gate_unit": _set_gate_unit,
    "set_capacity_limit": _set_capacity_limit, "set_machine": _set_machine, "set_collector": _set_collector,
    "remove_entry": _remove_entry, "set_setting": _set_setting}


def handle_setup(project: Project, store: AgentStore, command: str, a: dict[str, Any], author: str, role: str) -> dict:
    if role != "supervisor":
        raise StoreError("only the supervisor changes the configuration")
    target = target_of(a, None if command == "set_setting" else "repo")
    if a.get("change"):
        own_prediction(store, role, a["change"])      # refused before anything is written
    recovered = recover(project)                  # an entry an interrupted restart left out
    plan = HANDLERS[command](project, store, a)
    if why := landing(store, role):
        raise StoreError(why)
    done = change(project, plan.edit, plan.what, a.get("reason") or "", target)
    if recovered:
        done["recovered"] = recovered
    if not done["written"]:
        return {"ok": True, **done, "applies": "Nothing changed, so nothing applies or restarts."}
    # the change is made: it is announced whatever happens after, and what fails after is said
    try:
        applies = plan.applies()
    except Exception as e:  # noqa: BLE001 - the change stands; only the description of it failed
        applies = f"Changed; what applies now could not be worked out ({e}): read_config and check_setup show it."
    data = {"section": plan.section, "change": done["announce"], "reason": a["reason"].strip(), "commit": done["commit"],
            "target": target, "applies": applies, "by": author}
    if claim_change(store, role, a.get("change")):     # the change a prediction of the Supervisor's waits for: Orchestra judges it from here
        store.events.publish(CHANGED, {**data, "prediction_change": a["change"]}, id=a["change"])
    else:
        store.events.emit(CHANGED, **data)
    out = {"ok": True, **done, "applies": applies,
           "next": ("Tell the human what you changed and why, the commit, and what applies now." if target == "repo" else
                    "Tell the human what you changed in their private overlay (not committed) and why, and what applies now.")}
    if plan.then:
        try:
            plan.then()
        except Exception as e:  # noqa: BLE001 - the change stands and was announced
            out["then_failed"] = f"the change is made and announced, but what the agents do about it failed: {e}"
    return out
