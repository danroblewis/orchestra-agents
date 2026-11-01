"""What runs an integration, planned by ``integrate_system`` and written by ``approve_integration``
(``design_integrate.py``), always to the owner's private overlay through ``config_change`` (never
the repository, nothing committed; INTEGRATING.md §3.1):

- a poller: a ``[[machines]]`` entry with ``restart = "always"`` that keeps its interval loop
  running (a process the launcher keeps, never an announced machine: the poller only relays), and
  the ``[[collect]]`` entry that stores its measurement;
- a measurement alone: the ``[[collect]]`` entry;
- a store: its ``[[sources]]`` entry, with the token's variable named in ``auth.bearer_env``;
- a bridge's reader: its ``[bridges.<system>]`` section.

**Written only once the owner approved.** A scaffolded program reads nothing but a fake on this
machine until its ``machine.toml`` says ``clearance = "approved"`` (the kits' compliance check): run
for good before that, its poller waits and re-reads ``machine.toml``, but its ``[[collect]]``
measurement would fail every round. A ``[[sources]]`` entry or a bridge's section is read the
moment it is written. So the plan is kept with the integration and written by the approval, in
one act; the scaffolded test is not run again (it reads the variable's name and the scope from
``machine.toml``)."""

from __future__ import annotations

import shlex
from pathlib import Path

from orchestra.overlay import OVERLAY

from .config import Project
from .config_change import change
from .design import MACHINE
from .setup_tools import CHANGED, INSTRUMENT_SECTIONS, INSTRUMENTS_CHANGED
from .store import AgentStore

COLLECT_TIMEOUT = "60s"


def poller(name: str, target: Path, every: str, *, events: bool) -> list[dict]:
    """The steps that run a scaffolded program: its poll loop (when it relays events) and its measurement."""
    prog = shlex.quote(str(target / f"{name}.py"))
    steps: list[dict] = []
    if events:
        steps.append({"how": "entry", "section": "machines",
                      "entry": {"name": name, "command": f"python {prog}", "restart": "always"}})
    steps.append({"how": "entry", "section": "collect",
                  "entry": {"name": name, "command": f"python {prog} measure", "every": every, "timeout": COLLECT_TIMEOUT}})
    return steps


def source(entry: dict) -> list[dict]:
    return [{"how": "entry", "section": "sources", "entry": entry}]


def bridge(system: str, section: dict) -> list[dict]:
    return [{"how": "set", "key": f"bridges.{system}", "value": section}]


def describe(step: dict) -> str:
    if step["how"] == "entry":
        e = step["entry"]
        extra = (f", restart {e['restart']}" if "restart" in e else f", every {e['every']}" if "every" in e else "")
        return f"[[{step['section']}]] {e['name']}{extra}"
    return f"[{step['key']}]"


def with_token(steps: list[dict], token_env: str) -> list[dict]:
    """A store's entry with the token's variable named (``auth.bearer_env``): the name, never a value."""
    out = []
    for s in steps:
        if s["how"] == "entry" and s["section"] == "sources":
            s = {**s, "entry": {**s["entry"], "auth": {"bearer_env": token_env}}}
        out.append(s)
    return out


def write(project: Project, store: AgentStore, steps: list[dict], reason: str) -> list[str]:
    """Each step in the owner's overlay, announced on the bus; what was done, a line each."""
    lines = []
    for s in steps:
        if s["how"] == "entry":
            section, entry = s["section"], s["entry"]
            done = change(project, lambda ed, kw, section=section, entry=entry: ed.add_entry(section, entry, **kw),
                          lambda c, s=s: f"{'added' if c.what.startswith('added') else 'changed'} {describe(s)} (overlay)",
                          reason, OVERLAY)
        else:
            section = s["key"].split(".")[0]
            done = change(project, lambda ed, kw, s=s: ed.set(s["key"], s["value"], **kw),
                          lambda c, s=s: f"set {describe(s)} (overlay)", reason, OVERLAY)
        store.events.emit(INSTRUMENTS_CHANGED if section in INSTRUMENT_SECTIONS else CHANGED, section=section,
                          change=done["announce"], reason=reason, commit=None, target=OVERLAY,
                          applies="orchestra run reads it within a second", by=MACHINE, src=MACHINE)
        lines.append(done["changed"])
    return lines
