"""``request_work``: start a piece of work on a workflow (the ``work`` tool group, the Supervisor's
and the Coordinator's). It publishes the Workflows plugin's ``item.requested`` (its machine
contract: ``workflow``, ``brief``, ``parent``; subject the new item's id), then, when fields are
given, one ``item.fact`` setting them, both under the same source. Nothing else is written.

The source is whose request it is (the rule the design tools follow for provenance,
``design.provenance``, and Orchestra's for the owner's page requests, ``orchestra.intent.requested``
from ``owner``):

- the Supervisor asks only for the owner, so its call must quote the owner's words in their message
  of this turn (``design.quote_in``), and the request goes out as ``owner``, with ``via:
  supervisor``; without a verified quote it is refused (starting work is the owner's call, never
  the Supervisor's own proposal);
- the Coordinator plans work, a planner by the plugin's contract: its request is its own, as
  ``coordinator``.

Refused, with nothing published: the plugin not installed; no ``orchestra run`` launching the
project, or its floor (``workflows.floor``) not running, since nothing would offer the item; a
workflow the plugin does not load, or one that a request does not start; an id the record already
holds. The answer names the item, the Items page that shows it, ``explain_item`` to follow it, and
what runs (``runs``): the first step at once, apart from any ``every`` start of the workflow.

**One-off work** (2026-11-04, T54: code work without the task system): ``role`` in place of
``workflow`` publishes the contract's role-only ``item.requested`` (``role``, ``brief``, ``needs``,
``planner``), which the floor offers once to that role and closes when the turn finishes. Code work
is ``role: worker`` with ``needs: ["worktree"]``: the worker commits on the item's branch in its
worktree, and pushes only if ``[agents] allow_push`` names the role. The answer says when no agent
fills the role here (``floor_roles.served``), so the Supervisor proposes ``set_agent_roles``."""

from __future__ import annotations

import re
import secrets
from urllib.parse import quote as urlquote

from orchestra import runtime

from . import design, workflow_tools
from .config import Project
from .store import AgentStore, StoreError

FLOOR = "workflows.floor"            # the plugin's process, as the launcher names it (<owner>.<name>)
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,79}$")
REQUESTERS = ("supervisor", "coordinator")


def floor_down(project: Project) -> str | None:
    """Why no floor would offer a new item now, or None while it runs."""
    rec = runtime.read(project.orchestra)
    if runtime.launcher(rec) is None:
        return ("no orchestra run launches this project now, so the Workflows floor is not running and nothing would "
                "offer the item: tell the owner to start it (orchestra run in the repository), then ask again")
    p = (rec.get("processes") or {}).get(FLOOR) or {}
    if p.get("state") != "running" or not runtime.alive(p.get("pid")):
        state = p.get("state") or "not launched"
        return (f"the Workflows floor ({FLOOR}) is {state}, so nothing would offer the item: list_processes and "
                f"read_machine_log {FLOOR} say why; fix that first, then ask again")
    return None


def _workflow(p, project: Project, name: str):
    defs = p.define.load(project.orchestra)
    w = next((w for w in defs.workflows if w.name == name), None)
    names = ", ".join(sorted(x.name for x in defs.workflows)) or "none"
    if w is None:
        raise StoreError(f"no workflow {name!r} is loaded (the loaded ones: {names}); list_workflows shows each with "
                         "what starts it")
    if not getattr(w, "request", True):
        raise StoreError(f"workflow {name!r} is not started by a request (its starts: {w.raw.get('starts')}); "
                         "list_workflows shows what starts it")
    return w


def runs_now(w, item: str) -> str:
    """What happens to a requested item, in the owner's words: the floor offers its first step at once;
    a clock start (``every``) is a separate run of its own, never this one's wait."""
    steps = getattr(w, "steps", ()) or ()
    first = getattr(steps[0], "name", None) if steps else None
    out = f"started now as item {item}; " + (f"its first step {first} runs at once" if first else "it runs at once")
    every = next((s for s in (getattr(w, "raw", {}) or {}).get("starts") or []
                  if isinstance(s, dict) and s.get("every")), None)
    if every:
        at = f" at {every['at']}" + (f" {every['tz']}" if every.get("tz") else "") if every.get("at") else ""
        out += f"; the workflow's every {every['every']}{at} start is a separate run, not this one"
    return out


def _fields(a: dict) -> dict:
    fields = a.get("fields")
    if fields in (None, {}):
        return {}
    if not isinstance(fields, dict) or not all(isinstance(k, str) and k for k in fields):
        raise StoreError("fields is an object of the item's fields, {name: value}: {\"priority\": 2, \"area\": \"api\"}")
    return fields


