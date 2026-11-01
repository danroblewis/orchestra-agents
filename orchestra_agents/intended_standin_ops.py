"""The stand-in's ops (``intended_standin.py``), each applied to a layer in place, ported from
Orchestra's main at 64ea32a0 (``orchestra/sd/intended/ops.py``, ``machine_ops.py``, ``checks.py``;
consent and revision in ``intended_standin_consent.py``) so the design tools meet the same rules
before and after Orchestra's own is installed. Deleted with the stand-in.

What the owner asks for (``author: owner`` with a quote) is declared at once, a re-declaration of
a proposed stock or effect included; anything else is proposed. A proposal or a pending change is
settled only by the owner or in answer to their noted request (``provenance.request``); a confirm
answering the owner's request makes the element the owner's (``confirmed: {by: owner, via:
request}``). What is the owner's (an element, a mark, a target, a described event type:
``owners``) is re-targeted, unmarked, overwritten or retracted only with the owner's consent;
another author's re-mark of the owner's mark is a no-op, and a key is expected or ignored, never
both. A declared stock or effect that a flow, a keyed target or an expected effect names (a
pending change's included) is not removed until they change; an event type a machine names is
not retracted until the machine changes. A revision answering the owner's ``switch_kind`` request
(one that sets ``kind`` and nothing else) applies at once, also on a confirmed machine; a machine's
new expected effect, once it is no longer proposed, waits as a pending change, and new edits drop
the prediction linked to the old ones."""

from __future__ import annotations

import copy
import re

from .expect_grammar import expect_edit
from .intended_standin_consent import (
    consents,
    guard_owners,
    may_settle,
    owner_asks,
    owners,
    revise,
    settle_pending,
    verified,
)
from .store import StoreError

ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,80}$")
GROUP = {"stock": "stocks", "effect": "effects", "flow": "flows", "machine": "machines", "attachment": "attachments",
         "event": "events"}
KINDS = ("universal", "programmatic")
MANAGED = ("orchestra", "external")
MODES = ("ad hoc", "continuous", "scheduled")
MISSED = ("skip", "run once")
LISTS = ("tags", "receives", "emits", "reads")
ENVELOPE = ("id", "title", "description", "tags", "kind", "managed", "engagement", "receives", "emits", "reads", "spec")
DEFAULTS = {"title": None, "description": None, "tags": [], "kind": "universal", "managed": "orchestra",
            "engagement": {"mode": "ad hoc"}, "receives": [], "emits": [], "reads": [], "spec": {}}
OPTION = ("key", "title", "how", "kind", "instrument", "simulated", "cost", "caveat", "clearance", "provenance", "commit",
          "first_points", "bearer_env", "system", "series", "url", "command")
OPTION_TEXT = ("system", "series", "url", "command")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
URL_SECRET = re.compile(r"://[^/@\s]*:[^/@\s]*@")
DERIVED = frozenset({"present", "heard", "recording", "points", "points_series", "measured", "met", "instances",
                     "candidates", "alias_candidates", "unexpected", "verdict", "verdicts"})


def empty() -> dict:
    return {"stocks": {}, "effects": {}, "flows": {}, "machines": {}, "events": {}, "attachments": {}, "targets": {},
            "marks": {"expected": {}, "ignored": {}}}


def make_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")[:80] or "element"


def only(op: dict, allowed: tuple) -> None:
    if bad := sorted(set(op) & DERIVED):
        raise StoreError(f"{', '.join(bad)} derived on read, never declared")
    if extra := sorted(set(op) - set(allowed) - {"op", "guessed"}):
        raise StoreError(f"{op.get('op')}: unknown field{'s' if len(extra) > 1 else ''} {', '.join(extra)}")


def strings(v, name: str) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise StoreError(f"{name} must be a list of non-empty strings, not {v!r}")
    return list(v)


def engagement(e) -> dict:
    if not isinstance(e, dict) or e.get("mode") not in MODES:
        raise StoreError(f"engagement is {{mode, at?, tz?, missed?}} with mode one of {', '.join(MODES)}")
    if extra := set(e) - {"mode", "at", "tz", "missed"}:
        raise StoreError(f"engagement has no {sorted(extra)}")
    if e["mode"] != "scheduled" and any(k in e for k in ("at", "tz", "missed")):
        raise StoreError("at, tz and missed belong to a scheduled engagement only")
    if "missed" in e and e["missed"] not in MISSED:
        raise StoreError(f"missed is one of {', '.join(MISSED)}")
    return dict(e)


