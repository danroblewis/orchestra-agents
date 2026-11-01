"""The Machines side of the design tools (machine-design.md §2.3, §2.5, §4): a machine declared
by name with its envelope and its ``spec`` (the fields of §2.3 per kind and mode, which Orchestra
stores opaquely), refined edit by edit with the value each replaced, the event types it names,
and its expected effect, a draft prediction published only at confirm (``design_confirm``).

A Universal Machine is not described in AI terms: its declaration names no model, prompt or
tool (``ai_terms``). Its implementation is the plugin's alone, drafted in the state directory
(``drafts/<name>/``, in the format a confirmed one is run from: ``state_machines.files``), so a
draft is not official and a discard leaves nothing; each refinement redrafts it.

**The kind switch** (§4, Universal | Programmatic): to Programmatic, the Universal description
(``invariants``, ``steps``, ``uses``) is kept as the program's ``specification``, which the program is
written from; back to Universal, it is the description again and the program's fields wait under
``program``."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from orchestra.config import ConfigError, parse_duration

from . import intended_api, state_machines
from .config import Project
from .design import answer, apply, check_request, page_line
from .expect_grammar import expect_edit, is_slope, slopes
from .store import AgentStore, StoreError

ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")         # a machine's id: the source it publishes under
MODES = ("ad hoc", "continuous", "scheduled")
UNIVERSAL = ("invariants", "steps", "uses")
PROTOCOLS = ("http", "graphql", "grpc", "other")
BY_MODE = {"ad hoc": ("command", "trigger", "arguments", "environment", "stdin", "exit"),
           "scheduled": ("command", "trigger", "arguments", "environment", "stdin", "exit"),
           "continuous": ("command", "protocol", "send", "receive")}
EXTERNAL = ("source", "log")
SPECIFICATION, PROGRAM = "specification", "program"     # what a switch of kind keeps of the other kind's fields
AI_WORDS = re.compile(r"\b(AI|LLM|LLMs|GPT|haiku|sonnet|opus|claude|gemini|prompt|prompts|language model|tool call|tool group)\b",
                      re.IGNORECASE)
TURN_USD = 0.03          # an estimate of one Haiku turn of a narrow machine, for the cost line
DEFAULT_MODEL = state_machines.DEFAULT_MODEL
EVENT = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
DAY = 86_400.0


def drafts_dir(project: Project) -> Path:
    return project.state_dir / "drafts"


def ai_terms(m: dict) -> list[str]:
    """What in a Universal declaration is said in AI terms: a model, a prompt, a tool's name."""
    from .harness.tools import BY_NAME
    tools = {n for n in BY_NAME if "_" in n}
    found: list[str] = []

    def walk(v):
        if isinstance(v, str):
            found.extend(m_.group(0) for m_ in AI_WORDS.finditer(v))
            found.extend(t for t in tools if re.search(rf"\b{t}\b", v))
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
    walk([m.get("title"), m.get("description"), {k: v for k, v in (m.get("spec") or {}).items() if k != PROGRAM}])
    return sorted(set(found))


def check_machine(m: dict) -> None:
    """P4's checks on the whole declaration, beyond the envelope Orchestra checks."""
    spec, kind, mode = m.get("spec") or {}, m.get("kind"), (m.get("engagement") or {}).get("mode")
    if kind == "universal":
        allowed = (*UNIVERSAL, PROGRAM)
    else:
        allowed = (*BY_MODE.get(mode, ()), *(EXTERNAL if m.get("managed") == "external" else ()), SPECIFICATION)
    if extra := sorted(set(spec) - set(allowed)):
        raise StoreError(f"a {kind} {mode or ''} machine's spec has {', '.join(allowed)}; not {', '.join(extra)}")
    if kind == "universal":
        for k in UNIVERSAL:
            if not isinstance(spec.get(k, []), list):
                raise StoreError(f"spec.{k} is a list")
        if words := ai_terms(m):
            raise StoreError(f"a Universal Machine is not described in AI terms (no model, prompt or tool names): {', '.join(words)}")
    if spec.get("protocol") is not None and spec["protocol"] not in PROTOCOLS:
        raise StoreError(f"protocol is one of {', '.join(PROTOCOLS)}")
    if spec.get("trigger") and m.get("managed") != "external":
        raise StoreError("a trigger (a remote call that starts it) is for a machine managed elsewhere (managed: external)")
    bad = [t for t in [*m.get("receives", []), *m.get("emits", [])] if not EVENT.match(t)]
    if bad:
        raise StoreError(f"not event types (family.name): {bad}")


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s if s[:1].isalpha() or not s else f"x_{s}"


