"""``declare_valve``: the Supervisor declares a valve (``valve/``), a decision step that asks a model
the owner has access to which way each item flows, from the owner's words ("filter tickets that
don't need on-call"). It writes two files in the state directory's ``machines/<id>/``, never
committed: ``valve.json``, the declaration (``valve/declaration.py``, its checks the judge), and
``machine.json``, the program the Workflows plugin's program host runs (``spec.program: valve``,
which the agents package ships to it through the entry point ``orchestra_workflows.programs``),
with the ``exit`` map that makes each label the step's outcome and the proxy variables it may read
(``spec.env``). The plugin's reader reads it back; a problem it finds takes both files out again.

With ``workflow`` it also inserts the step into that workflow, before ``before`` (its first step by
default), each label routed by ``routes`` (else to the step it was put before) and the fallback to
the workflow's ``person: owner`` step when it has one; the new definition goes through
``declare_workflow`` and all its checks. Under ``[agents] ask = "outside"`` this is local and asks
nothing (the first call to the model is a read of an external service, but it spends the owner's
money): the answer's ``say`` is the line to the owner, which states the daily cap. The token is only
a file's path, which must be chmod 600 for the valve to read it."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from . import ask, design, program_tools
from .config import Project
from .secret_shapes import secret_in
from .store import AgentStore, StoreError
from .valve.declaration import as_json, exit_map, parse

PROGRAM = "valve"
FILE = "valve.json"
PROXY_ENV = ["HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy"]
DECLARED = ("question", "labels", "inputs", "api", "base_url", "model", "token_file", "min_confidence", "fallback",
            "max_calls_per_day", "timeout_s", "retries", "backoff_s", "max_tokens", "classify")
OWNER_STEP = {"person": "owner"}


def _declaration(mid: str, a: dict) -> dict:
    raw = {"name": mid, **{k: a[k] for k in DECLARED if a.get(k) is not None}}
    if "max_calls_per_day" not in raw:
        raise StoreError("max_calls_per_day is the owner's daily cap on calls to the model (it spends their money): give "
                         "it, and say it to them; nothing was written")
    if (hit := secret_in(json.dumps({k: v for k, v in raw.items() if k != "question"}))) is not None:
        raise StoreError(f"the declaration holds what looks like a secret ({hit}): the token goes only by its file's path "
                         "(token_file); nothing was written")
    v, problems = parse(raw)
    if v is None:
        raise StoreError("declare_valve refused, nothing written: " + " ".join(
            f"({i}) {s.rstrip('.')}." for i, s in enumerate(problems, 1)))
    return as_json(v)


def token_note(path: str) -> str | None:
    if not path:
        return None
    p = Path(path).expanduser()
    try:
        st = p.stat()
    except OSError:
        return f"the token file {p} is not there yet: until the owner puts it there, every decision is the fallback"
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return (f"the token file {p} is readable by others: until it is chmod 600, the valve refuses it and every "
                "decision is the fallback; tell the owner")
    return None


def _timeout(d: dict) -> str:
    t, n, b = float(d["timeout_s"]), int(d["retries"]), float(d["backoff_s"])
    return f"{int(t * (n + 1) + b * (2 ** n) + 30)}s"


def _write(path: Path, text: str) -> str | None:
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return old


def _put_back(path: Path, old: str | None) -> None:
    if old is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(old, encoding="utf-8")


def _inserted(definition: dict, mid: str, a: dict, labels: list[str], fallback: str) -> tuple[dict, dict]:
    """The definition with the valve's step before ``before``, and the step's outcomes."""
    steps = list(definition.get("steps") or [])
    names = [s.get("name") for s in steps]
    step = str(a.get("step") or mid)
    if step in names:
        raise StoreError(f"the workflow {definition.get('name')} has a step {step!r} already: choose another step name; "
                         "nothing was written")
    before = a.get("before") or (names[0] if names else None)
    if before not in names:
        raise StoreError(f"before names one of the workflow's steps ({', '.join(map(str, names))}), not {before!r}")
    routes = a.get("routes") or {}
    if not isinstance(routes, dict) or set(routes) - set(labels) - {fallback} or not all(
            r is None or r in names for r in routes.values()):
        raise StoreError(f"routes maps each label ({', '.join(labels)}) to one of the workflow's steps or null (the end)")
    outcomes = {label: routes.get(label, before) for label in labels}
    if fallback not in outcomes:
        owner = next((s["name"] for s in steps if s.get("who") == OWNER_STEP), None)
        target = routes.get(fallback, owner)
        if target is not None or fallback in routes:
            outcomes[fallback] = target
    i = names.index(before)
    new = {**definition, "steps": [*steps[:i], {"name": step, "who": {"program": mid}, "outcomes": outcomes}, *steps[i:]]}
    return new, outcomes


def declare_valve(project: Project, store: AgentStore, a: dict, role: str) -> dict:
    if role != design.MACHINE:
        raise StoreError(f"refused declare_valve: declaring a valve is the Supervisor's alone (the caller is {role or 'none'!r})")
    m = program_tools.programs_module()
    if m is None:
        from .workflow_tools import MISSING
        raise StoreError(MISSING)
    shipped = m.shipped() if hasattr(m, "shipped") else dict.fromkeys(getattr(m, "SHIPPED", {}))
    if PROGRAM not in shipped:
        raise StoreError("the installed Workflows plugin cannot run another package's program (it needs the entry point "
                         "group orchestra_workflows.programs): the valve cannot run here; nothing was written")
    prov = design.provenance(store, a)
    if not ask.decided(project, prov):
        raise StoreError("declare_valve asks a model with the owner's token ([agents] ask = \"always\"): quote their yes; ask "
                         "in one sentence first, with the daily cap. Nothing was written")
    mid = str(a.get("id") or "").strip()
    if not m.ID.match(mid):
        raise StoreError(f"id is the valve's id as the workflow's step names it ({program_tools.ID_CHARS}), not {mid!r}")
    d = _declaration(mid, a)
    o = project.orchestra
    if (Path(o.repo_root) / ".orchestra" / "machines" / mid / "machine.json").is_file():
        raise StoreError(f"the repository declares a machine {mid} already, and its copy is the one the program host reads: "
                         "choose another id; nothing was written")
    folder = Path(o.state_dir) / "machines" / mid
    if folder.is_dir() and any(p.name not in ("machine.json", FILE) for p in folder.iterdir()):
        raise StoreError(f"{folder} holds another machine's files: choose another id; nothing was written")
    vpath, mpath = folder / FILE, folder / "machine.json"
    body = {"id": mid, "kind": "programmatic", "managed": "orchestra", "engagement": {"mode": "ad hoc"},
            "spec": {"program": PROGRAM, "role": mid, "args": ["--declaration", str(vpath)], "timeout": _timeout(d),
                     "exit": exit_map(parse(d)[0]), "env": PROXY_ENV}}
    old_v = _write(vpath, json.dumps(d, indent=2) + "\n")
    old_m = _write(mpath, json.dumps(body, indent=2) + "\n")
    problems = [s for s in m.load(o)[1] if s.startswith((f"machines/{mid}/", f"machines/{mid}:"))]
    if problems:
        _put_back(mpath, old_m)
        _put_back(vpath, old_v)
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
        raise StoreError("declare_valve refused, nothing written: the Workflows plugin's reader found " + " ".join(
            f"({i}) {s.rstrip('.')}." for i, s in enumerate(problems, 1)))
    outcomes = list(parse(d)[0].outcomes)
    out = {"ok": True, "valve": mid, "declaration": str(vpath), "path": str(mpath), "outcomes": outcomes,
           "exit": body["spec"]["exit"], "author": prov.get("author"), "replaced": old_m is not None, "committed": False,
           "cap": d["max_calls_per_day"],
           "say": (f"Declared the valve {mid}: it asks {d['model'] or 'the classifier'} at {d['base_url']} "
                   f"\"{d['question']}\" for each item, answering {', '.join(d['labels'])}; at most "
                   f"{d['max_calls_per_day']} calls a day, an unchanged item never asked twice, and below "
                   f"{d['min_confidence']:g} confidence, over the cap or with the model down it goes to {d['fallback']}.")}
    if note := token_note(d["token_file"]):
        out["token"] = note
    if a.get("workflow"):
        out["workflow"] = _insert(project, store, a, mid, d, prov)
    else:
        out["note"] = (f"no workflow names it yet: give workflow (and before, routes) to insert the step, or declare_workflow "
                       f"with who: {{program: {mid}}}")
    return out


def _insert(project: Project, store: AgentStore, a: dict, mid: str, d: dict, prov: dict) -> dict:
    from . import relation_guard
    from .workflow_tools import _plugin, declare_workflow
    p = _plugin()
    o = project.orchestra
    name = str(a["workflow"])
    current = relation_guard.current(p, o, name)
    if current is None:
        raise StoreError(f"no root holds a workflow {name!r}: the valve is declared ({mid}), but no step was inserted")
    if any((s.get("who") or {}).get("program") == mid for s in current.get("steps") or []):
        return {"workflow": name, "unchanged": f"a step of {name} names the valve {mid} already"}
    new, outcomes = _inserted(current, mid, a, list(d["labels"]), d["fallback"])
    in_repo = (Path(o.repo_root) / ".orchestra" / "workflows" / name / "workflow.json").is_file()
    got = declare_workflow(project, store, {"definition": new, "root": "repository" if in_repo else "state",
                                            "reason": prov.get("reason"), "quote": a.get("quote")}, design.MACHINE)
    got["step"] = {"name": a.get("step") or mid, "outcomes": outcomes}
    if d["fallback"] not in outcomes:
        got["fallback"] = (f"the workflow has no person: owner step, so an item whose outcome is {d['fallback']} stays at "
                           "the valve's step and the planner is told once")
    return got
