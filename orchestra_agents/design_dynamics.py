"""The Dynamics side of the design tools (machine-design.md §2.2): declared stocks and effects,
targets, expected marks and ignores on discovered parts, the manual link of a declared stock to
the discovered one it turned out to be, and the ways to measure a declared element. Everything is
an op on the declared layer (``design.apply``); nothing here binds a series or makes discovery
find anything (Orchestra's rules 6 and 7).

**Clearance is decided from facts, never from the model's words** (``design_clearance.py``). What
an option names (its system, series, url, command and the environment variable of its token) is
kept on the option itself (Orchestra's ``ops.OPTION``, ``intended_api.OPTION_FIELDS``), and
``add_instrument`` reads it back from there: it runs only what was proposed."""

from __future__ import annotations

import re
import time

from orchestra.config import ConfigError, parse_duration

from . import intended_api
from .config import Project
from .design import MACHINE, answer, apply, page_line, plural
from .design_clearance import NEEDS_APPROVAL, UNTIL, clearance
from .secret_shapes import secret_in
from .store import AgentStore, StoreError

OPTION_KINDS = ("existing_series", "script", "scrape", "source", "counters", "simulated")
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
    description = (a.get("description") or "").strip() or None
    op = {"op": f"declare_{part}", "name": name, "unit": a.get("unit") or None, "near": near, "note": prov.get("quote"),
          "guessed": guessed, "description": description}
    done = apply(project, store, [{k: v for k, v in op.items() if v not in (None, [])}], prov)
    eid = done["changes"][0]["id"]
    origin = "added by you (from your message)" if prov["author"] == "owner" else "proposed by the Supervisor"
    placed = ("where you said it belongs" if owner_placed else "placed by the Supervisor") + f" beside {near}" if near else "not placed"
    return answer(project, done, [f"{part} {eid} ({name}) declared: {origin}; {placed}",
                                  page_line(project, "the Dynamics page draws it in the Expected group as a ghost, \"no series "
                                                     "yet\", until a series is found for it"),
                                  "next: find how to measure it (propose_measurements)"],
                  id=eid, part=part, placed_by_owner=owner_placed)


def declare_stock(project, store, a, prov):
    return _declare(project, store, a, prov, "stock")


def declare_effect(project, store, a, prov):
    return _declare(project, store, a, prov, "effect")


def resolve(project: Project, element: str) -> tuple[str, str, bool]:
    """``element`` as (part, id or key, declared?): ``stock:features``, or a declared id, or a
    discovered part's key (a flow's when it has `` -> ``, a stock's otherwise)."""
    element = (element or "").strip()
    if not element:
        raise StoreError("name the element: a declared stock or effect's id, or part:id")
    part, _, rest = element.partition(":")
    layer = intended_api.current(project)
    if rest and part in ("stock", "effect", "flow", "machine", "attachment"):
        return part, rest, rest in (layer.get(f"{part}s") or {})
    for p in ("stock", "effect", "machine", "flow", "attachment"):
        if element in (layer.get(f"{p}s") or {}):
            return p, element, True
    return ("flow" if " -> " in element else "stock"), element, False


def set_target(project, store, a, prov):
    part, eid, declared = resolve(project, a.get("element"))
    cmp = a.get("compare")
    if cmp not in ("below", "above", "within"):
        raise StoreError("compare is below, above or within (within needs hi)")
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
    return answer(project, done, [f"target on {part} {eid}: {cmp} {a['value']}{hi} (the mean over {a['over']})",
                                  page_line(project, "the Dynamics page marks it met, missed or unknown")])


def no_secret(*texts: str) -> None:
    for t in texts:
        if t and (why := secret_in(t)):
            raise StoreError(f"it looks like it holds a secret ({why}); a credential is named only by its environment variable "
                             "(bearer_env), set in the overlay and supplied out of band. Nothing was recorded or run")


def _option(project: Project, i: int, o: dict) -> tuple[dict, dict]:
    """The option as Orchestra keeps it, and what the Supervisor keeps of it (url, command, series)."""
    if not isinstance(o, dict):
        raise StoreError("each option is an object: {label, kind, how, cost, caveat?, series?, url?, command?, system?, bearer_env?}")
    kind = o.get("kind")
    if kind not in OPTION_KINDS:
        raise StoreError(f"option {i + 1}: kind is one of {', '.join(OPTION_KINDS)}")
    for k in ("label", "how", "cost"):
        if not (o.get(k) or "").strip():
            raise StoreError(f"option {i + 1}: give its {k} ({'how long a round takes, what it reads, a store’s request rate' if k == 'cost' else k})")
    env = (o.get("bearer_env") or "").strip()
    if env and not ENV_NAME.match(env):
        raise StoreError(f"option {i + 1}: bearer_env is the name of an environment variable (WORK_TOKEN), never a value")
    url, command, series = ((o.get(k) or "").strip() for k in ("url", "command", "series"))
    system = (o.get("system") or "").strip().lower()
    no_secret(url, command, o["how"], o["label"])
    level, why = clearance(project, kind, o["label"], o["how"], system, url, command)
    names = {"system": system or None, "series": series or None, "url": url or None, "command": command or None,
             "bearer_env": env or None}
    keeps = intended_api.OPTION_FIELDS
    key = "abcdefghij"[i]
    kept = {"key": key, "title": o["label"].strip(), "kind": kind, "how": o["how"].strip(), "cost": o["cost"].strip(),
            "caveat": (o.get("caveat") or "").strip() or None, "clearance": level, "simulated": kind == "simulated",
            "instrument": None, "commit": None, "first_points": [], "provenance": None,
            **{k: v for k, v in names.items() if k in keeps and v is not None}}
    return kept, why


