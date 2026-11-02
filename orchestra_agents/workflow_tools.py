"""The Supervisor's ``workflows`` tools (orchestra-workflows' SPEC §7.1, §10): it writes a workflow
the owner describes, reads the loaded definitions back, and explains an item.

The Workflows plugin is an optional dependency: it is imported here, lazily (``plugin``), for its
validated writer, its loader and its record's read API, the one exception to AGENTS.md rule 3's
"nothing is imported from that plugin". Without it every tool answers that it is not installed.

- ``declare_workflow`` is the Supervisor's alone. It takes a ``reason`` and an optional ``quote`` of
  the owner's words, verified like the design tools' (``design.provenance``). The plugin's checks
  run first; with any problem nothing is written and the refusal names every one. It writes only
  through the plugin's ``define.write``: by default to the state directory; to the repository's
  ``.orchestra/`` only with the owner's quoted words, and then it is **not committed** (at work the
  base branch is protected, and the plugin's writer leaves landing it to a pull request): the answer
  says so and that it is live, and the Supervisor hands it to the Coordinator to land through a ticket
  (never asking the owner to commit, discard or land it).
  An ``every`` start's ``tz`` given as the owner answered the zone question ("this machine's
  clock", "local", "here") is written as the host's zone's IANA name, and the answer's ``zone`` says so.
- ``list_workflows``, ``list_items`` and ``explain_item`` only read. ``list_items`` is the Items
  page's list (the plugin's ``pages.items``): what the owner asks when they ask what is stuck, and
  what needs them (L3: without it the Supervisor asked the task system, which this project does not
  run, and named no item)."""

from __future__ import annotations

import json
import time
from importlib import import_module
from types import SimpleNamespace

from orchestra.config import ConfigError

from . import design
from .clock import resolve_tz
from .config import Project
from .store import AgentStore, StoreError
from .tasking_text import gate

ROOTS = ("state", "repository")
MISSING = ("the Workflows plugin (orchestra-workflows) is not installed in the environment the agents run in, so there "
           "are no workflows to declare or read; tell the owner it is needed (install orchestra-workflows beside "
           "orchestra-agents) and do not write a definition any other way")
SHOWN = ("Its items show on the Items page (/items) and its piles as stocks on the Dynamics page. The plugin takes the "
         "change within a tick.")
LAST = 15


def plugin() -> SimpleNamespace | None:
    """The plugin's modules these tools use, or None when it is not installed (tests replace this)."""
    try:          # by module path: the package exports a function ``check`` that hides its module
        define = import_module("orchestra_workflows.define")
        check, loader = import_module("orchestra_workflows.define.check"), import_module("orchestra_workflows.define.loader")
        api = import_module("orchestra_workflows.record.api")
    except ImportError:
        return None
    try:
        listing = import_module("orchestra_workflows.pages.items").items
    except (ImportError, AttributeError):
        listing = None                                    # an older plugin: list_items says so
    return SimpleNamespace(define=define, problems=check.problems, roots=loader.roots, Record=api.Record, item_list=listing)


def _plugin() -> SimpleNamespace:
    p = plugin()
    if p is None:
        raise StoreError(MISSING)
    return p


