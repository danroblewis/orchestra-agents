"""A workflow step's program, declared (``declare_program``), and whether every program step of a
workflow has one behind it (``lacking``, ``status``): bootstrap run 7's headline goal stopped at its
first step because nothing could write the relator's ``machine.json``, while ``confirm`` said
nothing was to be written and the Supervisor told the owner the goal was "fully running".

- ``declare_program`` is the Supervisor's alone, on the owner's quoted yes. It writes
  ``machines/<id>/machine.json`` in the state directory's root, the file the Workflows plugin's
  program host reads (its guide, "Relating pull requests to tickets" and "Programs"; its
  ``programs/machines.py``), for a program **the plugin ships** (``spec.program``: ``relate_prs``
  first), never for code: a program of the project's own is a worker's to write. The answer's
  ``args`` name the token by its file's path (``--token-file``), never its value: an argument that
  looks like a secret is refused. The plugin's own reader is the judge: the file is read back
  through it, and a problem it finds takes the file out again and refuses the call, naming it. A
  repository's ``.orchestra/machines/<id>/machine.json`` wins over the state directory's, so one
  there is said and nothing is written; a scaffolded poller's folder of that name is never written
  into. Nothing is committed.
- ``lacking`` is each loaded workflow's program steps whose program no root declares; ``status`` is
  the workflow's state in words from it ("running" only when none lacks), the words the tools'
  answers carry and the host's check of the reply (``truth.overclaimed``) holds a reply to."""

from __future__ import annotations

import json
import os
from importlib import import_module
from pathlib import Path

from . import design
from .config import Project
from .secret_shapes import secret_in
from .store import AgentStore, StoreError

ID_CHARS = "lowercase letters, digits and _"
# what each program the plugin ships is declared with when the call leaves it out (the plugin's guide)
DEFAULTS = {"relate_prs": {"role": "relate_prs", "timeout": "1m", "exit": {"0": "related", "3": "unrelated"}}}
NEXT = ("declare_program declares a program the Workflows plugin ships (relate_prs), on the owner's yes; a program of "
        "the project's own is code, a worker's to write")


def programs_module():
    """The plugin's programs reader (``orchestra_workflows.programs.machines``), or None."""
    try:
        return import_module("orchestra_workflows.programs.machines")
    except ImportError:
        return None


def declared(project: Project) -> set[str] | None:
    """The program ids either root declares, as the plugin's reader has them; None without it."""
    m = programs_module()
    if m is None:
        return None
    try:
        return {p.id for p in m.load(project.orchestra)[0]}
    except Exception:  # noqa: BLE001 - a reader that fails declares nothing here
        return set()


def _defs(project: Project):
    from .workflow_tools import plugin
    p = plugin()
    if p is None:
        return None
    try:
        return p.define.load(project.orchestra)
    except Exception:  # noqa: BLE001 - definitions that do not load name no step here
        return None


def lacking(project: Project, defs=None) -> dict[str, list[dict]]:
    """Workflow -> its program steps whose program no root declares ({step, program}); empty when
    every step has one, or when the plugin is not there to say."""
    defs = _defs(project) if defs is None else defs
    have = declared(project)
    if defs is None or have is None:
        return {}
    out: dict[str, list[dict]] = {}
    for w in getattr(defs, "workflows", None) or []:
        for st in w.steps:
            actor = getattr(st, "actor", None)
            if getattr(actor, "kind", None) == "program" and actor.name not in have:
                out.setdefault(w.name, []).append({"step": st.name, "program": actor.name})
    return out


def waits_line(workflow: str, rows: list[dict]) -> str:
    return (f"the workflow {workflow} is declared but not running: " + "; ".join(
        f"its step {r['step']} waits for its program {r['program']}, which no root declares "
        f"(machines/{r['program']}/machine.json)" for r in rows) + f", so its items stop there. {NEXT}")


def status(project: Project, workflow: str, lacks: dict[str, list[dict]] | None = None) -> str:
    """The workflow's state in the words a reply may use."""
    lacks = lacking(project) if lacks is None else lacks
    if rows := lacks.get(workflow):
        return waits_line(workflow, rows) + ". Never call it running, or the goal it serves running, until then"
    return "running: every step has who does it (a program step's program is declared)"