def options_of(project: Project, part: str, eid: str) -> dict:
    el = (intended_api.current(project).get(f"{part}s") or {}).get(eid) or {}
    return el.get("measure") or {"options": [], "chosen": None}


def _session_of(store: AgentStore) -> str | None:
    """The Supervisor's Claude Code (or other harness) session, as the host records it: what one
    conversation is, across its turns."""
    s = store.find_session(MACHINE) or {}
    return s.get("session_file") or None


def note_metrics_listed(store: AgentStore) -> None:
    """The Supervisor read the metrics catalogue (list_metrics) in its session."""
    store.set_agent_state(MACHINE, metrics_listed={"at": time.time(), "session": _session_of(store)})


def metrics_listed(store: AgentStore) -> bool:
    """Whether the Supervisor called list_metrics in its current session (any session when the host
    records none)."""
    seen = store.agent_state(MACHINE).get("metrics_listed") or {}
    return bool(seen) and (seen.get("session") == _session_of(store) or _session_of(store) is None)


NOT_LOOKED = ("propose_measurements waits for list_metrics: call list_metrics first (this session has not), and read what the "
              "store already records. A series already there (option kind existing_series, its series named) always beats a "
              "script you would write; name the series you found, or say you found none")


def propose_measurements(project, store, a, prov):
    if not metrics_listed(store):
        raise StoreError(NOT_LOOKED)
    part, eid, declared = resolve(project, a.get("element"))
    if not declared or part not in ("stock", "effect"):
        raise StoreError(f"{eid} is not a declared stock or effect: declare it first (declare_stock)")
    given = a.get("options")
    if not isinstance(given, list) or not 1 <= len(given) <= 6:
        raise StoreError("options is a list of one to six ways to measure it, the best first, the simulated one last")
    pairs = [_option(project, i, o) for i, o in enumerate(given)]
    options = [k for k, _ in pairs]
    sims = [i for i, o in enumerate(options) if o["simulated"]]
    if sims and sims != [len(options) - 1]:
        raise StoreError("a simulated placeholder is always the last option")
    old = options_of(project, part, eid)
    kept = {o["key"]: o for o in old["options"]}
    for o in options:                                     # what an option already recorded stays with it
        if (was := kept.get(o["key"])) and was.get("title") == o["title"]:
            o.update({k: was[k] for k in ("instrument", "commit", "first_points", "provenance") if was.get(k)})
    chosen = old.get("chosen") if old.get("chosen") in {o["key"] for o in options} else None
    done = apply(project, store, [{"op": "record_measurement", "part": part, "id": eid, "options": options, "chosen": chosen}], prov)
    lines = [f"{plural(len(options), 'way')} to measure {eid}, in order: " + "; ".join(f"{o['key']} {o['title']} ({o['kind']})" for o in options),
             page_line(project, "the Dynamics page shows them in the element's drawer, \"How to measure it\"")]
    if waiting := [f"{o['key']} ({why})" for o, (_, why) in zip(options, pairs, strict=True) if o["clearance"] == NEEDS_APPROVAL]:
        lines.append(f"{', '.join(waiting)}: recorded \"needs approval\"; nothing reads or previews it {UNTIL}")
    lines += link_hints(project, eid, [o.get("series") for o in options if o["kind"] == "existing_series"])
    return answer(project, done, lines, options=[{k: o[k] for k in ("key", "title", "kind", "clearance")} for o in options])


def discovered_stock_of(project: Project, series: str) -> str | None:
    """The key of the current model's stock that is ``series`` (its key, or the key's name before
    its labels), or None."""
    keys = intended_api.Keys(intended_api.current_model(project))
    return next((k for k in keys.stocks if k == series or k.split("{", 1)[0] == series), None)


def link_hints(project: Project, eid: str, series: list[str | None]) -> list[str]:
    """For each existing series an option names that the model already has as a stock: link it."""
    out = []
    for name in dict.fromkeys(s for s in series if s):
        if key := discovered_stock_of(project, name):
            out.append(f"{name} is already a discovered stock ({key}): if it is what {eid} means, link it now "
                       f"(link declared={eid} discovered={key}), so its expected effects can be judged")
    return out


