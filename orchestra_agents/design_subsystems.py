"""The owner's subsystems in words (``declare_subsystem``; Orchestra's ``docs/proposals/subsystems.md``
§5, its SPEC §4.6 and §10; the owner, 2026-11-02: boxes on the Dynamics and Machines maps, "a
machine should only belong to one subsystem", "sub 4. yes it can gather"). A subsystem is how the
maps are read, a box around a group of the one model, never a split of it: it binds no series and
changes no stock, flow, equation, hypothesis or key.

Three things the owner says, one tool:

- **A name for a label's value** ("call the pr_watch workflow 'release watch'"): the value found
  among the partitions Orchestra derives from the model's labels (``model.subsystems``, read in
  process through ``intended_api.partitions``), the label too when the owner said it. A value two
  labels carry is one question, never a guess. Naming a value another subsystem already names
  renames that one.
- **A gathering** ("put the stock between write and sail_check, the review → sail flow and bot.sail
  together as 'the sail line'"): each stock and flow found by the name the page shows, with its
  tags (``design_names.find``), machines by their id. When a member's words match two parts,
  nothing is declared and the answer is one question. A part already in another subsystem is
  refused, saying which: a part is in one subsystem at a time.
- **Dropping one** ("drop the sail line"): retracted, kept only in the history.

The name is the owner's: the call needs their quote, the name in it; nothing here makes one up.
Without a quote the call is the Supervisor's **proposal** (``author: supervisor``): only a label's
value, named by the value itself, and Orchestra keeps it ``proposed`` and draws no box until the
owner confirms it (``confirm subsystem:<id>``, quoted). The digest says an open proposal, or a
partition worth a name, in one line at most once a day (``subsystem_offer.py``)."""

from __future__ import annotations

from . import intended_api
from .config import Project
from .design import OWNER, _norm, answer, apply, recent_events
from .design_names import find, shown_as
from .store import AgentStore, StoreError

BOTH = "on both maps"          # the Dynamics diagram and the Machines page's event map


def _subs(layer: dict) -> dict[str, dict]:
    return dict(layer.get("subsystems") or {})


def _called(x: dict) -> str:
    return f"'{x.get('name')}' (subsystem {x.get('id')}" + (", proposed" if x.get("state") == "proposed" else "") + ")"


def existing(layer: dict, said: str) -> list[dict]:
    """The subsystems the owner's words name: by name or id, ignoring case."""
    said = {_norm(said), _norm(said).removeprefix("the ")}
    return [x for x in _subs(layer).values()
            if said & {_norm(x.get("name") or ""), _norm(x.get("name") or "").removeprefix("the "), _norm(x.get("id") or "")}]


def _free_id(layer: dict, name: str, keep: str | None = None) -> str:
    from orchestra.sd.intended.checks import make_slug
    if keep:
        return keep
    base, taken = make_slug(name)[:70], set(_subs(layer))
    eid, n = base, 1
    while eid in taken:
        n += 1
        eid = f"{base}_{n}"
    return eid


def _quoted_name(a: dict, prov: dict) -> str:
    name = " ".join((a.get("name") or "").split())
    if not name:
        raise StoreError("name: the owner's name for the subsystem, exactly in their words (or drop=true to take one off)")
    if prov.get("author") != OWNER:
        raise StoreError("a subsystem's name is the owner's: quote their words (quote), the name among them; without a quote "
                         "you may only propose a label's value under its own name. Nothing was declared")
    if _norm(name) not in _norm(prov.get("quote") or ""):
        raise StoreError(f"the name {name!r} is not in the owner's words you quoted: name a subsystem only with the name they "
                         "gave, never one you made. Nothing was declared")
    return name


def _values(model, value: str, label: str | None) -> list[tuple[str, str]]:
    """(label, value) of every derived partition's group the owner's value names."""
    v = value.strip()
    if "=" in v and not label:
        label, v = (x.strip() for x in v.split("=", 1))
    parts = intended_api.partitions(model).get("partitions") or []
    return [(p["label"], g["value"]) for p in parts for g in p["groups"]
            if _norm(g["value"]) == _norm(v) and (not label or _norm(p["label"]) == _norm(label))]


def _all_values(model) -> list[str]:
    return [f"{p['label']}={g['value']}" for p in intended_api.partitions(model).get("partitions") or [] for g in p["groups"]]