def declare_workflow(project: Project, store: AgentStore, a: dict, role: str) -> dict:
    if role != design.MACHINE:
        raise StoreError(f"refused declare_workflow: writing a workflow is the Supervisor's alone (the caller is "
                         f"{role or 'none'!r})")
    p = _plugin()
    prov = design.provenance(store, a)
    root = a.get("root") or "state"
    if root not in ROOTS:
        raise StoreError("root is state (the state directory, the default) or repository (the repository's .orchestra/, "
                         "only when the owner says so)")
    if root == "repository" and prov.get("author") != design.OWNER:
        raise StoreError("root repository is the owner's choice: quote their words saying it belongs in the repository, "
                         "or leave root out (the state directory)")
    definition = a.get("definition")
    if not isinstance(definition, dict):
        raise StoreError("definition is the workflow as an object: {name, starts, steps: [{name, who, ...}], ...}")
    definition, local = _local_zone(definition)
    project.reload_config()
    o = project.orchestra
    refusing = list(p.problems(o, definition))
    if not refusing:
        try:
            written = p.define.write(o, definition, root=root)
        except p.define.DefinitionError as e:
            refusing = list(e.problems) or [str(e)]
    if refusing:
        raise StoreError(f"declare_workflow refused, nothing written: the Workflows plugin found {design.plural(len(refusing), 'problem')}. "
                         + " ".join(f"({i}) {s.rstrip('.')}." for i, s in enumerate(refusing, 1))
                         + " Fix each in the definition and declare it again.")
    name = definition.get("name")
    root = getattr(written, "root", root)       # the copy that is read: the repository's when it holds the workflow (T2f s10)
    out = {"ok": True, "workflow": name, "root": root, "path": str(written.path), "notes": list(written.notes),
           "author": prov.get("author"), "shown": SHOWN}
    if root == "repository":
        out["committed"] = False
        out["next"] = gate(f"The file is in the checkout, uncommitted ({_rel(o, written.path)}), and live there now. "
                           "[[tasking]]Hand it to the Coordinator (hand_to_coordinator) to land it through a ticket and a pull "
                           "request. [[/tasking]]Tell the owner where it is and that it applies now in this checkout; never "
                           "ask them to commit, discard or land it.")
    if runs := clock_words(definition.get("starts")):
        out["clock"] = runs + ": say this run time, and no other"
    if local:
        out["zone"] = (f"{local[0]!r} is this machine's zone, {local[1]}: declared {local[2]} {local[1]}. Tell the owner "
                       "that, and do not ask for the zone again")
    if written.notes:
        out["lacks"] = "What the project still lacks for it is in notes: nothing is offered for such a step until it is there."
    return out


def _local_zone(definition: dict) -> tuple[dict, tuple | None]:
    """The definition with an ``every`` start's ``tz`` given as the owner answered the zone question
    ("this machine's clock", "local", "here") set to the host's zone's IANA name, and what was
    resolved (their words, the zone, the start's ``at``), or None when nothing was."""
    starts, got = definition.get("starts"), None
    if not isinstance(starts, list):
        return definition, None
    out = []
    for s in starts:
        if isinstance(s, dict) and "every" in s and isinstance(s.get("tz"), str):
            try:
                tz = resolve_tz(s["tz"])
            except ConfigError as e:
                raise StoreError(str(e)) from None
            if tz != s["tz"]:
                got = (s["tz"], tz, s.get("at") or "00:00")
                s = {**s, "tz": tz}
        out.append(s)
    return ({**definition, "starts": out} if got else definition), got


def clock_words(starts) -> str | None:
    """When a workflow's clock starts its items, in words, from its ``every`` start as declared
    (the plugin counts slots from 00:00 UTC with no ``at``, from ``at`` in ``tz`` with one, in the
    project's local zone when ``tz`` is unset): the run time a reply may state, and no other."""
    every = next((s for s in starts or [] if isinstance(s, dict) and "every" in s), None)
    if every is None:
        return None
    if not every.get("at") and not every.get("tz"):
        return (f"every {every['every']}, counted from 00:00 UTC (no time of day declared: with a time the owner "
                "named, declare at and tz)")
    zone = every.get("tz") or f"the project's local zone ({time.strftime('%Z')}, no tz declared)"
    return f"every {every['every']} at {every.get('at') or '00:00'} {zone}"


def _rel(o, path) -> str:
    try:
        return str(path.relative_to(o.repo_root))
    except ValueError:
        return str(path)


def _who(step) -> dict:
    who = step.actor
    return {who.kind: who.name}


def list_workflows(project: Project, a: dict) -> dict:
    p = _plugin()
    project.reload_config()
    o = project.orchestra
    defs = p.define.load(o)
    roots = list(p.roots(o))

    def where(name: str) -> str:
        for label, root in roots:
            if (root / "workflows" / name / "workflow.json").is_file():
                return f"{label}/workflows/{name}/workflow.json"
        return "?"

    workflows = []
    for w in defs.workflows:
        steps = []
        for s in w.steps:
            step = {"name": s.name, "who": _who(s)}
            if s.needs:
                step["needs"] = list(s.needs)
            step["outcomes"] = dict(s.outcomes) if s.outcomes is not None else "any -> the next step"
            raw = next((x for x in (w.raw.get("steps") or []) if isinstance(x, dict) and x.get("name") == s.name), {})
            for k in ("done_when", "guards", "on", "depends"):
                if raw.get(k):
                    step[k] = raw[k]
            steps.append(step)
        runs = clock_words(w.raw.get("starts"))
        workflows.append({"name": w.name, "where": where(w.name), "planner": w.planner,
                          "starts": w.raw.get("starts") or [], **({"clock": runs} if runs else {}), "steps": steps})
    return {"ok": True, "workflows": workflows, "problems": list(defs.problems), "notes": list(defs.notes),
            "shown": "The Items page (/items) shows their items; the Dynamics page shows their piles as stocks."}