def envelope_field(k: str, v):
    if k == "id":
        if not isinstance(v, str) or not ID.match(v):
            raise StoreError(f"a machine's id is its bus source: ^[a-z][a-z0-9_]{{1,40}}$ (not {v!r})")
    elif k in LISTS:
        strings(v, k)
    elif k == "kind" and v not in KINDS:
        raise StoreError(f"kind is {' or '.join(KINDS)}")
    elif k == "managed" and v not in MANAGED:
        raise StoreError(f"managed is {' or '.join(MANAGED)}")
    elif k == "engagement":
        return engagement(v)
    elif k == "spec" and not isinstance(v, dict):
        raise StoreError("spec is an object")
    elif k not in ENVELOPE:
        raise StoreError(f"unknown envelope field {k} (the rest of a machine is its spec)")
    return v


def element(L: dict, part: str, eid) -> dict:
    if part not in GROUP:
        raise StoreError(f"part must be one of {', '.join(GROUP)}, not {part!r}")
    el = L[GROUP[part]].get(eid)
    if el is None:
        raise StoreError(f"there is no {part} {eid!r} in the declared layer")
    return el


def _new(ctx: dict, **fields) -> dict:
    prov = ctx["provenance"]
    return {**fields, "state": "declared" if prov.get("author") == "owner" else "proposed", "provenance": prov,
            "change": ctx["change"], "guessed": strings(ctx["op"].get("guessed"), "guessed"), "pending": []}


def declare_level(L: dict, op: dict, ctx: dict, part: str) -> tuple[str, str]:
    only(op, ("id", "name", "description", "unit", "near", "note"))
    name = op.get("name")
    if not isinstance(name, str) or not name.strip():
        raise StoreError("a stock or effect needs a name")
    eid = op.get("id") or make_slug(name)
    if not SLUG.match(eid):
        raise StoreError(f"id must be a slug, not {eid!r}")
    fields = {"name": name, **{k: op.get(k) for k in ("description", "unit", "near", "note")}}
    other = "effects" if part == "stock" else "stocks"
    if eid in L[other]:
        raise StoreError(f"{eid} is already declared as {'an effect' if part == 'stock' else 'a stock'}")
    group = L[GROUP[part]]
    if eid in group:
        el = group[eid]
        revise(el, {k: v for k, v in fields.items() if op.get(k) is not None}, {}, {}, strings(op.get("guessed"), "guessed"), ctx)
        if el.get("state") == "proposed" and owner_asks(ctx["provenance"]):
            el.update(state="declared", confirmed={"by": "owner", "change": ctx["change"]})
        return part, eid
    group[eid] = _new(ctx, id=eid, part=part, **fields, found_as=None, measure=None, target=None)
    return part, eid


def set_target(L: dict, op: dict, ctx: dict) -> tuple[str, str]:
    only(op, ("part", "id", "key", "target", "value", "hi", "over_s"))
    part = op.get("part")
    if part not in ("stock", "effect", "flow") or op.get("target") not in ("below", "above", "within"):
        raise StoreError("a target is on a stock, effect or flow, with op below, above or within")
    t = {"op": op["target"], "value": op.get("value"), "over_s": op.get("over_s"), **({"hi": op["hi"]} if "hi" in op else {})}
    if op.get("id") is not None:
        el = element(L, part, op["id"])
        guard_owners(el.get("target_by") or el, ctx, part, op["id"])     # who set the target, else whose the element is
        el["target"] = t
        el["target_by"] = {"provenance": ctx["provenance"], "change": ctx["change"]}
        return part, op["id"]
    guard_owners(L["targets"].get(op.get("key")), ctx, part, op.get("key"))
    L["targets"][op["key"]] = {"part": part, "key": op["key"], "target": t, "provenance": ctx["provenance"], "change": ctx["change"]}
    return part, op["key"]