def _by_label(project: Project, layer: dict, model, a: dict, prov: dict, target: dict | None) -> dict:
    value, label = (a.get("value") or "").strip(), (a.get("label") or "").strip() or None
    hits = _values(model, value, label)
    if not hits:
        known = _all_values(model)
        raise StoreError(f"no partition of the model has a group {value!r}" + (f" by {label}" if label else "")
                         + (f"; the model's labels group {', '.join(known[:12])}" if known else
                            "; the model's series carry no label that groups two values")
                         + ". Nothing was declared")
    if len(hits) > 1:
        q = "Which do you mean: " + ", or ".join(f"{v} as its {lab}" for lab, v in hits) + "?"
        return _ask(q, [{"label": lab, "value": v} for lab, v in hits])
    label, value = hits[0]
    namer = next((x for x in _subs(layer).values() if (x.get("label"), x.get("value")) == (label, value)), None)
    if prov.get("author") != OWNER:
        return _propose(project, layer, label, value, a, prov, namer)
    name = _quoted_name(a, prov)
    if target is not None and namer is not None and namer["id"] != target["id"]:
        raise StoreError(f"{label}={value} is already named by {_called(namer)}: a value is named by one subsystem; drop "
                         "that one or rename it, as the owner says. Nothing was declared")
    old = target or namer
    if old is not None and old.get("name") == name and (old.get("label"), old.get("value")) == (label, value):
        return {"ok": True, "changes": [], "said": [f"'{name}' already names {value}: nothing changed"]}
    eid = _free_id(layer, name, old["id"] if old else None)
    op = {"op": "declare_subsystem", "id": eid, "name": name, "label": label, "value": value}
    done = apply(project, [op], prov)
    was = f" (was '{old['name']}')" if old is not None and old.get("name") != name else ""
    return answer(project, done, [f"a box '{name}' over {value} {BOTH}, named by you{was}",
                                  (f"the maps open on the {label} boxes, {value} titled '{name}' (its value on the title's "
                                   "hover); nothing in the model changes")], id=eid, label=label, value=value)


def _propose(project: Project, layer: dict, label: str, value: str, a: dict, prov: dict, namer: dict | None) -> dict:
    if namer is not None:
        raise StoreError(f"{label}={value} is already named by {_called(namer)}: nothing to propose")
    name = " ".join((a.get("name") or "").split()) or value
    if name != value:
        raise StoreError(f"a proposal names {label}={value} by its value ({value!r}): a name of its own is the owner's to "
                         "give (quote their words). Nothing was proposed")
    if a.get("subsystem"):
        raise StoreError("changing a subsystem is the owner's: quote their words. Nothing was proposed")
    eid = _free_id(layer, name)
    done = apply(project, [{"op": "declare_subsystem", "id": eid, "name": name, "label": label, "value": value,
                            "note": f"proposed by the Supervisor: the model's {label} partition"}], prov)
    return answer(project, done, [(f"proposed: '{name}' over {label}={value}; no box until the owner confirms it "
                                   f"(confirm subsystem:{eid}, their words quoted)")], id=eid, label=label, value=value,
                  proposed=True)


def _ask(q: str, candidates: list[dict]) -> dict:
    return {"ok": True, "asked": q, "changes": [], "candidates": candidates,
            "said": [q, "nothing was declared: ask the owner this, in one sentence, and call again with their answer"]}


def _known_machines(project: Project, store: AgentStore, model, layer: dict) -> set[str]:
    heard = {str(e.get("source") or "").split("/")[0] for e in recent_events(store)}
    return {m.id for m in (model.machines if model is not None else [])} | set(layer.get("machines") or {}) | (heard - {""})