def _prov(p: dict | None) -> str:
    if not p:
        return ""
    return " ".join(str(x) for x in (p.get("source"), p.get("time")) if x)


def explain_item(project: Project, a: dict) -> dict:
    p = _plugin()
    item = str(a.get("item") or "").strip()
    if not item:
        raise StoreError("item is the item's id (its subject)")
    try:
        last = max(1, min(int(a.get("last") or LAST), 100))
    except (TypeError, ValueError):
        raise StoreError("last is a number of facts (default 15)") from None
    rec = p.Record.for_project(project.orchestra)
    ex = rec.explain(item)
    if ex is None:
        raise StoreError(f"the Workflows record holds no item {item!r}: list_workflows shows what starts one, and the Items "
                         "page lists the open ones")
    fields = {k: {"value": v.get("value"), "set_by": _prov(v.get("set_by")),
                  **({"outranked": len(v["outranked"])} if v.get("outranked") else {})}
              for k, v in (ex.get("fields") or {}).items()}
    row = next(iter(rec.items(lambda s: s.get("item") == item)), None) or {}     # its index row, while open
    positions = row.get("at") or {}
    keep = ("step", "pile", "since", "wait", "holder", "rounds", "finished", "outcome", "to")
    at = {w: {k: pos[k] for k in keep if isinstance(pos, dict) and pos.get(k) not in (None, "", [], {})}
          for w, pos in positions.items()}
    for pos in at.values():
        if pos.get("wait"):
            pos["wait"] = wait_of(pos["wait"])
    not_counted = [
        {"workflow": w, **x} if isinstance(x, dict) else {"workflow": w, "fact": x}
        for w, pos in positions.items() if isinstance(pos, dict) for x in pos.get("not_counted") or []]
    timeline = ex.get("timeline") or []
    out = {"ok": True, "item": item, "summary": _summary(row, rec.item(item) or {}), "fields": fields, "positions": at,
           "not_counted": not_counted[-10:], "facts": ex.get("facts"), "last_facts": timeline[-last:]}
    for k in ("relations", "ended", "requested"):
        if ex.get(k):
            out[k] = ex[k]
    return out


OPEN_SHOWN, CLOSED_SHOWN = 40, 10
ROW_KEYS = ("item", "title", "step", "pile", "held", "wait", "holder", "since", "age_s", "reworked", "last_step",
            "finished", "outcome")


def _exit(x: dict) -> str:
    """One exit of a wait (the plugin's ``for``) in words: "github.pr.commented with state = commented
    (to sail)"."""
    data = ", ".join(f"{k} = {v if isinstance(v, str) else json.dumps(v)}" for k, v in (x.get("data") or {}).items())
    to = f" (to {'the end' if x.get('to') is None else x['to']})" if x.get("via") == "on" else ""
    return f"{x.get('event') or x.get('when') or '?'}{' of a child' if x.get('of') else ''}{f' with {data}' if data else ''}{to}"


def wait_of(w) -> dict | None:
    """A wait as the tools show it: its reason, by and until, the fact it entered on (``entered_on``;
    ``fact`` from a plugin that serves only the old name) and what ends it, in words ("until
    github.pr.approved, or github.pr.commented with state = commented (to sail)"): the plugin's
    ``for``, so a review a stand-in's comment also ends is never told as waiting for an approval alone."""
    if not isinstance(w, dict):
        return None
    out = {k: w[k] for k in ("reason", "by", "until", "kind") if w.get(k) not in (None, "", [], {})}
    if w.get("entered_on") or w.get("fact"):
        out["entered_on"] = w.get("entered_on") or w.get("fact")
    exits = [x for x in w.get("for") or [] if isinstance(x, dict)]
    if exits:
        out["ends_on"] = "until " + ", or ".join(_exit(x) for x in exits)
    return out or None


def _row(r: dict) -> dict:
    out = {k: r[k] for k in ROW_KEYS if r.get(k) not in (None, "", [], {}, False, 0)}
    if r.get("closed"):                                   # an item.closed is the end: nothing waits on it
        out.pop("wait", None)
    elif out.get("wait"):
        out["wait"] = wait_of(out["wait"])
    fields = {k: v for k, v in (r.get("fields") or {}).items() if isinstance(v, str | int | float) and len(str(v)) <= 200}
    return out | ({"fields": fields} if fields else {})


