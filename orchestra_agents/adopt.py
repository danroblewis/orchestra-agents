"""Tracking what was already under way when a workflow begins (T44, bootstrap run 8's finding 4):
a ticket assigned before its poller began is never relayed as ``jira.issue.assigned``, so a
``ticket`` workflow that starts on that event never had an item for it, and its pull requests
related to a parent that did not exist.

**The adopt rule** is the Supervisor's, declared beside a workflow (``declare_workflow``'s
``adopt``): which of a poller's event types report a ticket as it is **now** (each ``jira.issue.*``
carries the issue's ``status`` and ``category`` when the poll read it), which of those fields say it
is under way (``data``: a plain value or ``{"not": [values]}``), and which step each value of one
field implies (``at``: ``{"status": {"In Review": "review"}}``; any other value: the first step).
The Workflows plugin has no such start (its items start only at their first step, on a start
event, a request or the clock), so the rule is kept here, in the Supervisor's agent state, and
**compiled into the definition in the plugin's own words**: a ``{"request": true}`` start, and on
the first step an ``on`` for each step the rule can imply, moved by the Supervisor's own
``supervisor.adopted`` about the item (``{"workflow": <name>, "step": <step>}``; an ``on`` at an
outside system's step counts another system's type from that system's machine, and this type's
system is the Supervisor).

**Adopting** a ticket is the Supervisor's acts on the bus, never a poller's event made up:
``item.requested`` naming the workflow (``adopted: true``, ``from``: the poller's event that
reported it), ``item.fact`` setting ``adopted: "at tracking start"`` and the field's value, and,
for a step past the first, ``supervisor.adopted``. At the declaration every ticket the bus holds a
report of (the newest ``SCAN`` events) whose newest report says it is under way, that no start
refused (assigned to someone else) and that the Workflows record does not know yet is adopted
(one a start admitted is adopted too: the recorder read it before the workflow existed). After it,
the host's ``Adopter`` (as the Supervisor) adopts each ticket the poller first reports under way
later (HK-101 in run 8: assigned before the poller began, first relayed by its move to In
Progress), unless a start admits it there (the floor starts that one) or it was seen already."""

from __future__ import annotations

import re
from typing import Any

from .store import StoreError

SUPERVISOR = "supervisor"
STATE = "adopting"               # the Supervisor's agent state: {workflow: {rule, at, seen}}
MOVE = "supervisor.adopted"      # the Supervisor's own fact that places an adopted item at its step
FIELD = "adopted"
SAID = "at tracking start"
SCAN = 20_000                    # the newest events read at the declaration
PAGE = 5_000
SEEN_KEPT = 5_000
TYPE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
ADOPTED, STARTED, REFUSED = "adopted", "started", "refused"


def _problem(text: str) -> StoreError:
    return StoreError(f"declare_workflow refused, nothing written: adopt {text}")


def rule_of(raw: Any, definition: dict) -> dict:
    """The adopt rule as given, checked against the definition, or the refusal saying what is wrong."""
    if not isinstance(raw, dict) or not raw:
        raise _problem("is {event: [the poller's types that report a ticket now], data: {field: value or "
                       "{not: [values]}}, at: {field: {value: step}}}")
    if extra := sorted(set(raw) - {"event", "data", "at"}):
        raise _problem(f"has {', '.join(map(repr, extra))}, which is not one of its words: event, data, at")
    types = raw.get("event")
    types = [types] if isinstance(types, str) else types
    if not isinstance(types, list) or not types or not all(isinstance(t, str) and TYPE.match(t) for t in types):
        raise _problem("event is the poller's event types that carry the ticket as it is now "
                       "([\"jira.issue.transitioned\", \"jira.issue.created\"])")
    data = raw.get("data") or {}
    if not isinstance(data, dict) or not all(_cond_ok(v) for v in data.values()):
        raise _problem("data maps each field of the poller's event to a plain value or {\"not\": [values]} "
                       "({\"category\": {\"not\": [\"new\", \"done\"]}})")
    steps = [s.get("name") for s in definition.get("steps") or [] if isinstance(s, dict)]
    at = raw.get("at") or {}
    if at:
        ok = isinstance(at, dict) and len(at) == 1 and isinstance(next(iter(at.values())), dict)
        if not ok:
            raise _problem("at names one field of the poller's event and the step each of its values implies "
                           "({\"status\": {\"In Review\": \"review\"}}); any other value: the first step")
        bad = sorted({s for s in next(iter(at.values())).values() if s not in steps}, key=str)
        if bad:
            raise _problem(f"at names {', '.join(map(repr, bad))}, which is no step of {definition.get('name')!r} "
                           f"(its steps: {', '.join(map(str, steps))})")
    starts = sorted({s["event"] for s in definition.get("starts") or [] if isinstance(s, dict) and isinstance(s.get("event"), str)})
    return {"event": sorted(set(types)), "data": data, "at": at, "starts": starts}


