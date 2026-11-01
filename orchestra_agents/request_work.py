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
holds. The answer names the item, the Items page that shows it, and ``explain_item`` to follow it."""

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
    if not name:
        raise StoreError("workflow is the name of a loaded workflow (list_workflows)")
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
    project.reload_config()
    _workflow(p, project, name)
    item = str(a.get("item") or "").strip() or f"{name}-{secrets.token_hex(3)}"
    if not ID.match(item):
        raise StoreError("item is the new item's id: letters, digits and . _ : @ -, up to 80 (leave it out for a new one)")
    rec = p.Record.for_project(project.orchestra)
    if rec.item(item) is not None:
        raise StoreError(f"the Workflows record already holds an item {item!r} (an id is never used twice): leave item "
                         "out for a new id, or give another")
    if why := floor_down(project):
        raise StoreError(f"request_work refused, nothing published: {why}")
    data = {"workflow": name, "brief": brief, **({"parent": parent} if parent else {}), **extra}
    ev = store.events.publish("item.requested", data, source=source, subject=item)
    out = {"ok": True, "item": item, "workflow": name, "source": source, "requested": ev.get("id"),
           "shown": f"The Items page shows it: /items#item={urlquote(item, safe='')}; explain_item with item {item!r} says "
                    "where it is and why.",
           "say": f"Tell the owner the item's id, {item}, and where to watch it."}
    if parent:
        out["parent"] = parent
    if fields:
        store.events.publish("item.fact", {"set": fields}, source=source, subject=item)
        out["fields"] = sorted(fields)
    return out