def _gather(project: Project, store: AgentStore, layer: dict, model, a: dict, prov: dict, target: dict | None) -> dict:
    from orchestra.sd.intended.keys import Keys
    if prov.get("author") != OWNER:
        raise StoreError("a gathering is the owner's: quote their words; you may only propose a name for a label's value. "
                         "Nothing was declared")
    name = _quoted_name(a, prov)
    keys = Keys(model)
    lent = intended_api.declared_names(layer, keys)
    members: dict[str, list[str]] = {"stocks": [], "flows": [], "machines": []}
    said: list[str] = []
    for m in a.get("members") or []:
        shown = str((m or {}).get("shown") or "").strip()
        part = (m or {}).get("part") or None
        if part not in (None, "stock", "flow"):
            raise StoreError("a member's part is stock or flow (leave it out when the owner's words do not say)")
        hits = find(model, keys, lent, shown, [str(t) for t in (m or {}).get("tags") or []], part)
        if not hits:
            raise StoreError(f"no {part or 'stock or flow'} is shown as {shown!r} in the model now: name each member as the "
                             "page shows it (with its tags). Nothing was declared")
        if len(hits) > 1:
            q = (f"For '{shown}', which do you mean: "
                 + ", or ".join(f"the {p['part']} {shown_as(p['title'], p['tags'])}" for p in hits) + "?")
            return _ask(q, [{k: p[k] for k in ("part", "key", "title", "tags")} for p in hits])
        p = hits[0]
        group = "flows" if p["part"] == "flow" else "stocks"
        if p["key"] not in members[group]:
            members[group].append(p["key"])
            said.append(f"the {p['part']} {shown_as(p['title'], p['tags'])}")
    known = _known_machines(project, store, model, layer)
    for mid in a.get("machines") or []:
        mid = str(mid).strip()
        if mid not in known:
            raise StoreError(f"no machine {mid!r} is known (in the model, declared, or heard on the bus): name it by its id "
                             "as the Machines page shows it. Nothing was declared")
        if mid not in members["machines"]:
            members["machines"].append(mid)
            said.append(f"the machine {mid}")
    if not any(members.values()):
        if target is not None and target.get("members"):
            members = target["members"]                  # a rename keeps what it gathers
        else:
            raise StoreError("a gathering names at least one member: the stocks and flows as the page shows them (members), "
                             "the machines by id (machines). Nothing was declared")
    for x in _subs(layer).values():
        if target is not None and x["id"] == target["id"]:
            continue
        for group, word in (("stocks", "stock"), ("flows", "flow"), ("machines", "machine")):
            if both := sorted(set(members[group]) & set((x.get("members") or {}).get(group) or [])):
                raise StoreError(f"the {word} {', '.join(both)} is already in {_called(x)}: a part is in one subsystem at a "
                                 "time. Take it out of that one first, as the owner says. Nothing was declared")
    eid = _free_id(layer, name, target["id"] if target else None)
    done = apply(project, [{"op": "declare_subsystem", "id": eid, "name": name, "members": members}], prov)
    held = ", ".join(said[:-1]) + (" and " if len(said) > 1 else "") + said[-1] if said else "what it gathered"
    was = f" (was '{target['name']}')" if target is not None and target.get("name") != name else ""
    return answer(project, done, [f"a box '{name}' {BOTH}, named by you{was}, holding {held}",
                                  ("the maps open on your subsystems; a flow it does not name is placed by its ends, a machine "
                                   "by its hypotheses; nothing in the model changes")], id=eid, members=members)


def _drop(project: Project, x: dict, prov: dict) -> dict:
    if prov.get("author") != OWNER:
        raise StoreError(f"dropping {_called(x)} is the owner's: quote their words. Nothing was changed")
    done = apply(project, [{"op": "retract", "part": "subsystem", "id": x["id"]}], prov)
    return answer(project, done, [f"the box '{x['name']}' is gone from both maps: retracted, kept only in the history"],
                  id=x["id"])


def declare_subsystem(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    layer = intended_api.current(project)
    target = None
    if said := (a.get("subsystem") or "").strip():
        found = existing(layer, said)
        if not found:
            names = [_called(x) for x in _subs(layer).values()]
            raise StoreError(f"no subsystem is called {said!r}" + (f"; declared: {', '.join(names)}" if names else
                                                                   "; none is declared") + ". Nothing was changed")
        if len(found) > 1:
            return _ask("Which do you mean: " + ", or ".join(_called(x) for x in found) + "?",
                        [{"id": x["id"], "name": x["name"]} for x in found])
        target = found[0]
    if a.get("drop"):
        if target is None:
            raise StoreError("drop: name the subsystem to drop (subsystem), by its name or id")
        return _drop(project, target, prov)
    by_value = bool((a.get("value") or "").strip())
    if by_value and (a.get("members") or a.get("machines")):
        raise StoreError("a subsystem names a label's value (value) or gathers parts (members, machines), one of the two")
    model = intended_api.current_model(project)
    if model is None:
        raise StoreError("there is no model yet (no discovery saved): nothing to box. Nothing was declared")
    if target is not None and not by_value and not (a.get("members") or a.get("machines")) and target.get("label"):
        a = {**a, "value": target["value"], "label": target["label"]}           # a rename of a value's name
        by_value = True
    if by_value:
        return _by_label(project, layer, model, a, prov, target)
    return _gather(project, store, layer, model, a, prov, target)


COMMANDS = {"declare_subsystem": declare_subsystem}