def _cond_ok(v) -> bool:
    if isinstance(v, dict):
        return list(v) == ["not"] and isinstance(v["not"], list)
    return isinstance(v, str | int | float | bool) or v is None


def compiled(definition: dict, rule: dict | None) -> dict:
    """The definition in the plugin's own words: with a rule, a request start and the first step's
    ``on`` moves to each step it can imply; without one, its own ``supervisor.adopted`` moves gone."""
    name = definition.get("name")
    steps = [dict(s) if isinstance(s, dict) else s for s in definition.get("steps") or []]
    if not steps or not isinstance(steps[0], dict):
        return definition
    first = steps[0]
    on = [o for o in first.get("on") or [] if not (isinstance(o, dict) and o.get("event") == MOVE)]
    starts = list(definition.get("starts") or [])
    if rule:
        mapped = [s for s in dict.fromkeys((next(iter(rule["at"].values())) if rule.get("at") else {}).values())
                  if s != first.get("name")]
        on += [{"event": MOVE, "data": {"workflow": name, "step": s}, "to": s} for s in mapped]
        if not any(isinstance(s, dict) and s.get("request") is True for s in starts):
            starts.append({"request": True})
    if on:
        first["on"] = on
    else:
        first.pop("on", None)
    return {**definition, "starts": starts, "steps": [first, *steps[1:]]}


def _matches(data: dict, conds: dict) -> bool:
    for k, want in conds.items():
        if isinstance(want, dict):
            if k in data and data[k] in want["not"]:
                return False
        elif data.get(k) != want:
            return False
    return True


def step_of(rule: dict, data: dict, first: str) -> tuple[str, str | None, Any]:
    """The step a report implies, and the field and value it was read from."""
    if not rule.get("at"):
        return first, None, None
    field, steps = next(iter(rule["at"].items()))
    value = data.get(field)
    return steps.get(str(value), first) if value is not None else first, field, value


def fold(evs: list[dict], rule: dict, admit, seen: dict[str, str]) -> dict[str, dict]:
    """Each ticket's newest report among ``evs`` that says it is under way (None when its newest
    says it is not: done, say), with ``seen`` updated: a start that admitted a ticket (the floor
    starts it) or refused it (assigned to someone else) marks it, and a refusal holds until a start
    admits it."""
    last: dict[str, dict | None] = {}
    starts, types = set(rule.get("starts") or ()), set(rule["event"])
    for ev in evs:
        subject, t = ev.get("subject"), ev.get("type")
        if not isinstance(subject, str) or not subject:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if t in starts:
            if admit(ev):
                seen[subject] = STARTED if seen.get(subject) != ADOPTED else ADOPTED
                last[subject] = ev if _matches(data, rule["data"]) else None
            elif seen.get(subject) not in (STARTED, ADOPTED):
                seen[subject] = REFUSED
            continue
        if t in types:
            last[subject] = ev if _matches(data, rule["data"]) else None
    return {s: e for s, e in last.items() if e is not None and seen.get(s) != REFUSED}


def admitter(p, o, name: str):
    """Whether one of the workflow's starts admits an event (the plugin's own match, with the
    project's settings' values), or None when the workflow is not loaded."""
    wf = p.define.load(o).get(name)
    if wf is None or getattr(p, "admits", None) is None:
        return None
    values = (p.settings(o) or {}).get(name) if getattr(p, "settings", None) else None
    return lambda ev: p.admits(wf, ev, values)


def known(rec, subject: str) -> bool:
    try:
        return rec.item(subject) is not None
    except (OSError, ValueError):
        return False


def publish(events, name: str, first: str, subject: str, ev: dict, rule: dict) -> dict:
    """Adopt one ticket: the Supervisor's request of its item, its fields, and its step."""
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    step, field, value = step_of(rule, data, first)
    said = f"{field} {value}" if field else "under way"
    events.emit("item.requested", src=SUPERVISOR, about=subject, workflow=name, adopted=True,
                brief=f"{subject} was {said} when tracking began: adopted at {step} (not a new assignment, not a replay)",
                **{"from": ev.get("id")})
    facts = {FIELD: SAID, **({field: value} if field else {})}
    events.emit("item.fact", src=SUPERVISOR, about=subject, set=facts)
    if step != first:
        events.emit(MOVE, src=SUPERVISOR, about=subject, workflow=name, step=step, adopted=True,
                    **{"from": ev.get("id"), **({field: value} if field else {})})
    return {"item": subject, "step": step, **({field: value} if field else {}), "from": ev.get("id")}