def mark(L: dict, op: dict, ctx: dict, which: str, on: bool) -> tuple[str, str]:
    only(op, ("part", "key"))
    if op.get("part") not in ("stock", "flow") or not op.get("key"):
        raise StoreError("a mark names a discovered stock or flow by key")
    box, key, part = L["marks"][which], op["key"], op["part"]
    if on:
        other = L["marks"]["ignored" if which == "expected" else "expected"]
        if key in other:                          # expected or ignored, never both
            guard_owners(other[key], ctx, part, key)
            del other[key]
        if key in box and owners(box[key]) and not consents(ctx, part, key):
            return part, key                      # the owner's mark stands, with the owner's provenance
        box[key] = {"part": part, "key": key, "provenance": ctx["provenance"], "change": ctx["change"]}
        return part, key
    if key not in box:
        raise StoreError(f"{key} is not {which}")
    guard_owners(box[key], ctx, part, key)
    del box[op["key"]]
    return op["part"], op["key"]


def link(L: dict, op: dict, ctx: dict) -> tuple[str, str]:
    only(op, ("id", "part", "found_as"))
    part = op.get("part") or ("stock" if op.get("id") in L["stocks"] else "effect")
    if part not in ("stock", "effect"):
        raise StoreError(f"link joins a declared stock or effect to what discovery found, not a {part}")
    el = element(L, part, op.get("id"))
    el["found_as"] = op.get("found_as")
    el["linked"] = {"by": ctx["provenance"].get("author"), "change": ctx["change"]} if el["found_as"] else None
    return part, el["id"]


def declare_event(L: dict, op: dict, ctx: dict) -> tuple[str, str]:
    only(op, ("type", "description"))
    if not isinstance(op.get("type"), str) or not op["type"].strip():
        raise StoreError("an event type is named")
    old = L["events"].get(op["type"])
    if old is not None and not old.get("implicit"):          # an undescribed type is described at once
        guard_owners(old, ctx, "event", op["type"])
    L["events"][op["type"]] = {"type": op["type"], "description": op.get("description"), "implicit": False,
                               "provenance": ctx["provenance"], "change": ctx["change"]}
    return "event", op["type"]


def record_measurement(L: dict, op: dict, ctx: dict) -> tuple[str, str]:
    only(op, ("part", "id", "options", "chosen"))
    part = op.get("part") or "stock"
    if part not in ("stock", "effect"):
        raise StoreError(f"a measurement is recorded for a declared stock or effect, not a {part}")
    el = element(L, part, op.get("id"))
    opts = op.get("options")
    if not isinstance(opts, list) or not opts:
        raise StoreError("options is a non-empty list")
    for o in opts:
        if not isinstance(o, dict) or (extra := set(o) - set(OPTION)):
            raise StoreError(f"an option's unknown fields: {sorted(extra) if isinstance(o, dict) else o!r}")
        if not isinstance(o.get("key"), str) or not o["key"]:
            raise StoreError("each option has its key")
        _option_names(o)
    keys = [o["key"] for o in opts]
    if len(set(keys)) != len(keys):
        raise StoreError("each option's key is used once")
    if op.get("chosen") is not None and op["chosen"] not in keys:
        raise StoreError(f"chosen must be one of the options' keys ({', '.join(keys)}), not {op['chosen']!r}")
    el["measure"] = {"options": [dict(o) for o in opts], "chosen": op.get("chosen"), "change": ctx["change"]}
    return part, el["id"]


def _option_names(o: dict) -> None:
    """What an option names is text; its credential only by the environment variable that holds it."""
    for k in OPTION_TEXT:
        if o.get(k) is not None and not isinstance(o[k], str):
            raise StoreError(f"an option's {k} is text")
    if o.get("url") and URL_SECRET.search(o["url"]):
        raise StoreError("an option's url carries no password: name the credential by its environment variable (bearer_env)")
    env = o.get("bearer_env")
    if env is not None and (not isinstance(env, str) or not ENV_NAME.match(env)):
        raise StoreError(f"an option's bearer_env is the name of an environment variable (WORK_TOKEN), never a value, not {env!r}")


def _implicit(L: dict, m: dict, ctx: dict) -> None:
    for t in [*m.get("receives", []), *m.get("emits", [])]:
        L["events"].setdefault(t, {"type": t, "description": None, "implicit": True, "provenance": ctx["provenance"],
                                   "change": ctx["change"]})


