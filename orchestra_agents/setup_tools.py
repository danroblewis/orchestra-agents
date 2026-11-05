"""The Supervisor's configuration tools: the setup of the project, changed one entry at a time
through Orchestra's own objects (``project.orchestra.config_edit``; ``config_change.change``),
announced on the bus (``config.changed``; ``instruments.changed`` for a change of what is measured
only, which begins no era of the model) and answered with what applies now and what restarts.

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
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestra import runtime
from orchestra.config import ConfigError, parse_duration
from orchestra.config_edit import Change

from . import (
    credential_paths,
    era_guard,
    prompting,
    runs_project,
    scm_settings,
    source_limits,
    supervisor_pr,
)
from .builtins import PER_TICKET_ROLES
from .config import Project
from .config_change import Edit, change, target_of
from .fillers import KEEP_S, Fillers
from .gate_busy import landing
from .instruments import shell_command
from .modeling import claim_change, own_prediction
from .org_store import all_agents
from .restart import recover
from .store import AgentStore, StoreError

SETUP_COMMANDS = frozenset({"set_agent_roles", "set_work_mode", "set_gate_unit", "set_capacity_limit",
                            "set_machine", "set_collector", "remove_entry", "set_setting"})
SECTIONS = ("machines", "collect", "scrape", "gate.unit")
CHANGED = "config.changed"
# A change of what is measured only: announced as its own type, which begins no era in Orchestra's model
# (only config.changed does, by default): measuring more is observation, not a change to the system, and an
# era begun by it would throw away the history a new source brings (the model reads only the current era).
INSTRUMENTS_CHANGED = "instruments.changed"
INSTRUMENT_SECTIONS = frozenset({"collect", "scrape", "sources", "series"})


def measured(section: str, subject: str) -> bool:
    """Whether a change is of what is measured only (``instruments.changed``): an instrument's section,
    or a ``[[machines]]`` entry that runs a collector (its subject ``collector:``)."""
    return section in INSTRUMENT_SECTIONS or subject.startswith(("collector:", "source:", "instrument:"))


@dataclass
class Plan:
    section: str                                  # what the bus event names
    edit: Edit                                    # Orchestra's editor and its arguments -> the change
    what: Callable[[Change], str]                 # the change -> one line saying what it does (the commit's title)
    applies: Callable[[], str]                    # after it is made: what applies now, what restarts
    then: Callable[[], None] | None = None        # after it is made: what the agents do about it
    label: str = ""                               # what it is for, in a few words: the commit's subject, the PR's title
    subject: str = ""                             # what it changes, the bus event's subject: machine:, collector:, source:, setting:


# set_setting's keys, named for what they are for (the commit's subject and the pull request's title); a key not here is
# named by its last part ("agents.draft_expiry": "draft expiry"), a whole section by its table ("scm": "[scm]")
LABELS = {"outbox.kinds": "an Outbox kind", "discovery.eras_from": "the era filter", "sources": "[[sources]]",
          "series": "[[series]]", "agents.serves": "the agents' roles", "project.work": "the work mode"}


def _about(section: str, name: str, project: Project | None = None) -> str:
    """The bus subject of an entry's change: what kind of thing it is, and its name. Only a machine's
    (``machine:<name>``) can begin an era (Orchestra's C3): a collector, a source or a setting never does.
    A ``[[machines]]`` entry that runs an instrument (``measures``: a kit's poller) is ``collector:<name>``,
    added, changed or removed (bootstrap run 5: a poller's removal said ``machine:`` and began the run's
    only era, cutting discovery's fit to 58 minutes of a week's history)."""
    if section == "machines" and project is not None and measures(project, name):
        return f"collector:{name}"
    return {"machines": f"machine:{name}", "collect": f"collector:{name}", "scrape": f"collector:{name}",
            "sources": f"source:{name}"}.get(section, f"setting:{section}.{name}")


def measures(project: Project, name: str) -> bool:
    """Whether the ``[[machines]]`` entry ``name`` runs an instrument, not a machine: a poller
    scaffolded from a kit (its directory holds a ``poller.toml``: under ``.orchestra/machines/``, the
    state directory's ``machines/``, or beside the program its command runs; or the bridges list it,
    a legacy ``machine.toml`` included), or a program with a ``[[collect]]``/``[[scrape]]`` twin of
    the same name (an integration's pair, ``integrate_schedule.poller``). A poller only relays what
    a system's people and bots did, as them: the provider is never the machine (Orchestra's rule 2)."""
    if _entry(project, "collect", name) or _entry(project, "scrape", name):
        return True
    root = project.repo_root
    dirs = [root / ".orchestra" / "machines" / name, project.orchestra.state_dir / "machines" / name]
    command = (_entry(project, "machines", name) or {}).get("command")
    try:
        words = command if isinstance(command, list) else shlex.split(command or "")
    except ValueError:
        words = []
    for w in words:
        if str(w).endswith(".py"):
            path = Path(str(w)).expanduser()
            dirs.append((path if path.is_absolute() else root / path).parent)
    if any((d / "poller.toml").is_file() for d in dirs):
        return True
    from . import integrate_kits
    try:
        return integrate_kits.installed() and any(i["name"] == name for i in integrate_kits.shipped(project))
    except Exception:  # noqa: BLE001 - the bridges' listing is a hint; without it the files above decide
        return False


def _setting_about(key: str, value: Any) -> str:
    """``set_setting``'s subject: ``source:<names>`` for [[sources]], else ``setting:<key>``."""
    if key == "sources" and isinstance(value, list):
        names = [str(e.get("name")) for e in value if isinstance(e, dict) and e.get("name")]
        return "source:" + ",".join(names) if names else "setting:sources"
    return f"setting:{key}"


def label_for(key: str) -> str:
    for k, v in LABELS.items():
        if key == k or key.startswith(k + "."):
            return v
    if "." not in key:
        return f"[{key}]"
    return key.rsplit(".", 1)[1].strip('"').replace("_", " ")


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
                lambda c: f"the AI agents fill {', '.join(serves) or 'no task role'} ([agents] serves)", applies,
                label="the agents' roles", subject="setting:agents.serves")


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
                lambda c: f"work is {work} ([project] work)", applies, then, label="the work mode", subject="setting:project.work")