def pages(events, start: int, end: int) -> list[dict]:
    """The raw envelopes between two offsets, page by page."""
    out, at = [], start
    while at < end:
        evs, nxt = events.page(at, min(PAGE, end - at))
        out += evs
        if nxt <= at:
            break
        at = nxt
    return out


def _trim(seen: dict) -> dict:
    return dict(list(seen.items())[-SEEN_KEPT:])


def declared(p, o, store, name: str, first: str, rule: dict | None) -> list[dict]:
    """At the declaration: keep (or drop) the rule, and adopt every ticket the bus already reports
    under way that the record does not know. The tickets adopted, each with its step."""
    events = store.events
    if rule is None:
        store.update_agent_state(SUPERVISOR, STATE, lambda st: {k: v for k, v in (st or {}).items() if k != name})
        return []
    end = events.end()
    seen: dict[str, str] = dict(((store.agent_state(SUPERVISOR).get(STATE) or {}).get(name) or {}).get("seen") or {})
    admit = admitter(p, o, name) or (lambda ev: False)
    reports = fold(pages(events, max(0, end - SCAN), end), rule, admit, seen)
    rec = p.Record.for_project(o)
    out = []
    for subject, ev in reports.items():
        if seen.get(subject) in (REFUSED, ADOPTED) or known(rec, subject):
            continue
        out.append(publish(events, name, first, subject, ev, rule))
        seen[subject] = ADOPTED
    state = {"rule": rule, "first": first, "at": events.end(), "seen": _trim(seen)}
    store.update_agent_state(SUPERVISOR, STATE, lambda st: {**(st or {}), name: state})
    return out


def kept(store, name: str) -> dict | None:
    """The rule kept for the workflow, if any (a re-declaration without ``adopt`` keeps it)."""
    return (((store.agent_state(SUPERVISOR).get(STATE) or {}).get(name)) or {}).get("rule")


def listened(store) -> list[str]:
    """The event types the host reads on the Supervisor's behalf to adopt tickets."""
    rules = store.agent_state(SUPERVISOR).get(STATE) or {}
    return sorted({t for st in rules.values() for t in (st.get("rule") or {}).get("event") or []}
                  | {t for st in rules.values() for t in (st.get("rule") or {}).get("starts") or []})


class Adopter:
    """The host's part, as the Supervisor: each ticket a poller first reports under way after the
    declaration, adopted, once."""

    def __init__(self, store, plugin, log=None):
        self.store, self.plugin, self.log = store, plugin, log or (lambda _m: None)

    def tick(self, project) -> list[dict]:
        rules = self.store.agent_state(SUPERVISOR).get(STATE) or {}
        if not rules:
            return []
        events = self.store.events
        end = events.end()
        p = rec = o = None
        done: list[dict] = []
        updates: dict[str, dict] = {}
        for name, st in rules.items():
            at = int(st.get("at") or 0)
            if at >= end:
                continue
            rule = st.get("rule") or {}
            types = set(rule.get("event") or ()) | set(rule.get("starts") or ())
            mine = [e for e in pages(events, at, end) if e.get("type") in types]
            if not mine:
                updates[name] = {"at": end}
                continue
            if p is None:
                p = self.plugin()
                if p is None:
                    return []
                o = project.orchestra
                rec = p.Record.for_project(o)
            admit = admitter(p, o, name)
            if admit is None:                       # not loaded yet: read these again next tick
                continue
            seen = dict(st.get("seen") or {})
            for subject, ev in fold(mine, rule, admit, seen).items():
                if seen.get(subject) in (REFUSED, ADOPTED, STARTED) or known(rec, subject):
                    continue
                done.append({"workflow": name, **publish(events, name, st.get("first") or "", subject, ev, rule)})
                seen[subject] = ADOPTED
            updates[name] = {"at": end, "seen": _trim(seen)}
        if updates:                                 # never over a re-declaration that read further meanwhile
            self.store.update_agent_state(SUPERVISOR, STATE, lambda cur: {
                k: ({**v, **updates[k]} if k in updates and int(v.get("at") or 0) <= end else v)
                for k, v in (cur or {}).items()})
        for d in done:
            self.log(f"adopted {d['item']} into {d['workflow']} at {d['step']}")
        return done