def engagement(a: dict, default: str | None = "ad hoc") -> dict | None:
    """The engagement from flat arguments (mode, at, tz, missed); None when none is given and no default."""
    mode = a.get("mode") or default
    if mode is None:
        if any(a.get(k) for k in ("at", "tz", "missed")):
            raise StoreError("at, tz and missed go with mode scheduled")
        return None
    mode = "ad hoc" if mode == "ad_hoc" else mode
    e = {"mode": mode, **{k: a[k] for k in ("at", "tz", "missed") if a.get(k) is not None}}
    if mode not in MODES:
        raise StoreError(f"mode is one of {', '.join(MODES)}")
    if mode != "scheduled" and len(e) > 1:
        raise StoreError("at, tz and missed belong to a scheduled engagement only")
    if mode == "scheduled" and not re.fullmatch(r"\d{2}:\d{2}", str(e.get("at") or "")):
        raise StoreError("a scheduled machine runs at a time of day: at \"HH:MM\" (00:00 for midnight)")
    if e.get("missed") not in (None, "skip", "run once"):
        raise StoreError("missed is skip or run once")
    return e


def machine_id(a: dict) -> str:
    mid = (a.get("name") or "").strip()
    mid = mid if ID.match(mid) else slug(mid or a.get("title") or "")
    if not ID.match(mid):
        raise StoreError("the machine's name (its bus source) is lowercase letters, digits and underscores, 2 to 41 characters")
    return mid


def current(project: Project, mid: str) -> dict:
    m = (intended_api.current(project).get("machines") or {}).get(mid)
    if m is None:
        raise StoreError(f"no machine {mid!r} is declared (declare_machine)")
    return m


def _t(e: dict) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(e.get("ts"))).timestamp()
    except ValueError:
        return 0.0


def _recent(store: AgentStore, n: int = 20_000) -> list[dict]:
    end = store.events.end()
    return store.events.read(max(0, end - n), limit=n)[0]


def runs_a_day(store: AgentStore, m: dict) -> tuple[float, str]:
    """How many turns a day it would take, and how that was worked out: a scheduled machine by its
    schedule (once a day at its ``at``), one woken by events by how often they came in the last day."""
    e = m.get("engagement") or {}
    if e.get("mode") == "scheduled":
        return 1.0, f"scheduled once a day at {e.get('at') or '?'}"
    since, types = time.time() - DAY, set(m.get("receives") or [])
    n = float(sum(1 for ev in _recent(store) if ev.get("type") in types and _t(ev) >= since))
    return n, f"woken by {', '.join(sorted(types)) or 'nothing'}: {n:.0f} such events in the last day"


def cost_line(store: AgentStore, m: dict) -> str:
    per_day, how = runs_a_day(store, m)
    return (f"as drafted: about {per_day:.0f} turn(s) a day on {DEFAULT_MODEL}, about ${per_day * TURN_USD:.2f} a day ({how}); "
            "unconfirmed, it never runs and costs nothing")


def listeners(project: Project, store: AgentStore, types: list[str], but: str) -> dict[str, list[str]]:
    """Who listens for each type: declared machines (their receives) and announced ones."""
    out = {t: [] for t in types}
    for mid, m in (intended_api.current(project).get("machines") or {}).items():
        if mid != but:
            for t in types:
                if t in (m.get("receives") or []):
                    out[t].append(mid)
    for e in _recent(store, 5000):
        if e.get("type") == "orchestra.machine.started":
            for t in types:
                if t in (e.get("listens_for") or []) and e.get("source") not in out[t]:
                    out[t].append(str(e.get("source")))
    return out


