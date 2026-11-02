"""Whether a declared machine runs, in the words ``list_machines`` and ``read_design`` give it
(``running``): from what the bus has heard and what the Workflows plugin's loaded definitions name,
never from the declared layer's state alone. "Unconfirmed" is the declared layer's state, not a
switch: the T2c walkthrough's Supervisor told the owner that seven machines the Workflows plugin
proposed "don't run" while each was heard on the bus, and that the nudger's program still had to be
written while it ran and sent that day's reminder.

- a machine a workflow's step names as its ``program`` (``who: {program: review_nudger}``) "runs as
  the workflow's program (review_nudger)": the plugin's program host runs it when the step is
  offered, whatever the declared layer says, and the launcher does not (``list_processes`` does not
  list it);
- a machine heard on the bus is "running: heard <when>", with who proposed or confirmed it;
- one never heard and with no workflow behind it says so: only that one runs nothing yet."""

from __future__ import annotations

from .config import Project

NOT_HEARD = "not heard on the bus yet, and no workflow runs it"


def _mid(name: str) -> str:
    return name.replace(".", "_").replace("-", "_")       # the Workflows plugin's machine id for a name


def workflow_programs(project: Project) -> dict[str, list[str]]:
    """Machine id -> the workflows whose steps name it as their program, from the plugin's loaded
    definitions; empty without the plugin or when they cannot be loaded."""
    from .workflow_tools import plugin
    p = plugin()
    if p is None:
        return {}
    try:
        defs = p.define.load(project.orchestra)
    except Exception:  # noqa: BLE001 - a definition that does not load names no program here
        return {}
    out: dict[str, list[str]] = {}
    for w in getattr(defs, "workflows", None) or []:
        for st in w.steps:
            actor = getattr(st, "actor", None)
            if getattr(actor, "kind", None) == "program" and w.name not in out.setdefault(_mid(actor.name), []):
                out[_mid(actor.name)].append(w.name)
    return out


def by_whom(declared: dict | None) -> str:
    """Who stands behind a declaration, in a few words: "proposed by the workflows plugin"."""
    d = declared or {}
    state = d.get("state")
    author = d.get("author") or (d.get("provenance") or {}).get("author")
    if state == "declared":
        return "confirmed"
    if author == "owner":
        return "declared by you, unconfirmed"
    if author == "supervisor":
        return "proposed by the Supervisor, unconfirmed"
    return f"proposed by the {author} plugin" if author else "proposed"


def running(mid: str, declared: dict | None, last_seen: float | None, programs: dict[str, list[str]]) -> str:
    """How the machine runs: ``declared`` its declared-layer entry (or the view's ``intent``),
    ``last_seen`` when the bus last heard it (None: never)."""
    from .machines_read import when
    heard = f"heard {when(last_seen)}" if last_seen is not None else None
    if mid in programs:
        out = f"runs as the workflow's program ({', '.join(programs[mid])})"
        return out + (f"; {heard}" if heard else "; not heard yet: it runs when the workflow offers its step")
    if heard:
        return f"running: {heard}" + (f", {by_whom(declared)}" if declared else "")
    return NOT_HEARD
