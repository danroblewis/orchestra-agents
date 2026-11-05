"""``clear_items``: the Supervisor clears the Workflows items, all or one workflow's, when the owner
says clear, reset, delete or start over with them (the owner, 2026-11-04, after loading several
hundred tickets as items: "I just want to clear the data. This shouldn't be so hard. People will
want to do this.").

It goes through the plugin's contract, never its files: ``items.clear_requested`` (orchestra-workflows
W19, SPEC §2.4; the name copied in ``floor.py``), with no subject, from the Supervisor, naming the
``workflow`` (none: every item), ``rebuild`` (read the bus again from its start for them, under the
current definitions) and the reason. The plugin's recorder clears them in its own tick while
``orchestra run`` runs, the floor withdraws their offers, and answers ``items.cleared`` as its
planner's dispatch with what it did. It is local: a write on this machine's record, never outside it.

- Without ``confirm`` it changes nothing and answers a dry run (the plugin's ``Record.clearing``): how
  many items, open and closed, of which workflows, and what remains.
- With ``confirm: true`` it publishes the request, then waits up to ``WAIT_S`` for the answer and
  reports it; without one in time it says the request is on the bus and what to check.
- Under ``[agents] ask = "outside"`` (the default) the owner's request is enough: it acts on its
  reason. Under ``always`` it needs the owner's quoted words (``quote``)."""

from __future__ import annotations

import time

from . import ask, design
from .config import Project
from .floor import CLEAR_REQUESTED, ITEMS_CLEARED
from .store import AgentStore, StoreError

ROLE = "supervisor"
WAIT_S = 15.0                 # how long the call waits for the floor's answer
POLL_S = 0.25
DROP = ("The step stocks on the Dynamics page drop to zero for them, and their move counters start again from "
        "zero (the history before stays in the metrics store)")


def _dry(rec, workflow: str | None) -> dict:
    if not hasattr(rec, "clearing"):
        raise StoreError("this Workflows plugin cannot clear items (it needs orchestra-workflows W19, items.clear_requested): "
                         "tell the owner it must be updated; never delete the item files or publish item.deleted per item")
    return rec.clearing(workflow)


def _loaded(p, project: Project) -> list[str]:
    try:
        return sorted(w.name for w in p.define.load(project.orchestra).workflows)
    except Exception:  # noqa: BLE001 - the dry run still answers from the record
        return []


def _answer(store: AgentStore, request: str, since: int, wait_s: float) -> dict | None:
    """The floor's ``items.cleared`` for ``request``, waited for up to ``wait_s``, or None."""
    end = time.monotonic() + wait_s
    while True:
        evs, _ = store.events.after(since)
        got = next((e for e in evs if e.get("type") == ITEMS_CLEARED and e.get("causationid") == request), None)
        if got is not None or time.monotonic() >= end:
            return got
        time.sleep(POLL_S)


def clear_items(project: Project, store: AgentStore, a: dict, role: str) -> dict:
    if role != ROLE:
        raise StoreError("only the supervisor clears the Workflows items")
    from .workflow_tools import _plugin
    p = _plugin()
    workflow = str(a.get("workflow") or "").strip() or None
    rebuild = a.get("rebuild", False)
    if not isinstance(rebuild, bool):
        raise StoreError("rebuild is true (make the items again from the bus's history, under the current definitions) or "
                         "false (the default: they are gone, and only new events make items)")
    project.reload_config()
    rec = p.Record.for_project(project.orchestra)
    dry = _dry(rec, workflow)
    loaded = _loaded(p, project)
    which = f"the workflow {workflow}'s" if workflow else "every"
    if not a.get("confirm"):
        out = {"ok": True, "dry_run": True, "workflow": workflow, "rebuild": rebuild, "would_clear": dry.get("items", 0),
               "open": dry.get("open", 0), "closed": dry.get("closed", 0), "workflows": dry.get("workflows") or {},
               "remain": dry.get("remain", 0),
               "next": (f"Nothing changed yet. Clearing {which} items removes {dry.get('items', 0)} "
                        f"({dry.get('open', 0)} open, {dry.get('closed', 0)} closed) and leaves {dry.get('remain', 0)}"
                        + ("; rebuild makes them again from the bus's history under the current definitions" if rebuild else
                           "; only new events make items after") + ". Call clear_items again with confirm: true to do it.")}
        if workflow and workflow not in loaded:
            out["note"] = (f"no workflow {workflow!r} is loaded now (loaded: {', '.join(loaded) or 'none'}): only the items the "
                           "record names it for are cleared")
        return out
    prov = design.provenance(store, a)
    if not ask.decided(project, prov):
        raise StoreError("refused, nothing published: clearing the items is the owner's choice here ([agents] ask = \"always\"): "
                         "quote their words asking to clear, reset, delete or start over with the items")
    data = {"rebuild": rebuild, "reason": prov["reason"], "by": prov.get("author") or ROLE,
            **({"workflow": workflow} if workflow else {}), **({"quote": prov["quote"]} if prov.get("quote") else {})}
    since = store.events.end()
    ev = store.events.publish(CLEAR_REQUESTED, data, source=ROLE)
    from .request_work import floor_down
    down = floor_down(project)
    got = None if down else _answer(store, ev["id"], since, WAIT_S)
    out = {"ok": True, "requested": ev["id"], "workflow": workflow, "rebuild": rebuild}
    if got is None:
        out["answer"] = None
        out["said"] = ((f"The request is on the bus, but {down}; it clears when the floor runs" if down else
                        f"The request is on the bus; the floor did not answer within {WAIT_S:g} s"
                        + (" (a rebuild reads the whole bus again, which can take a while)" if rebuild else ""))
                       + ". list_items shows the items still open; tell the owner it was asked and is not yet confirmed.")
        return out
    d = got                                             # as this package reads events: the data's fields (events.flat)
    out.update(cleared=d.get("cleared"), workflows=d.get("workflows") or {}, rebuilt=d.get("rebuilt"),
               remain=_dry(rec, None).get("items"))
    made = f", and made {d.get('rebuilt')} again from the bus's history under the current definitions" if rebuild else ""
    out["said"] = (f"Cleared {d.get('cleared')} items ({which}){made}; {out['remain']} items remain. {DROP}. Tell the owner "
                   "what was cleared, in one line, and that the step stocks on the Dynamics page dropped to zero"
                   + (" and fill again as the rebuilt items are counted" if rebuild else "") + ".")
    return out