def _mark(project, store, a, prov, op: str) -> dict:
    part = a.get("part") or "stock"
    key = (a.get("key") or "").strip()
    if part not in ("stock", "flow") or not key:
        raise StoreError("name the discovered part: part (stock or flow) and its key, as read_model or read_design gives it")
    done = apply(project, store, [{"op": op, "part": part, "key": key}], prov)
    said = ("marked expected" if op == "expect" else "ignored")
    drawn = ("always drawn, with the pin; a ghost when a version leaves it out" if op == "expect" else
             "it moves to the collapsed \"ignored\" group; nothing is hidden or removed")
    return answer(project, done, [f"{part} {key} {said}", page_line(project, f"the Dynamics page: {drawn}")])


def mark_expected(project, store, a, prov):
    return _mark(project, store, a, prov, "expect")


def ignore(project, store, a, prov):
    return _mark(project, store, a, prov, "ignore")


def link(project, store, a, prov):
    from .design_confirm import publish_waiting
    from .design_machines import rewrite_readers
    part, eid, declared = resolve(project, a.get("declared"))
    found = (a.get("discovered") or "").strip()
    if not declared or part not in ("stock", "effect") or not found:
        raise StoreError("declared is a declared stock or effect's id; discovered, the key of the discovered stock or series")
    caveat = (a.get("caveat") or "").strip()
    no_secret(caveat)
    if caveat:
        prov = {**prov, "reason": f"{prov['reason'].rstrip('.')}. Caveat: {caveat}"}
    ops = [{"op": "link", "part": part, "id": eid, "found_as": found}]
    measure = options_of(project, part, eid)
    if option := next((o for o in measure["options"] if o["kind"] == "existing_series"
                       and o.get("series") in (found, found.split("{", 1)[0])), None):
        option.update({"caveat": caveat or option.get("caveat")})
        ops.append({"op": "record_measurement", "part": part, "id": eid, "options": measure["options"], "chosen": option["key"]})
    done = apply(project, store, ops, prov)
    lines = [(f"{eid} linked to {found}: a manual binding, origin declared, drawn as an assumption until fitted; {found} "
              "keeps its name")]
    if option:
        lines.append(f"option {option['key']} ({found}) marked chosen")
    if caveat:
        lines.append(f"caveat recorded with the link: {caveat}. Tell the owner, and offer a new instrument only if they "
                     "reject this series")
    lines += publish_waiting(project, store)
    if again := rewrite_readers(project, store, eid):
        lines.append(f"the records of {', '.join(again)}, which read {eid}, are written again: they read {found} now")
    return answer(project, done, lines)


def _event_edges(project: Project, store: AgentStore, layer: dict) -> dict:
    """Each event type's publishers and listeners on both layers: the declared machines' emits and
    receives, what was heard on the bus (its sources) and what announced machines listen for."""
    out: dict[str, dict] = {}

    def row(t: str) -> dict:
        return out.setdefault(t, {"declared_emitters": [], "declared_receivers": [], "heard_from": [], "listened_by": []})
    for mid, m in (layer.get("machines") or {}).items():
        for t in m.get("emits") or []:
            row(t)["declared_emitters"].append(mid)
        for t in m.get("receives") or []:
            row(t)["declared_receivers"].append(mid)
    for t, d in (layer.get("events") or {}).items():
        row(t)["purpose"] = d.get("description")
    end = store.events.end()
    evs, _ = store.events.read(max(0, end - 5000), limit=5000)
    for e in evs:
        src = str(e.get("source") or "")
        if e.get("type") in out and src not in out[e["type"]]["heard_from"]:
            out[e["type"]]["heard_from"].append(src)
        if e.get("type") == "orchestra.machine.started":
            for t in e.get("listens_for") or []:
                if t in out and src not in out[t]["listened_by"]:
                    out[t]["listened_by"].append(src)
    return out


def read_design(project: Project, store: AgentStore, a: dict) -> dict:
    from .design_instruments import drafts
    from .design_passes import last_proposal
    lay = intended_api.layer(project)
    out = lay.design()
    layer = lay.read()["layer"]
    machines = {mid: {k: m.get(k) for k in ("state", "title", "description", "kind", "managed", "engagement", "receives",
                                            "emits", "reads", "spec", "guessed", "pending", "expected_effect", "latest")}
                for mid, m in (layer.get("machines") or {}).items()}
    return {"ok": True, **out, "machines": machines, "events": _event_edges(project, store, layer),
            "instruments": drafts(store), "refinement": last_proposal(store), "kept_in": intended_api.where(project)}


COMMANDS = {"declare_stock": declare_stock, "declare_effect": declare_effect, "set_target": set_target,
            "propose_measurements": propose_measurements, "mark_expected": mark_expected, "ignore": ignore, "link": link,
            "read_design": read_design}
READS = frozenset({"read_design"})