def draft(project: Project, m: dict) -> Path | None:
    """The machine's implementation, written again from the declaration: a proposed Universal
    one's draft in ``drafts/``; a declared one's live files in ``machines/`` (``state_machines``)."""
    d = drafts_dir(project) / m["id"]
    if m.get("kind") != "universal":
        shutil.rmtree(d, ignore_errors=True)
        return None
    if m.get("state") == "declared":
        return state_machines.write(project, m)
    out = state_machines.files(m)
    out["machine.toml"] = "# A draft, not official: written by the Supervisor from the declaration; confirm moves it to machines/.\n" \
        + out["machine.toml"]
    return state_machines.write_files(d, out)


def declare_machine(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    mid = machine_id(a)
    kind = a.get("kind")
    m = {"id": mid, "title": (a.get("title") or "").strip() or mid.replace("_", " ").title(),
         "description": (a.get("description") or "").strip(), "tags": list(a.get("tags") or []), "kind": kind,
         "managed": a.get("managed") or "orchestra", "engagement": engagement(a),
         "receives": list(a.get("receives") or []), "emits": list(a.get("emits") or []), "reads": list(a.get("reads") or []),
         "spec": {k: v for k, v in dict(a.get("spec") or {}).items() if v not in (None, "", [])}}
    if kind not in ("universal", "programmatic"):
        raise StoreError("kind is universal (an abstract machine, described by its steps) or programmatic (a program)")
    if not m["description"]:
        raise StoreError("describe what it achieves, in the work's terms")
    check_machine(m)
    guessed = list(dict.fromkeys("engagement" if g in ("mode", "at", "tz", "missed") else g for g in a.get("guesses") or []))
    done = apply(project, store, [{"op": "propose_machine", **m, "guessed": guessed}], prov)
    d = draft(project, {**m, "state": "proposed"})
    who = "added by you (from your message)" if prov["author"] == "owner" else "proposed by the Supervisor"
    lines = [f"machine {mid} ({m['title']}) declared, {kind}, {who}: unconfirmed",
             page_line(project, "the Machines page draws it dashed, \"unconfirmed\""), cost_line(store, m)]
    if a.get("guesses"):
        lines.append(f"guessed: {', '.join(a['guesses'])}")
    unheard = [t for t, who_ in listeners(project, store, m["emits"], mid).items() if not who_]
    if unheard:
        lines.append(f"no listener yet for {', '.join(unheard)} (the refinement pass will propose one)")
    if d:
        lines.append(f"draft implementation: {d} (not official until confirmed)")
    return answer(project, done, lines, machine=mid, draft=str(d) if d else None)


LIST_EDITS = (("add_receives", "receives", True), ("remove_receives", "receives", False), ("add_emits", "emits", True),
              ("remove_emits", "emits", False), ("add_reads", "reads", True), ("remove_reads", "reads", False))
FIELDS = ("title", "description", "tags", "managed")


def _switch(spec: dict, to: str) -> dict:
    """The spec after a switch of kind: the other kind's fields kept aside (module docstring)."""
    spec = dict(spec or {})
    if to == "programmatic":
        desc = {k: spec.pop(k) for k in UNIVERSAL if k in spec}
        program = spec.pop(PROGRAM, {}) or {}
        return {**program, **({SPECIFICATION: desc} if desc else {})}
    desc = spec.pop(SPECIFICATION, {}) or {}
    return {**desc, **({PROGRAM: spec} if spec else {})}


def edits_of(a: dict, m: dict) -> list[tuple[str, dict]]:
    """The flat arguments as Orchestra's ``revise_machine`` ops, each with its label, in order, on
    the machine as the earlier ones leave it (``m`` is changed in place)."""
    ops: list[tuple[str, dict]] = []
    mid = m["id"]

    def one(label: str, op: dict) -> None:
        for k, v in (op.get("set") or {}).items():
            m[k] = v
        for k, items in (op.get("add") or {}).items():
            m[k] = list(m.get(k) or []) + items
        for k, items in (op.get("remove") or {}).items():
            m[k] = [x for x in m.get(k) or [] if x not in items]
        ops.append((label, {"op": "revise_machine", "id": mid, **{k: v for k, v in op.items() if k != "op"}}))

    for arg, field, add in LIST_EDITS:
        for v in a.get(arg) or []:
            if add == (v in (m.get(field) or [])):
                raise StoreError(f"{arg}: {field} {'has' if add else 'has no'} {v}")
            one(f"{arg} {v}", {"add" if add else "remove": {field: [v]}})
    def spec() -> dict:                      # the spec as the earlier edits left it
        return dict(m.get("spec") or {})

    for arg, add in (("add_invariants", True), ("remove_invariants", False)):
        for text in a.get(arg) or []:
            cur = list(spec().get("invariants") or [])
            if add == (text in cur):
                raise StoreError(f"{arg}: invariants {'has' if add else 'has no'} {text!r}")
            one(f"{arg} {text[:40]}", {"set": {"spec": {**spec(), "invariants": cur + [text] if add else [x for x in cur if x != text]}}})
    if a.get("steps") is not None:
        if not isinstance(a["steps"], list) or not a["steps"]:
            raise StoreError("steps is the whole new numbered list")
        one("steps", {"set": {"spec": {**spec(), "steps": list(a["steps"])}}})
    if (e := engagement(a, default=None)) is not None:
        one(f"engagement {e['mode']}", {"set": {"engagement": e}})
    if (to := a.get("set_kind")) is not None:
        if to not in ("universal", "programmatic") or to == m.get("kind"):
            raise StoreError(f"set_kind is the other kind ({'programmatic' if m.get('kind') == 'universal' else 'universal'})")
        one(f"kind {to}", {"set": {"kind": to, "spec": _switch(spec(), to)}})
    if field := (a.get("set_field") or "").strip():
        if field.startswith("spec."):
            one(f"set {field}", {"set": {"spec": {**spec(), field[5:]: a.get("value")}}})
        elif field in FIELDS:
            one(f"set {field}", {"set": {field: a.get("value")}})
        else:
            raise StoreError(f"set_field sets {', '.join(FIELDS)} or spec.<field>")
    return ops


def refine_machine(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    mid = machine_id(a)
    m = current(project, mid)
    if prov.get("request"):
        others = [k for k in a if k not in ("name", "set_kind", "request", "reason", "quote") and a[k] not in (None, "", [])]
        if a.get("set_kind") is None or others:
            raise StoreError("a request backs the kind switch alone (set_kind); other changes are quoted or proposed, in a call "
                             "of their own")
        check_request(project, store, prov, "switch_kind", "machine", mid)
    after = json.loads(json.dumps(m))
    ops = edits_of(a, after)
    if not ops:
        raise StoreError("say what changes: add_receives, remove_receives, add_emits, remove_emits, add_reads, remove_reads, "
                         "add_invariants, remove_invariants, steps, mode (with at, tz, missed), set_kind, or set_field and value")
    check_machine(after)
    done = apply(project, store, [op for _, op in ops], prov)
    now = current(project, mid)
    waiting = {p["change"] for p in now.get("pending") or []}
    pending = any(c["change"] in waiting for c in done["changes"])
    if not pending:
        draft(project, now)
    lines = [f"{len(ops)} change(s) to {mid}: " + ", ".join(f"{c['change']} {label}" for c, (label, _) in zip(done["changes"], ops, strict=True))]
    if pending:
        lines.append("recorded as a proposed change: the owner confirms or discards it in the chat")
    else:
        lines.append("applied" + (" (in answer to the owner's request)" if prov.get("request") else ""))
        lines.append(page_line(project, "the Machines page marks it \"just now\", with what it replaced"))
        if runs_a_day(store, after)[0] != runs_a_day(store, m)[0]:
            lines.append(cost_line(store, after))
    return answer(project, done, lines, machine=mid, pending=pending, kind=now.get("kind"))


def declare_event(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    t, purpose = (a.get("type") or "").strip(), (a.get("purpose") or "").strip()
    if not EVENT.match(t) or not purpose:
        raise StoreError("an event type is family.name (work.improvement_proposed), with its purpose in a sentence")
    done = apply(project, store, [{"op": "declare_event", "type": t, "description": purpose}], prov)
    return answer(project, done, [f"event type {t} declared: {purpose}"])


def _declared(layer: dict, eid) -> dict | None:
    return ((layer.get("stocks") or {}).get(eid) or (layer.get("effects") or {}).get(eid)) if eid else None


def publishable(layer: dict, e: dict) -> dict | None:
    """The edit as it can be published now, its declared ends read as the series they were linked
    to; None while one of them has none ("waits for a series"). Orchestra's rule
    (``orchestra.sd.intended.drafts.publishable``: a ``stock`` and an ``appears`` flow's ``from`` and
    ``to``), consulted when installed, else the same rule here."""
    ends = [k for k in ("stock", "from", "to") if k in e]
    try:
        from orchestra.sd.intended.drafts import publishable as core_rule
    except ImportError:
        core_rule = None
    if core_rule is not None:
        names = {e[k]: e[k] for k in ends if e.get(k) and _declared(layer, e[k]) is None}
        ok, _ = core_rule(e, {"stocks": layer.get("stocks") or {}, "effects": layer.get("effects") or {}}, names)
        if not ok:
            return None
    elif any(e.get(k) and (d := _declared(layer, e[k])) is not None and not d.get("found_as") for k in ends):
        return None
    out = dict(e)
    for k in ends:
        if e.get(k) and (d := _declared(layer, e[k])) is not None:
            out[k] = d["found_as"]
    return out


def cannot_judge(e: dict) -> str | None:
    """Why this Orchestra could not judge the edit once published, or None."""
    return "this Orchestra cannot judge rises or falls yet" if is_slope(e) and not slopes() else None


def min_horizon(project: Project, store: AgentStore, m: dict) -> tuple[float, str]:
    """The least horizon, and why: three of its runs, or what discovery needs of a new era (its
    minimum steps on its grid, and the share of its window a stock must cover), whichever is longer."""
    try:
        from orchestra.sd.discover import MIN_COVER, MIN_STEPS
    except ImportError:
        MIN_COVER, MIN_STEPS = 0.5, 5
    cfg = project.orchestra.config
    era = max(MIN_STEPS * cfg.discovery_step, MIN_COVER * cfg.discovery_window)
    per_day, how = runs_a_day(store, m)
    runs = 3 * DAY / per_day if per_day > 0 else 0.0
    if runs >= era:
        return runs, f"three of its runs ({how})"
    return era, "what discovery needs to see a new era" + (f" (three of its runs, {how}, take less)" if per_day else "")


def declare_expected_effect(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    mid = machine_id({"name": a.get("machine")})
    m = current(project, mid)
    expect = a.get("expect")
    if not isinstance(expect, list) or not expect:
        raise StoreError("expect is a list of edits in SPEC's kinds; a machine with no prediction should not be there at all")
    edits = [expect_edit({k: v for k, v in e.items() if v is not None} if isinstance(e, dict) else e) for e in expect]
    try:
        horizon = parse_duration(a.get("horizon"), 0.0)
    except ConfigError as e:
        raise StoreError(f"horizon: {e}") from None
    floor, why = min_horizon(project, store, m)
    lines = []
    if horizon < floor:
        lines.append(f"horizon raised to {floor / DAY:.3g} days: {why}")
        horizon = floor
    if horizon <= 0:
        raise StoreError("horizon is how long after the machine starts to judge it: 3d, 7d")
    done = apply(project, store, [{"op": "set_expected_effect", "machine": mid, "edits": edits, "horizon_s": horizon}], prov)
    layer = intended_api.current(project)
    waiting = [e.get("stock") or e.get("to") or e.get("from") for e in edits if publishable(layer, e) is None]
    lines.insert(0, f"expected effect of {mid}: {len(edits)} edit(s), judged {horizon / DAY:.3g} days after it starts; a draft, "
                    "published only when the owner confirms the machine")
    if waiting:
        lines.append(f"{', '.join(waiting)} has no series yet: that edit waits for a series (link it once it is found)")
    if unjudged := [why for e in edits if (why := cannot_judge(e))]:
        lines.append(f"{unjudged[0]}: such an edit is kept and not published until it can")
    return answer(project, done, lines, machine=mid)


COMMANDS = {"declare_machine": declare_machine, "refine_machine": refine_machine, "declare_event": declare_event,
            "declare_expected_effect": declare_expected_effect}