def propose_machine(L: dict, op: dict, ctx: dict) -> tuple[str, str]:
    fields = {k: envelope_field(k, v) for k, v in op.items() if k not in ("op", "guessed")}
    mid = fields.get("id")
    if mid is None:
        raise StoreError("a machine needs its id: the source it is expected to publish under")
    if mid in L["machines"]:
        raise StoreError(f"machine {mid} is already in the declared layer: revise it")
    m = {**copy.deepcopy(DEFAULTS), **fields}
    m["title"] = m["title"] or mid
    m.update(state="proposed", provenance=ctx["provenance"], change=ctx["change"], guessed=strings(op.get("guessed"), "guessed"),
             pending=[], aliases=[], expected_effect=None, latest=None)
    L["machines"][mid] = m
    _implicit(L, m, ctx)
    return "machine", mid


def revise_machine(L: dict, op: dict, ctx: dict) -> tuple[str, str]:
    only(op, ("id", "set", "add", "remove"))
    m = element(L, "machine", op.get("id"))
    set_, add, remove = (op.get(k) or {} for k in ("set", "add", "remove"))
    if "id" in set_:
        raise StoreError("a machine's id is not revised: retract it and propose another")
    set_ = {k: envelope_field(k, v) for k, v in set_.items()}
    for what, d in (("add", add), ("remove", remove)):
        for k, v in d.items():
            if k not in LISTS:
                raise StoreError(f"{what} takes the list fields ({', '.join(LISTS)}), not {k}")
            strings(v, f"{what}.{k}")
    switch = set(set_) == {"kind"} and not add and not remove and verified(ctx, "machine", m["id"], "switch_kind")
    revise(m, set_, add, remove, strings(op.get("guessed"), "guessed"), ctx, waits_once_confirmed=True, consent=switch)
    _implicit(L, m, ctx)
    return "machine", m["id"]


def set_expected_effect(L: dict, op: dict, ctx: dict) -> tuple[str, str]:
    only(op, ("machine", "edits", "horizon_s"))
    m = element(L, "machine", op.get("machine"))
    if not isinstance(op.get("edits"), list):
        raise StoreError("edits is a list, in the grammar of orchestra.prediction.made")
    edits = [expect_edit(e) for e in op["edits"]]
    h = op.get("horizon_s")
    if isinstance(h, bool) or not isinstance(h, int | float) or h <= 0:
        raise StoreError("horizon_s must be a number above 0")
    prev = m.get("expected_effect") or {}
    kept = prev.get("prediction") if prev.get("edits") == edits else None      # a verdict judged these edits, or none
    effect = {"edits": edits, "horizon_s": float(h), "prediction": kept, "change": ctx["change"]}
    if m.get("state") == "proposed":
        m["expected_effect"] = effect
    else:
        revise(m, {"expected_effect": effect}, {}, {}, [], ctx, waits_once_confirmed=True)
    return "machine", m["id"]


def record_prediction(L: dict, op: dict, ctx: dict) -> tuple[str, str]:
    only(op, ("machine", "prediction"))
    m = element(L, "machine", op.get("machine"))
    if not m.get("expected_effect"):
        raise StoreError(f"machine {m['id']} has no expected effect to link a prediction to")
    if not isinstance(op.get("prediction"), str) or not op["prediction"]:
        raise StoreError("prediction is the prediction's id")
    m["expected_effect"]["prediction"] = op["prediction"]
    return "machine", m["id"]


def confirm(L: dict, op: dict, ctx: dict, accept: bool) -> tuple[str, str]:
    only(op, ("part", "id", "change"))
    part, eid = op.get("part"), op.get("id")
    el = element(L, part, eid)
    may_settle(ctx, part, eid, op["op"], op.get("change") or None)
    if op.get("change"):
        settle_pending(el, op["change"], accept, ctx, part)
        if accept and part == "machine":
            _implicit(L, el, ctx)
        return part, eid
    if el.get("state") != "proposed":
        raise StoreError(f"{part} {eid} is {el.get('state')}, not proposed" + ("" if accept else ": retract it"))
    if accept:
        el.update(state="declared", guessed=[], confirmed=_confirmed_by(ctx))
    else:
        _remove(L, part, eid)
    return part, eid