def _set_gate_unit(project: Project, store: AgentStore, a: dict) -> Plan:
    name, command = (a.get("name") or "unit").strip(), (a.get("command") or "").strip()
    if not command:
        raise StoreError("command is the shell command a branch must pass, run in the branch's checkout")
    entry: dict[str, Any] = {"name": name, "command": command}
    if t := _duration(a, "timeout"):
        entry["timeout"] = t
    return Plan("gate.unit", lambda ed, kw: ed.add_entry("gate.unit", entry, **kw),
                lambda c: f"{_how(c)} the gate's test {name}: {command} ([[gate.unit]])",
                lambda: "Live: the task system's next gate run uses it (its scheduler reads [gate] on every tick).",
                label=f"the gate's test {name}", subject=f"setting:gate.unit.{name}")


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
                         "or below it."), label="capacity", subject="setting:tasks")


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
                lambda c: f"{_how(c)} the machine {name}: {command} ([[machines]])", applies,
                label=f"the machine {name}", subject=_about("machines", name, project))


def _set_collector(project: Project, store: AgentStore, a: dict) -> Plan:
    name = (a.get("name") or "").strip()
    command, url = (a.get("command") or "").strip(), (a.get("url") or "").strip()
    if not name or bool(command) == bool(url):
        raise StoreError("a collector needs a name and either a command (a script under machines/ that prints Prometheus "
                         "text: [[collect]]) or a url that serves it ([[scrape]]), not both")
    runs_project.refuse(command, root=project.repo_root)       # an instrument measures, never runs the project (SPEC §10)
    section = "collect" if command else "scrape"
    words: Any = shell_command(command) if command and a.get("shell") else command
    entry: dict[str, Any] = {"name": name, **({"command": words} if command else {"url": url})}
    for k in ("every", "timeout"):
        if v := _duration(a, k):
            entry[k] = v
    before = _entry(project, section, name)

    def applies():
        what = "runs from now on, on its interval" if before is None else "starts over with the new entry"
        return (f"orchestra run reads it within a second: it {what}, storing what it prints labelled instrument={name}; a "
                f"failed round says why in its log, logs/instruments/{name}.log. read_instrument {name} after its first "
                "round shows what it stored." + _unless_launching(project))
    return Plan(section, lambda ed, kw: ed.add_entry(section, entry, **kw),
                lambda c: (f"{_how(c)} the collector {name}: {command or url}, every "
                           f"{entry.get('every') or (before or {}).get('every') or '60s'} ([[{section}]])"), applies,
                label=f"the collector {name}", subject=f"collector:{name}")


def _remove_entry(project: Project, store: AgentStore, a: dict) -> Plan:
    section, name = a.get("section"), (a.get("name") or "").strip()
    if section not in SECTIONS:
        raise StoreError(f"section is one of {', '.join(SECTIONS)}")
    said = {"machines": "orchestra run stops it within a second.",
            "collect": "orchestra run stops running it; a round under way finishes and its points are dropped.",
            "scrape": "orchestra run stops reading it; a program it read is not stopped by this.",
            "gate.unit": "Live: the task system's next gate run leaves it out."}[section]
    return Plan(section, lambda ed, kw: ed.remove_entry(section, name, **kw), lambda c: f"removed {name} from [[{section}]]",
                lambda: said + ("" if section == "gate.unit" else _unless_launching(project)),
                label=f"{name} removed", subject=_about(section, name, project))