def request_work(project: Project, store: AgentStore, a: dict, role: str) -> dict:
    if role not in REQUESTERS:
        raise StoreError(f"refused request_work: asking for work is the Supervisor's (for the owner) and the "
                         f"Coordinator's (the caller is {role or 'none'!r})")
    p = workflow_tools._plugin()
    name = str(a.get("workflow") or "").strip()
    brief = str(a.get("brief") or "").strip()
    one_off = str(a.get("role") or "").strip()
    if name and one_off:
        raise StoreError("give workflow (an item of that workflow) or role (a one-off item for that role), not both")
    if not name and not one_off:
        raise StoreError("workflow is the name of a loaded workflow (list_workflows); or, for one piece of work with no "
                         "workflow, role (worker, for code work) with needs [\"worktree\"]")
    if len(brief) < 10:
        raise StoreError("brief is the work asked for, in a sentence or more: it is what the first step's machine is "
                         "given")
    parent = str(a.get("parent") or "").strip() or None
    fields = _fields(a)
    if role == design.MACHINE:
        prov = design.provenance(store, a)
        if prov.get("author") != design.OWNER:
            raise StoreError("starting work is the owner's to ask: quote their words in their message of this turn "
                             "(quote), the request then goes out as theirs; never start work on your own")
        source, extra = design.OWNER, {"via": design.MACHINE}
    else:
        if len(str(a.get("reason") or "").strip()) < 10:
            raise StoreError("give the reason in a sentence: which plan this item is part of")
        source, extra = role, {}
    needs = _needs(a) if one_off else []
    project.reload_config()
    w = _workflow(p, project, name) if name else None
    item = str(a.get("item") or "").strip() or f"{name or one_off}-{secrets.token_hex(3)}"
    if not ID.match(item):
        raise StoreError("item is the new item's id: letters, digits and . _ : @ -, up to 80 (leave it out for a new one)")
    rec = p.Record.for_project(project.orchestra)
    if rec.item(item) is not None:
        raise StoreError(f"the Workflows record already holds an item {item!r} (an id is never used twice): leave item "
                         "out for a new id, or give another")
    if why := floor_down(project):
        raise StoreError(f"request_work refused, nothing published: {why}")
    if one_off:
        data = {"role": one_off, "brief": brief, **({"needs": needs} if needs else {}), "planner": "supervisor"}
    else:
        data = {"workflow": name, "brief": brief}
    data = {**data, **({"parent": parent} if parent else {}), **extra}
    ev = store.events.publish("item.requested", data, source=source, subject=item)
    out = {"ok": True, "item": item, **({"role": one_off} if one_off else {"workflow": name}), "source": source,
           "requested": ev.get("id"),
           "shown": f"The Items page shows it: /items#item={urlquote(item, safe='')}; explain_item with item {item!r} says "
                    "where it is and why.",
           "runs": runs_now(w, item) if w is not None else f"offered now to the role {one_off} as item {item}, once",
           "say": f"Tell the owner the item's id, {item}, where to watch it, and what runs says: it starts now, "
                  "never on the workflow's schedule."}
    if one_off:
        out.update(_one_off_notes(project, one_off, item, needs))
        if "say_branch" in out:
            out["say"] += " " + out.pop("say_branch")
    if parent:
        out["parent"] = parent
    if fields:
        store.events.publish("item.fact", {"set": fields}, source=source, subject=item)
        out["fields"] = sorted(fields)
    return out


def _needs(a: dict) -> list[str]:
    needs = a.get("needs")
    if needs in (None, []):
        return []
    if not isinstance(needs, list) or not all(isinstance(n, str) and n for n in needs):
        raise StoreError("needs is a list of resource kinds, [\"worktree\"]")
    return list(needs)


def _one_off_notes(project: Project, role: str, item: str, needs: list[str]) -> dict:
    """What the owner is told of a one-off item: the branch its worktree is on, that nothing is
    pushed unless ``allow_push`` names the role, and whether any agent here fills the role."""
    from .floor_roles import served
    out: dict = {}
    if "worktree" in needs:
        pushes = role in (project.config.allow_push or [])
        out["branch"] = (f"the {role} commits on the item's branch, workflows/{item}-<hash>, in its own worktree (explain_item "
                         "shows it); " + (f"[agents] allow_push names {role}, so its session may push" if pushes else
                                          f"nothing is pushed: [agents] allow_push does not name {role}"))
        out["say_branch"] = "Name the branch and say " + ("that it may push (allow_push)." if pushes else "that nothing is pushed.")
    if role not in served(project):
        out["unfilled"] = (f"no AI agent fills the role {role} here ([agents] serves): unless a program of the role "
                           "announces itself, nobody claims the item. Propose in one sentence that the AI agents fill it "
                           "(set_agent_roles, the owner's yes quoted), on the same model")
    return out
