"""The Dynamics side of the design tools (machine-design.md §2.2): declared stocks and effects,
targets, expected marks and ignores on discovered parts, the manual link of a declared stock to
the discovered one it turned out to be, and the ways to measure a declared element. Everything is
an op on the declared layer (``design.apply``); nothing here binds a series or makes discovery
find anything (Orchestra's rules 6 and 7)."""

from __future__ import annotations

import re

from orchestra.config import ConfigError, parse_duration

from . import intended_api
from .config import Project
from .design import answer, apply
from .store import AgentStore, StoreError

OPTION_KINDS = ("existing_series", "script", "scrape", "source", "counters", "simulated")
# a company system: what an option reading one names in `system` (or any `source` option)
COMPANY = ("jira", "github", "prometheus", "jenkins", "confluence", "slack", "pagerduty", "datadog", "grafana")
NEEDS_APPROVAL = "needs approval"
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{1,63}$")


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").casefold()) if len(w) >= 3}


def _declare(project: Project, store: AgentStore, a: dict, prov: dict, part: str) -> dict:
    name = (a.get("name") or "").strip()
    if not name:
        raise StoreError(f"give the {part}'s name, in the owner's words (Features)")
    near = (a.get("near") or "").strip() or None
    owner_placed = bool(near and prov.get("quote") and _words(near) & _words(prov["quote"]))
    guessed = ["near"] if near and not owner_placed else []
    op = {"op": f"declare_{part}", "name": name, "unit": a.get("unit") or None, "near": near, "note": prov.get("quote"),
          "guessed": guessed}
    done = apply(project, store, [{k: v for k, v in op.items() if v not in (None, [])}], prov)
    eid = done["changes"][0]["id"]
    origin = "added by you (from your message)" if prov["author"] == "owner" else "proposed by the Supervisor"
    placed = ("where you said it belongs" if owner_placed else "placed by the Supervisor") + f" beside {near}" if near else "not placed"
    return answer(project, done, [f"{part} {eid} ({name}) declared: {origin}; {placed}",
                                  ("the Dynamics page draws it in the Expected group as a ghost, \"no series yet\", until a "
                                   "series is found for it: propose_measurements next")], id=eid, part=part, placed_by_owner=owner_placed)


def declare_stock(project, store, a, prov):
    return _declare(project, store, a, prov, "stock")


def declare_effect(project, store, a, prov):
    return _declare(project, store, a, prov, "effect")


def resolve(project: Project, element: str) -> tuple[str, str, bool]:
    """``element`` as (part, id or key, declared?): ``stock:features``, or a declared id, or a
    discovered stock's key."""
    element = (element or "").strip()
    if not element:
        raise StoreError("name the element: a declared stock or effect's id, or part:id")
    part, _, rest = element.partition(":")
    layer = intended_api.current(project)
    if rest and part in ("stock", "effect", "flow"):
        return part, rest, rest in (layer.get(f"{part}s") or {})
    for p in ("stock", "effect"):
        if element in (layer.get(f"{p}s") or {}):
            return p, element, True
    return "stock", element, False


def set_target(project, store, a, prov):
    part, eid, declared = resolve(project, a.get("element"))
    cmp = a.get("op")
    if cmp not in ("below", "above", "within"):
        raise StoreError("op is below, above or within (within needs hi)")
    if not isinstance(a.get("value"), int | float) or (cmp == "within" and not isinstance(a.get("hi"), int | float)):
        raise StoreError("value is a number (and hi too, for within)")
    try:
        over_s = parse_duration(a.get("over"), 0.0)
    except ConfigError as e:
        raise StoreError(f"over: {e}") from None
    if over_s <= 0:
        raise StoreError("over is the window the target's mean is judged over: 1h, 1d, 7d")
    op = {"op": "set_target", "part": part, ("id" if declared else "key"): eid, "target": cmp, "value": a["value"],
          "over_s": over_s, **({"hi": a["hi"]} if cmp == "within" else {})}
    done = apply(project, store, [op], prov)
    hi = f"–{a['hi']}" if cmp == "within" else ""
    return answer(project, done, [(f"target on {part} {eid}: {cmp} {a['value']}{hi} (the mean over {a['over']}); the page marks "
                                   "it met, missed or unknown")])