def _check_sources(project: Project, key: str, value: list) -> str | None:
    """Refuse a [[sources]] or [[series]] list Orchestra would refuse when it reads it (a bad name,
    a literal secret, a series of no source): config_edit does not check sources, so without this
    a refused source would be committed and only seen missing at the next discovery. One refused
    only because its token file could not be found from here is written all the same (the owner,
    2026-11-04: never give up silently): the tool command may run in Claude Code's sandbox, which
    cannot see the file, and the host and the Core page read it from outside any sandbox. What is
    said then is the caution returned."""
    from orchestra.sources.spec import load
    raw = dict(project.orchestra.config.raw)
    raw[key] = value
    refused = load(raw, project.repo_root).refused
    mine = {k: v for k, v in refused.items() if k.startswith("series[") == (key == "series")}
    unseen = credential_paths.unseen(mine) if key == "sources" else {}
    mine = {k: v for k, v in mine.items() if k not in unseen}
    if mine:
        raise StoreError(f"Orchestra would refuse these [[{key}]] entries, so nothing was written: "
                         + "; ".join(f"{k}: {v}" for k, v in mine.items()))
    if not unseen:
        return None
    return ("; ".join(f"[[sources]] {name}: its token_file {tf} could not be {why} from here, so the entry was written "
                      f"without checking it" for name, (tf, why) in unseen.items())
            + ". Orchestra's host and the Core page read it from outside any sandbox: the Core page's store section "
              "shows whether the store is reachable, and a token file that is not there is refused there with why. "
              "Tell the owner what was written and that the Core page shows whether the store answers")


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
    if key == "agents.pass_env" or (key == "agents" and isinstance(value, dict) and "pass_env" in value):
        raise StoreError("[agents] pass_env is the owner's alone: it lets a variable of the host's environment, a credential "
                         "perhaps, into every session. Tell the owner the variable's name and to add it to pass_env in "
                         "their private overlay, then restart orchestra run")
    unseen = None
    if key in ("sources", "series") and isinstance(value, list):
        unseen = _check_sources(project, key, value)
    if key == "sources":
        source_limits.check_scopes(project, store, value)     # on a wake-up: narrow, split, kinds, names only (explore_round.py)
        source_limits.check_declared(project, store, value)   # a match the owner gave, never one picked here (run 6)
    caution = scm_settings.check(project, key, value)      # [scm]: orchestra-scm's schema, the token by file only
    eras = era_guard.check(project, store, key, value)     # [discovery]: never eras faster than discovery can fit a stock in
    caution = "; ".join(x for x in (caution, eras, unseen) if x)
    label = " ".join(str(a.get("subject") or "").split())[:40] or label_for(key)

    def edit(ed, kw):
        return ed.unset(key, **kw) if value is None else ed.set(key, value, **kw)
    return Plan(head, edit, lambda c: f"{key} {'removed' if value is None else 'set to ' + _short(value)}",
                lambda: (f"Whatever reads [{head}] sees it on its next read: Orchestra's host and launcher within a "
                         "second, a plugin when it next reloads its configuration." + (f" Caution: {caution}" if caution else "")),
                label=label, subject=_setting_about(key, value))


def _short(value: Any, most: int = 60) -> str:
    """A value as the commit's subject shows it: its JSON, cut at ``most`` characters."""
    text = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    return text if len(text) <= most else text[:most - 1] + "…"


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
    done = change(project, plan.edit, plan.what, a.get("reason") or "", target, store=store,
                  quote=supervisor_pr.owner_words(store, a), label=plan.label)
    if recovered:
        done["recovered"] = recovered
    # no commit of its own to carry the branch: its pending commits, now [scm] may be set
    if not done.get("commit") and (pushed := supervisor_pr.catch_up(project, store)) is not None:
        done.update(pull_request={k: pushed.get(k) for k in ("number", "url", "draft", "pending")},
                    said=pushed["said"], branch=supervisor_pr.SIDE)
    if not done["written"]:           # nothing committed and nothing announced: a no-op begins no era
        return {"ok": True, **done, "applies": "Nothing changed, so nothing was committed or announced, and nothing applies "
                                               "or restarts."}
    # the change is made: it is announced whatever happens after, and what fails after is said
    try:
        applies = plan.applies()
    except Exception as e:  # noqa: BLE001 - the change stands; only the description of it failed
        applies = f"Changed; what applies now could not be worked out ({e}): read_config and check_setup show it."
    data = {"section": plan.section, "change": done["announce"], "for": plan.label, "reason": a["reason"].strip(),
            "commit": done["commit"], "target": target, "applies": applies, "by": author}
    if claim_change(store, role, a.get("change")):     # the change a prediction of the Supervisor's waits for: Orchestra judges it from here
        store.events.publish(CHANGED, {**data, "prediction_change": a["change"]}, id=a["change"], subject=plan.subject or f"setting:{plan.section}")
    else:
        store.events.emit(INSTRUMENTS_CHANGED if measured(plan.section, plan.subject) else CHANGED,
                          about=plan.subject or f"setting:{plan.section}", **data)
    out = {"ok": True, **done, "applies": applies,
           "next": ("Tell the human what you changed and why, what applies now, and the branch and its pull request as "
                    "`said` puts them (or that the pull request is pending, and why)." if target == "repo" else
                    "Tell the human what you changed in their private overlay (not committed) and why, and what applies now"
                    + (", and the branch's pull request as `said` puts them." if done.get("said") else "."))}
    if plan.then:
        try:
            plan.then()
        except Exception as e:  # noqa: BLE001 - the change stands and was announced
            out["then_failed"] = f"the change is made and announced, but what the agents do about it failed: {e}"
    return out