OUTSIDE_NOTE = ("waiting_outside: the open items at a step that waits on an outside system (CI, a review, SAIL), by step "
                "and system. An incident on a service such a step waits on holds the items at that step: name them. The "
                "Workflows plugin serves no setting naming the service a step's system runs on, so never say an incident "
                "leaves them unaffected: name them as the items it may hold")


def _outside_steps(defs) -> dict[tuple[str, str], str]:
    """(workflow, step) -> the outside system the step waits on, from the loaded definitions."""
    out = {}
    for w in getattr(defs, "workflows", None) or []:
        for st in w.steps:
            actor = getattr(st, "actor", None)
            if getattr(actor, "kind", None) == "outside":
                out[(w.name, st.name)] = actor.name
    return out


def _waiting_outside(workflow: str | None, rows: list[dict], systems: dict[tuple[str, str], str]) -> list[dict]:
    """The open items at each step that waits on an outside system: the step's actor, or the wait's
    own kind and by when the definition is not loaded."""
    by: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        wait = r.get("wait") if isinstance(r.get("wait"), dict) else {}
        system = systems.get((workflow, r.get("step"))) or (wait.get("by") if wait.get("kind") == "outside" else None)
        if not system or not r.get("step"):
            continue
        fields = _row(r).get("fields") or {}
        by.setdefault((r["step"], system), []).append({"item": r.get("item"), **({"fields": fields} if fields else {})})
    return [{"workflow": workflow, "step": step, "system": system, "items": items} for (step, system), items in by.items()]


def list_items(project: Project, a: dict) -> dict:
    p = _plugin()
    if getattr(p, "item_list", None) is None:
        raise StoreError("this Workflows plugin has no Items list to read: explain_item reads one item by its id, and the "
                         "Items page (/items) lists them")
    project.reload_config()
    o = project.orchestra
    workflow = str(a.get("workflow") or "").strip() or None
    defs = p.define.load(o)
    got = p.item_list(p.Record.for_project(o), defs, time.time(), workflow=workflow)
    systems = _outside_steps(defs)
    out, outside = [], []
    for w in got.get("workflows") or []:
        rows = w.get("items") or []
        live = [_row(r) for r in rows if not r.get("closed")]
        done = [_row(r) for r in rows if r.get("closed")]
        out.append({"workflow": w.get("workflow"), "steps": w.get("steps"), "open": live[:OPEN_SHOWN],
                    "closed_recently": done[:CLOSED_SHOWN],
                    **({"more_open": len(live) - OPEN_SHOWN} if len(live) > OPEN_SHOWN else {})})
        outside += _waiting_outside(w.get("workflow"), [r for r in rows if not r.get("closed")], systems)
    return {"ok": True, "open": got.get("open"), "workflows": out, "waiting_outside": outside,
            "problems": got.get("problems") or [],
            "outside_note": OUTSIDE_NOTE,
            "shown": "The Items page (/items) lists the same; explain_item says why one item is where it is, fact by fact. "
                     "An item whose wait names the owner (an approval, attention) needs them; one waiting long on an "
                     "outside system (CI, a review, a check) is stuck there: say which, with its fields (a ticket's key, "
                     "a pull request's number). A wait's ends_on is every event that ends it, as the workflow declares: "
                     "name them all, never one as the only way on. An item is ended by item.closed (on the bus, in "
                     "read_events; the floor's for a workflow's item names the workflow, its last_step and outcome): a "
                     "closed item is in closed_recently, never waiting, and nothing waits on it."}


def _summary(row: dict, snap: dict) -> dict:
    """The item in a few values: the index's row while it is open, else its snapshot's."""
    got = {"item": snap.get("item"), "open": bool(row), "facts": snap.get("facts"), "first": snap.get("first"),
           "last": snap.get("last"), "closed": row.get("closed") or sorted(snap.get("ended") or {}),
           "requested": row.get("requested") or {k: v for k, v in (snap.get("requested") or {}).items() if k != "id"},
           "wait": wait_of(row.get("wait")), "holder": row.get("holder"), "children": snap.get("children")}
    return {k: v for k, v in got.items() if v not in (None, "", [], {}) or k == "open"}


READS = {"list_workflows": list_workflows, "list_items": list_items, "explain_item": explain_item}
COMMANDS = frozenset({"declare_workflow", *READS})


def handle(project: Project, store: AgentStore, command: str, a: dict, role: str) -> dict:
    if command == "declare_workflow":
        return declare_workflow(project, store, a, role)
    return READS[command](project, a)