def _confirmed_by(ctx: dict) -> dict:
    """Who confirmed: the owner (quoted, or by the request the call answers), else the author."""
    prov, cid = ctx["provenance"], ctx["change"]
    if owner_asks(prov):
        return {"by": "owner", "change": cid}
    if prov.get("request"):                       # may_settle verified it: the owner's request
        return {"by": "owner", "via": "request", "request": prov["request"], "change": cid}
    return {"by": prov.get("author"), "change": cid}


def _names(edits, eid: str) -> bool:
    return any(eid in (e.get("stock"), e.get("from"), e.get("to")) for e in edits or [])


def _named_by(L: dict, eid: str) -> list[str]:
    """What names a declared stock or effect: flows, a keyed target, expected effects, and the
    pending changes that would set one of them."""
    out = [f"flow {fid}" for fid, f in L["flows"].items() if eid in (f.get("from"), f.get("to"))]
    if eid in L["targets"]:
        out.append(f"the target on {eid}")
    for mid, m in L["machines"].items():
        if _names((m.get("expected_effect") or {}).get("edits"), eid):
            out.append(f"machine {mid}'s expected effect")
    for part, group in (("machine", "machines"), ("attachment", "attachments"), ("flow", "flows")):
        for xid, x in L[group].items():
            for p in x.get("pending") or []:
                s = p.get("set") or {}
                if _names((s.get("expected_effect") or {}).get("edits"), eid) or eid in (s.get("from"), s.get("to")):
                    out.append(f"{part} {xid}'s pending change {p.get('change')}")
    return out


def _remove(L: dict, part: str, eid: str) -> None:
    if part in ("stock", "effect") and (by := _named_by(L, eid)):
        raise StoreError(f"{part} {eid} is named by {', '.join(by)}: retract or change {'it' if len(by) == 1 else 'them'} first")
    if part == "event" and (ms := [mid for mid, m in L["machines"].items()
                                   if eid in [*(m.get("receives") or []), *(m.get("emits") or [])]]):
        raise StoreError(f"event {eid} is named by machine{'s' if len(ms) > 1 else ''} {', '.join(ms)}: change the machine first")
    del L[GROUP[part]][eid]
    if part == "machine":
        L["attachments"] = {k: a for k, a in L["attachments"].items() if a.get("machine") != eid}


def retract(L: dict, op: dict, ctx: dict) -> tuple[str, str]:
    only(op, ("part", "id"))
    part, eid = op.get("part"), op.get("id")
    guard_owners(element(L, part, eid), ctx, part, eid)
    _remove(L, part, eid)
    return part, eid


def apply_op(L: dict, op: dict, ctx: dict) -> tuple[str, str]:
    if not isinstance(op, dict):
        raise StoreError(f"an op is an object, not {op!r}")
    name, ctx = op.get("op"), {**ctx, "op": op}
    table = {"declare_stock": lambda: declare_level(L, op, ctx, "stock"),
             "declare_effect": lambda: declare_level(L, op, ctx, "effect"),
             "set_target": lambda: set_target(L, op, ctx),
             "expect": lambda: mark(L, op, ctx, "expected", True), "unexpect": lambda: mark(L, op, ctx, "expected", False),
             "ignore": lambda: mark(L, op, ctx, "ignored", True), "unignore": lambda: mark(L, op, ctx, "ignored", False),
             "link": lambda: link(L, op, ctx), "declare_event": lambda: declare_event(L, op, ctx),
             "record_measurement": lambda: record_measurement(L, op, ctx),
             "propose_machine": lambda: propose_machine(L, op, ctx), "revise_machine": lambda: revise_machine(L, op, ctx),
             "set_expected_effect": lambda: set_expected_effect(L, op, ctx),
             "record_prediction": lambda: record_prediction(L, op, ctx),
             "confirm": lambda: confirm(L, op, ctx, True), "discard": lambda: confirm(L, op, ctx, False),
             "retract": lambda: retract(L, op, ctx)}
    if name not in table:
        raise StoreError(f"unknown op {name!r}")
    return table[name]()
