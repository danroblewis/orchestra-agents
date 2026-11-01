"""Confirmed Universal Machines, kept in the state directory (machine-design.md §2.1: at work the
base branch is protected, so a confirmed machine's spec lives in
``~/.orchestrator/<key>/agents/machines/<name>/`` and is never committed).

Confirming one (``design_confirm.confirm``) writes, from its declaration, the whole of what the
agents run it from:

- ``machine.toml``: its spec in ``machine_spec``'s format (description, engagement, model: Haiku,
  D20's default for narrow machines, and ``emits``, the only types its ``emit_event`` may raise);
- ``role.md``: its role's prompt, the description, invariants and steps as instructions, for a
  scheduled one the events it reads each run, and each stock or effect it reads as a series it can
  read (``reads``: its link, else its instrument's series; ``design_machines.reads_of``), written
  again when a link or a keep changes that;
- ``role.json``: its role record (``roles.RoleDef``): the fewest tool groups its ``spec.uses``
  need (``USES``), with ``events`` (its emits) and ``escalate`` (it reaches the Supervisor);
- ``agent.json``: its agent record (``org.AgentDef``: scheduled at its time in its zone, with what
  a missed run does, or woken by its receives).

``roles.all_roles``, ``org_store.all_agents``, ``prompting.role_prompt`` and
``machine_spec.load_all`` read them beside the repository's ``.orchestra/`` (the repository's wins
on a name both have). A confirmed change to it writes them again; retiring it (``discard`` on a
declared machine, which retracts the declaration) or switching it to Programmatic removes the
directory (``remove``). Nothing here commits."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .config import Project

DEFAULT_MODEL = "haiku"
STATE_ROOT = "~/.orchestrator/<key>/agents/machines"      # how a spec written here names where it lives
MODES = {"scheduled": "scheduled", "continuous": "continuous", "ad hoc": "ad_hoc"}
# what a Universal Machine may act on, in the work's terms (spec.uses), and the tool group that lets it
USES = ((re.compile(r"\bticket", re.IGNORECASE), "tickets"), (re.compile(r"\bmodel\b|\bstocks?\b|\bflows?\b|dynamics", re.IGNORECASE), "model"),
        (re.compile(r"repositor|\bcode\b|\bmetrics?\b|\bseries\b|setup|configuration|\blogs?\b", re.IGNORECASE), "observe"),
        (re.compile(r"\bevents?\b|\bbus\b|coordinator|queue of work", re.IGNORECASE), "events"))
READ_ONLY = re.compile(r"read[- ]only|only read", re.IGNORECASE)
NUMBERED = re.compile(r"^\s*\d+\s*[.)]\s*")


def root(project: Project) -> Path:
    return project.state_dir / "machines"


def groups_for(uses: list[str]) -> tuple[list[str], list[str]]:
    """(the tool groups ``uses`` need, the uses no group covers)."""
    got, unmapped = [], []
    for u in uses or []:
        hit = [g for rx, g in USES if rx.search(u)]
        got += hit
        if not hit:
            unmapped.append(u)
    return list(dict.fromkeys(got)), unmapped


def unmapped_line(m: dict) -> str | None:
    """What the answer says when a use of a Universal declaration lets it act on nothing."""
    _, missing = groups_for((m.get("spec") or {}).get("uses") or [])
    if not missing or m.get("kind") != "universal":
        return None
    return (f"it could act on nothing for {', '.join(repr(u) for u in missing)}: say each use as the ticket queue, the model, "
            "the repository or the events, or it runs without that")


def runs_on(project: Project) -> str:
    """The harness and model a confirmed machine runs on in this project."""
    from .harness import get_harness
    h = get_harness(project.config.harness)
    return f"the {h.name} harness, model {h.model_for(DEFAULT_MODEL) or project.config.model or 'as the harness is set'}"


def _steps(steps: list[str]) -> list[str]:
    return [f"{i}. {NUMBERED.sub('', str(x))}" for i, x in enumerate(steps or [], 1)]


def files(m: dict, reads: dict[str, str] | None = None) -> dict[str, str]:
    """The files of a Universal Machine's implementation, written from its declaration ``m``;
    ``reads``: each of its reads as the series it reads (``design_machines.reads_of``)."""
    from .machine_spec import MachineSpec, render
    from .org import AgentDef
    from .roles import RoleDef
    e = m.get("engagement") or {}
    mode = MODES.get(e.get("mode"), "ad_hoc")
    spec = MachineSpec(name=m["id"], description=m.get("description") or "", engagement=mode, model=DEFAULT_MODEL,
                       created_by="supervisor", emits=list(m.get("emits") or []), root=STATE_ROOT)
    s = m.get("spec") or {}
    uses = list(s.get("uses") or [])
    groups, _ = groups_for(uses)
    receives = list(m.get("receives") or [])
    role = [f"# {m.get('title') or m['id']}", "", m.get("description") or "", "", "## Never", ""]
    role += [f"- {x}" for x in s.get("invariants") or []]
    if "tickets" in groups and any(READ_ONLY.search(u) for u in uses if re.search(r"ticket", u, re.IGNORECASE)):
        role.append("- change a ticket: you read the ticket queue only (list_tickets, get_ticket)")
    role += ["", "## Each run", ""]
    if mode == "scheduled" and receives:
        role += [f"Each run, read these events since your last run (read_events): {', '.join(receives)}.", ""]
    role += _steps(s.get("steps") or [])
    role += ["", f"You may act on: {', '.join(uses) or 'nothing but your events'}.",
             f"Emit only {', '.join(m.get('emits') or []) or 'nothing'} (emit_event)."]
    if m.get("reads"):
        role += ["Read only these:", *[f"- {(reads or {}).get(r) or r}" for r in m["reads"]]]
    else:
        role.append("Read no stock or effect.")
    role.append("When something is beyond what you may do, say so to the Supervisor (escalate_to_supervisor).")
    tools = list(dict.fromkeys(["events", "escalate", *groups, *(["model"] if m.get("reads") else [])]))
    rec = RoleDef(name=m["id"], description=m.get("description") or "", tools=tools, created_by="supervisor")
    scheduled = mode == "scheduled"
    agent = AgentDef(name=m["id"], roles=[m["id"]], kind=mode, session="resident" if mode == "continuous" else "ephemeral",
                     wake_on=[] if scheduled else receives, reads_events=receives if scheduled else [],
                     at=e.get("at") if scheduled else None, tz=e.get("tz") if scheduled else None,
                     missed=e.get("missed") if scheduled else None, description=m.get("description") or "",
                     created_by="supervisor")
    agent.validate(set(receives))
    return {"machine.toml": render(spec), "role.md": "\n".join(role) + "\n",
            "role.json": json.dumps(rec.to_dict(), indent=2) + "\n", "agent.json": json.dumps(agent.to_dict(), indent=2) + "\n"}


def write_files(d: Path, out: dict[str, str]) -> Path:
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    for rel, text in out.items():
        (d / rel).write_text(text, encoding="utf-8")
    return d


def write(project: Project, m: dict, reads: dict[str, str] | None = None) -> Path:
    return write_files(root(project) / m["id"], files(m, reads))


def remove(project: Project, name: str) -> bool:
    """Take a confirmed machine's records out; whether there were any."""
    d = root(project) / name
    had = d.is_dir()
    shutil.rmtree(d, ignore_errors=True)
    return had


def _dirs(project: Project) -> list[Path]:
    r = root(project)
    return sorted(p for p in r.iterdir() if p.is_dir()) if r.is_dir() else []


def _record(d: Path, name: str) -> dict | None:
    try:
        return json.loads((d / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def roles(project: Project) -> list:
    from .roles import RoleDef
    return [RoleDef.from_dict(r) for d in _dirs(project) if (r := _record(d, "role.json"))]


def agents(project: Project) -> list:
    from .org import AgentDef
    return [AgentDef.from_dict(r) for d in _dirs(project) if (r := _record(d, "agent.json"))]


def prompt(project: Project, name: str) -> str:
    p = root(project) / name / "role.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def spec_files(project: Project) -> dict[str, dict[str, str]]:
    """Each confirmed machine's spec files (``machine.toml``, ``role.md``), by name."""
    out = {}
    for d in _dirs(project):
        got = {}
        for rel in ("machine.toml", "role.md"):
            try:
                got[rel] = (d / rel).read_text(encoding="utf-8")
            except OSError:
                continue
        if "machine.toml" in got:
            out[d.name] = got
    return out
