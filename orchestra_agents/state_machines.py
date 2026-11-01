"""Confirmed Universal Machines, kept in the state directory (machine-design.md §2.1: at work the
base branch is protected, so a confirmed machine's spec lives in
``~/.orchestrator/<key>/agents/machines/<name>/`` and is never committed).

Confirming one (``design_confirm.confirm``) writes, from its declaration, the whole of what the
agents run it from:

- ``machine.toml``: its spec in ``machine_spec``'s format (description, engagement, model: Haiku,
  D20's default for narrow machines, and ``emits``, the only types its ``emit_event`` may raise);
- ``role.md``: its role's prompt, the description, invariants and steps as instructions;
- ``role.json``: its role record (``roles.RoleDef``: the fewest tool groups its steps need);
- ``agent.json``: its agent record (``org.AgentDef``: scheduled at its time, or woken by its
  receives).

``roles.all_roles``, ``org_store.all_agents``, ``prompting.role_prompt`` and
``machine_spec.load_all`` read them beside the repository's ``.orchestra/`` (the repository's wins
on a name both have). A confirmed change to it writes them again; discarding is the declaration's,
and ``remove`` takes the directory out. Nothing here commits."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import Project

DEFAULT_MODEL = "haiku"
MODES = {"scheduled": "scheduled", "continuous": "continuous", "ad hoc": "ad_hoc"}


def root(project: Project) -> Path:
    return project.state_dir / "machines"


def files(m: dict) -> dict[str, str]:
    """The files of a Universal Machine's implementation, written from its declaration ``m``."""
    from .machine_spec import MachineSpec, render
    from .org import AgentDef
    from .roles import RoleDef
    e = m.get("engagement") or {}
    mode = MODES.get(e.get("mode"), "ad_hoc")
    spec = MachineSpec(name=m["id"], description=m.get("description") or "", engagement=mode, model=DEFAULT_MODEL,
                       created_by="supervisor", emits=list(m.get("emits") or []))
    s = m.get("spec") or {}
    role = [f"# {m.get('title') or m['id']}", "", m.get("description") or "", "", "## Never", ""]
    role += [f"- {x}" for x in s.get("invariants") or []] + ["", "## Each run", ""]
    role += [f"{i}. {x}" for i, x in enumerate(s.get("steps") or [], 1)]
    role += ["", f"You may act on: {', '.join(s.get('uses') or []) or 'nothing but your events'}.",
             (f"Emit only {', '.join(m.get('emits') or []) or 'nothing'} (emit_event); read only "
              f"{', '.join(m.get('reads') or []) or 'nothing'}.")]
    tools = ["events"] + (["model"] if m.get("reads") else [])
    rec = RoleDef(name=m["id"], description=m.get("description") or "", tools=tools, created_by="supervisor")
    agent = AgentDef(name=m["id"], roles=[m["id"]], kind=mode, session="resident" if mode == "continuous" else "ephemeral",
                     wake_on=[] if mode == "scheduled" else list(m.get("receives") or []),
                     at=e.get("at") if mode == "scheduled" else None, description=m.get("description") or "",
                     created_by="supervisor")
    agent.validate(set(m.get("receives") or []))
    return {"machine.toml": render(spec), "role.md": "\n".join(role) + "\n",
            "role.json": json.dumps(rec.to_dict(), indent=2) + "\n", "agent.json": json.dumps(agent.to_dict(), indent=2) + "\n"}


def write_files(d: Path, out: dict[str, str]) -> Path:
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    for rel, text in out.items():
        (d / rel).write_text(text, encoding="utf-8")
    return d


def write(project: Project, m: dict) -> Path:
    return write_files(root(project) / m["id"], files(m))


def remove(project: Project, name: str) -> None:
    shutil.rmtree(root(project) / name, ignore_errors=True)


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