def _arg_list(a: dict) -> list[str]:
    args = a.get("args")
    if args is None:
        return []
    if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
        raise StoreError("args is a list of words: [\"--projects\", \"HK\", \"--github\", \"https://github.example.com/api/v3\", "
                         "\"--token-file\", \"/path/the/owner/gave\"]")
    if (hit := secret_in(" ".join(args))) is not None:
        raise StoreError(f"args hold what looks like a secret ({hit}): give the token by its file's path (--token-file) "
                         "or its variable's name (--token-env), never its value; nothing was written")
    for i, x in enumerate(args):
        if x == "--token-file" and i + 1 < len(args) and not Path(args[i + 1]).expanduser().is_absolute():
            raise StoreError(f"--token-file is the token file's full path, as the owner gave it, not {args[i + 1]!r}")
    return args


def _body(mid: str, program: str, a: dict) -> dict:
    d = DEFAULTS.get(program, {})
    spec = {"program": program, "role": a.get("role") or d.get("role") or mid, "args": _arg_list(a)}
    for k in ("timeout", "exit"):
        if a.get(k) is not None or d.get(k) is not None:
            spec[k] = a.get(k) if a.get(k) is not None else d[k]
    return {"id": mid, "kind": "programmatic", "managed": "orchestra", "engagement": {"mode": "ad hoc"}, "spec": spec}


def _write(path: Path, body: dict) -> str | None:
    """Write ``body`` atomically; the text it replaced (None: there was none)."""
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return old


def declare_program(project: Project, store: AgentStore, a: dict, role: str) -> dict:
    if role != design.MACHINE:
        raise StoreError(f"refused declare_program: declaring a program is the Supervisor's alone (the caller is {role or 'none'!r})")
    m = programs_module()
    if m is None:
        from .workflow_tools import MISSING
        raise StoreError(MISSING)
    prov = design.provenance(store, a)
    if prov.get("author") != design.OWNER:
        raise StoreError("declare_program runs a program on the owner's systems: quote their yes to it (quote), as they said "
                         "it this session; ask in one sentence first. Nothing was written")
    program = str(a.get("program") or "").strip()
    shipped = sorted(getattr(m, "SHIPPED", {}) or {})
    if program not in shipped:
        raise StoreError(f"program names one the Workflows plugin ships ({', '.join(shipped) or 'none in this version'}), "
                         f"not {program!r}: a program of the project's own is code, a worker's to write; nothing was written")
    mid = str(a.get("id") or program).strip()
    if not m.ID.match(mid):
        raise StoreError(f"id is the program's id as the workflow's step names it ({ID_CHARS}), not {mid!r}")
    o = project.orchestra
    repo = Path(o.repo_root) / ".orchestra" / "machines" / mid / "machine.json"
    if repo.is_file():
        raise StoreError(f"the repository declares {mid} already ({repo}), and its copy is the one the program host reads: "
                         "nothing was written; a change to it is a change to the repository")
    d = Path(o.state_dir) / "machines" / mid
    if d.is_dir() and any(p.name != "machine.json" for p in d.iterdir()):
        raise StoreError(f"{d} holds another machine's files (a scaffolded poller?): choose another id; nothing was written")
    body = _body(mid, program, a)
    path = d / "machine.json"
    old = _write(path, body)
    problems = [s for s in m.load(o)[1] if s.startswith((f"machines/{mid}/", f"machines/{mid}:"))]
    if problems:
        if old is None:
            path.unlink(missing_ok=True)
            if not any(d.iterdir()):
                d.rmdir()
        else:
            path.write_text(old, encoding="utf-8")
        raise StoreError(f"declare_program refused, nothing written: the Workflows plugin's reader found "
                         f"{design.plural(len(problems), 'problem')}: " + " ".join(f"({i}) {s.rstrip('.')}." for i, s in
                                                                                     enumerate(problems, 1)))
    defs = _defs(project)
    steps = [f"{w.name}.{st.name}" for w in (getattr(defs, "workflows", None) or []) for st in w.steps
             if getattr(st.actor, "kind", None) == "program" and st.actor.name == mid]
    out = {"ok": True, "program": mid, "path": str(path), "spec": body["spec"], "author": prov.get("author"),
           "replaced": old is not None, "committed": False,
           "runs": ("the Workflows plugin's program host reads it within a tick and runs it each time a step naming it is "
                    "offered (not a launcher process: list_processes does not list it)"),
           "steps": steps}
    if not steps:
        out["note"] = f"no loaded workflow's step names the program {mid} yet: declare_workflow with who: {{program: {mid}}}"
    lacks = lacking(project, defs)
    out["status"] = {w: status(project, w, lacks) for w in {s.split(".")[0] for s in steps}}
    return out