def _option(i: int, o: dict) -> dict:
    if not isinstance(o, dict):
        raise StoreError("each option is an object: {label, kind, how, cost, caveat?, clearance?, system?, bearer_env?}")
    kind = o.get("kind")
    if kind not in OPTION_KINDS:
        raise StoreError(f"option {i + 1}: kind is one of {', '.join(OPTION_KINDS)}")
    for k in ("label", "how", "cost"):
        if not (o.get(k) or "").strip():
            raise StoreError(f"option {i + 1}: give its {k} ({'how long a round takes, what it reads, a store’s request rate' if k == 'cost' else k})")
    env = (o.get("bearer_env") or "").strip()
    if env and not ENV_NAME.match(env):
        raise StoreError(f"option {i + 1}: bearer_env is the name of an environment variable (WORK_TOKEN), never a value")
    system = (o.get("system") or "").strip().lower()
    company = kind == "source" or bool(system) and any(c in system for c in COMPANY)
    # until data classes exist (work charter §8), every company system needs the owner's approval
    clearance = NEEDS_APPROVAL if company else ((o.get("clearance") or "").strip() or "cleared: the project's own store or repository")
    how = o["how"].strip()                     # Orchestra keeps an option's fields only (its OPTION): the rest goes in how
    how += f" (the series {o['series']})" if o.get("series") else ""
    how += f" (reads {system})" if system and system not in how.lower() else ""
    how += f"; its token is read from the environment variable {env}, supplied out of band" if env else ""
    return {"key": "abcdefghij"[i], "title": o["label"].strip(), "kind": kind, "how": how, "cost": o["cost"].strip(),
            "caveat": (o.get("caveat") or "").strip() or None, "clearance": clearance, "simulated": kind == "simulated",
            "instrument": None, "commit": None, "first_points": [], "provenance": None}


def options_of(project: Project, part: str, eid: str) -> dict:
    el = (intended_api.current(project).get(f"{part}s") or {}).get(eid) or {}
    return el.get("measure") or {"options": [], "chosen": None}


def propose_measurements(project, store, a, prov):
    part, eid, declared = resolve(project, a.get("element"))
    if not declared:
        raise StoreError(f"{eid} is not a declared stock or effect: declare it first (declare_stock)")
    given = a.get("options")
    if not isinstance(given, list) or not 1 <= len(given) <= 6:
        raise StoreError("options is a list of one to six ways to measure it, the best first, the simulated one last")
    options = [_option(i, o) for i, o in enumerate(given)]
    sims = [i for i, o in enumerate(options) if o["simulated"]]
    if sims and sims != [len(options) - 1]:
        raise StoreError("a simulated placeholder is always the last option")
    old = options_of(project, part, eid)
    kept = {o["key"]: o for o in old["options"]}
    for o in options:                                     # what an option already recorded stays with it
        if (was := kept.get(o["key"])) and was.get("title") == o["title"]:
            o.update({k: was[k] for k in ("instrument", "commit", "first_points", "provenance") if was.get(k)})
    done = apply(project, store, [{"op": "record_measurement", "part": part, "id": eid, "options": options,
                                   "chosen": old.get("chosen")}], prov)
    lines = [f"{len(options)} way(s) to measure {eid}, in order: " + "; ".join(f"{o['key']} {o['title']} ({o['kind']})" for o in options),
             "the Dynamics page shows them in the element's drawer, \"How to measure it\""]
    if waiting := [o["key"] for o in options if o["clearance"] == NEEDS_APPROVAL]:
        lines.append(f"{', '.join(waiting)} read a company system: recorded \"needs approval\", and nothing reads it until the owner "
                     "clears it")
    return answer(project, done, lines, options=[{k: o[k] for k in ("key", "title", "kind", "clearance")} for o in options])


def _mark(project, store, a, prov, op: str) -> dict:
    part = a.get("part") or "stock"
    key = (a.get("key") or "").strip()
    if part not in ("stock", "flow") or not key:
        raise StoreError("name the discovered part: part (stock or flow) and its key, as read_model or read_design gives it")
    done = apply(project, store, [{"op": op, "part": part, "key": key}], prov)
    said = ("marked expected: always drawn, with the pin; a ghost when a version leaves it out" if op == "expect" else
            "ignored: it moves to the collapsed \"ignored\" group; nothing is hidden or removed")
    return answer(project, done, [f"{part} {key} {said}"])


def mark_expected(project, store, a, prov):
    return _mark(project, store, a, prov, "expect")


def ignore(project, store, a, prov):
    return _mark(project, store, a, prov, "ignore")


def link(project, store, a, prov):
    _, eid, declared = resolve(project, a.get("declared"))
    found = (a.get("discovered") or "").strip()
    if not declared or not found:
        raise StoreError("declared is a declared stock or effect's id; discovered, the key of the discovered stock or series")
    done = apply(project, store, [{"op": "link", "id": eid, "found_as": found}], prov)
    return answer(project, done, [(f"{eid} linked to {found}: a manual binding, origin declared, drawn as an assumption until "
                                   f"fitted; {found} keeps its name")])


def read_design(project: Project, store: AgentStore, a: dict) -> dict:
    from .design_instruments import drafts
    from .design_passes import last_proposal
    out = intended_api.layer(project).design()
    return {"ok": True, **out, "instruments": drafts(store), "refinement": last_proposal(store),
            "kept_in": intended_api.where(project)}


COMMANDS = {"declare_stock": declare_stock, "declare_effect": declare_effect, "set_target": set_target,
            "propose_measurements": propose_measurements, "mark_expected": mark_expected, "ignore": ignore, "link": link,
            "read_design": read_design}
READS = frozenset({"read_design"})
