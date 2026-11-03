"""What runs an integration, planned by ``integrate_system`` and written by ``approve_integration``
(``design_integrate.py``), always to the owner's private overlay through ``config_change`` (never
the repository, nothing committed; INTEGRATING.md §3.1):

- a poller: a ``[[machines]]`` entry with ``restart = "always"`` that keeps its interval loop
  running (a process the launcher keeps, never an announced machine: the poller only relays), and
  the ``[[collect]]`` entry that stores its measurement;
- a measurement alone: the ``[[collect]]`` entry;
- a store: its ``[[sources]]`` entry, with the token named in ``auth.bearer_env`` (a variable) or
  ``auth.bearer_file`` (a file no commit can carry: ``credential_paths``).

**Written only once the owner approved.** A scaffolded program reads nothing but a fake on this
machine until its ``poller.toml`` says ``clearance = "approved"`` (the kits' compliance check): run
for good before that, its poller waits and re-reads ``poller.toml``, but its ``[[collect]]``
measurement would fail every round. A ``[[sources]]`` entry is read the moment it is
written. So the plan is kept with the integration and written by the approval, in
one act; the scaffolded test is not run again (it reads the variable's name and the scope from
``poller.toml``)."""

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


def describe(step: dict) -> str:
    e = step["entry"]
    extra = (f", restart {e['restart']}" if "restart" in e else f", every {e['every']}" if "every" in e else "")
    return f"[[{step['section']}]] {e['name']}{extra}"


def with_token(steps: list[dict], creds: dict) -> list[dict]:
    """A store's entry with the token named (``auth.bearer_env``, else ``auth.bearer_file``): a
    variable's name or a file's path, never a value."""
    auth = {"bearer_env": creds["token_env"]} if creds.get("token_env") else {"bearer_file": creds["token_file"]}
    out = []
    for s in steps:
        if s["how"] == "entry" and s["section"] == "sources":
            s = {**s, "entry": {**s["entry"], "auth": auth}}
        out.append(s)
    return out


def write(project: Project, store: AgentStore, steps: list[dict], reason: str) -> list[str]:
    """Each step in the owner's overlay, announced on the bus; what was done, a line each."""
    lines = []
    for s in steps:
        section, entry = s["section"], s["entry"]
        done = change(project, lambda ed, kw, section=section, entry=entry: ed.add_entry(section, entry, **kw),
                      lambda c, s=s: f"{'added' if c.what.startswith('added') else 'changed'} {describe(s)} (overlay)",
                      reason, OVERLAY)
        store.events.emit(INSTRUMENTS_CHANGED if section in INSTRUMENT_SECTIONS else CHANGED, section=section,
                          change=done["announce"], reason=reason, commit=None, target=OVERLAY,
                          applies="orchestra run reads it within a second", by=MACHINE, src=MACHINE)
        lines.append(done["changed"])
    return lines
