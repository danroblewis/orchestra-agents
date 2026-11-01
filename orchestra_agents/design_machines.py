"""The Machines side of the design tools (machine-design.md §2.3, §2.5, §4): a machine declared
by name with its envelope and its ``spec`` (the fields of §2.3 per kind and mode, which Orchestra
stores opaquely), refined edit by edit with the value each replaced, the event types it names,
and its expected effect, a draft prediction published only at confirm (``design_confirm``).

A Universal Machine is not described in AI terms: its declaration names no model, prompt or
tool (``ai_terms``). Its implementation is the plugin's alone, drafted in the state directory
(``drafts/<name>/``: ``machine.toml`` and ``role.md``), so a draft is not official and a discard
leaves nothing; each refinement redrafts it."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from orchestra.config import ConfigError, parse_duration

from . import intended_api
from .config import Project
from .design import MACHINE, answer, apply
from .store import AgentStore, StoreError

ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")         # a machine's id: the source it publishes under
MODES = ("ad hoc", "continuous", "scheduled")
UNIVERSAL = ("invariants", "steps", "uses")
PROTOCOLS = ("http", "graphql", "grpc", "other")
BY_MODE = {"ad hoc": ("command", "trigger", "arguments", "environment", "stdin", "exit"),
           "scheduled": ("command", "trigger", "arguments", "environment", "stdin", "exit"),
           "continuous": ("command", "protocol", "send", "receive")}
EXTERNAL = ("source", "log")
AI_WORDS = re.compile(r"\b(AI|LLM|LLMs|GPT|haiku|sonnet|opus|claude|gemini|prompt|prompts|language model|tool call|tool group)\b",
                      re.IGNORECASE)
TURN_USD = 0.03          # an estimate of one Haiku turn of a narrow machine, for the cost line
DEFAULT_MODEL = "haiku"  # D20's default for narrow machines
EDITS = ("add_receive", "remove_receive", "add_emit", "remove_emit", "set_engagement", "set_field", "rewrite_steps",
         "add_invariant", "remove_invariant", "add_read", "remove_read", "set_kind")
EVENT = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


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
    walk([m.get("title"), m.get("description"), m.get("spec")])
    return sorted(set(found))


def check_machine(m: dict) -> None:
    """P4's checks on the whole declaration, beyond the envelope Orchestra checks."""
    spec, kind, mode = m.get("spec") or {}, m.get("kind"), (m.get("engagement") or {}).get("mode")
    allowed = UNIVERSAL if kind == "universal" else BY_MODE.get(mode, ()) + (EXTERNAL if m.get("managed") == "external" else ())
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


def _engagement(e) -> dict:
    e = {k: v for k, v in dict(e or {}).items() if v is not None}
    if e.get("mode") == "ad_hoc":
        e["mode"] = "ad hoc"
    if e.get("mode") not in MODES:
        raise StoreError(f"engagement is {{mode, at?, tz?, missed?}} with mode one of {', '.join(MODES)}")
    if e["mode"] != "scheduled" and any(k in e for k in ("at", "tz", "missed")):
        raise StoreError("at, tz and missed belong to a scheduled engagement only")
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


def runs_a_day(m: dict) -> float | None:
    e = m.get("engagement") or {}
    if e.get("mode") == "scheduled":
        return 1.0
    return None


def cost_line(store: AgentStore, m: dict) -> str:
    per_day = runs_a_day(m)
    if per_day is None:                         # woken by its events: as often as they came in the last day
        end = store.events.end()
        evs, _ = store.events.read(max(0, end - 20_000), limit=20_000)
        since = time.time() - 86_400
        per_day = float(sum(1 for e in evs if e.get("type") in set(m.get("receives") or []) and _t(e) >= since))
        how = f"woken by {', '.join(m.get('receives') or []) or 'nothing'}: {per_day:.0f} such events in the last day"
    else:
        how = "scheduled once a day"
    return f"as drafted: about {per_day:.0f} turn(s) a day on {DEFAULT_MODEL}, about ${per_day * TURN_USD:.2f} a day ({how}); unconfirmed, it runs never and costs nothing"


def _t(e: dict) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(e.get("ts"))).timestamp()
    except ValueError:
        return 0.0


def listeners(project: Project, store: AgentStore, types: list[str], but: str) -> dict[str, list[str]]:
    """Who listens for each type: declared machines (their receives) and announced ones."""
    out = {t: [] for t in types}
    for mid, m in (intended_api.current(project).get("machines") or {}).items():
        if mid != but:
            for t in types:
                if t in (m.get("receives") or []):
                    out[t].append(mid)
    end = store.events.end()
    evs, _ = store.events.read(max(0, end - 5000), limit=5000)
    for e in evs:
        if e.get("type") == "orchestra.machine.started":
            for t in types:
                if t in (e.get("listens_for") or []) and e.get("source") not in out[t]:
                    out[t].append(str(e.get("source")))
    return out


def draft(project: Project, m: dict) -> Path | None:
    """The Universal Machine's draft implementation, written again from the declaration."""
    d = drafts_dir(project) / m["id"]
    if m.get("kind") != "universal":
        shutil.rmtree(d, ignore_errors=True)
        return None
    d.mkdir(parents=True, exist_ok=True)
    e = m.get("engagement") or {}
    kind = {"scheduled": "scheduled", "continuous": "continuous"}.get(e.get("mode"), "ad_hoc")
    lines = ["# A draft, not official: written by the Supervisor from the declaration; confirm moves it to machines/.",
             f"name = {json.dumps(m['id'])}", f"title = {json.dumps(m.get('title') or m['id'])}",
             f"model = {json.dumps(DEFAULT_MODEL)}", f"kind = {json.dumps(kind)}"]
    if e.get("at"):
        lines.append(f"at = {json.dumps(e['at'])}")
    if e.get("tz"):
        lines.append(f"tz = {json.dumps(e['tz'])}")
    if kind != "scheduled":
        lines.append(f"wake_on = {json.dumps(m.get('receives') or [])}")
    lines += [f"reads_events = {json.dumps(m.get('receives') or [])}", f"emits = {json.dumps(m.get('emits') or [])}",
              f"tools = {json.dumps(['events'] + (['model'] if m.get('reads') else []))}"]
    (d / "machine.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    spec = m.get("spec") or {}
    role = [f"# {m.get('title') or m['id']}", "", m.get("description") or "", "", "## Never", ""]
    role += [f"- {x}" for x in spec.get("invariants") or []] + ["", "## Each run", ""]
    role += [f"{i}. {s}" for i, s in enumerate(spec.get("steps") or [], 1)]
    role += ["", f"Emit only {', '.join(m.get('emits') or []) or 'nothing'}; read only {', '.join(m.get('reads') or []) or 'nothing'}."]
    (d / "role.md").write_text("\n".join(role) + "\n", encoding="utf-8")
    return d


def declare_machine(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    mid = machine_id(a)
    kind = a.get("kind")
    m = {"id": mid, "title": (a.get("title") or "").strip() or mid.replace("_", " ").title(),
         "description": (a.get("description") or "").strip(), "tags": list(a.get("tags") or []), "kind": kind,
         "managed": a.get("managed") or "orchestra", "engagement": _engagement(a.get("engagement") or {"mode": "ad hoc"}),
         "receives": list(a.get("receives") or []), "emits": list(a.get("emits") or []), "reads": list(a.get("reads") or []),
         "spec": dict(a.get("spec") or {})}
    if kind not in ("universal", "programmatic"):
        raise StoreError("kind is universal (an abstract machine, described by its steps) or programmatic (a program)")
    if not m["description"]:
        raise StoreError("describe what it achieves, in the work's terms")
    check_machine(m)
    done = apply(project, store, [{"op": "propose_machine", **m, "guessed": list(a.get("guesses") or [])}], prov)
    d = draft(project, m)
    who = "added by you (from your message)" if prov["author"] == "owner" else "proposed by the Supervisor"
    lines = [f"machine {mid} ({m['title']}) declared, {kind}, {who}: unconfirmed; the Machines page draws it dashed",
             cost_line(store, m)]
    if a.get("guesses"):
        lines.append(f"guessed (tagged on the page): {', '.join(a['guesses'])}")
    unheard = [t for t, who_ in listeners(project, store, m["emits"], mid).items() if not who_]
    if unheard:
        lines.append(f"no listener yet for {', '.join(unheard)} (the refinement pass will propose one)")
    if d:
        lines.append(f"draft implementation: {d} (not official until confirmed)")
    return answer(project, done, lines, machine=mid, draft=str(d) if d else None)


def _ops(mid: str, e: dict, m: dict) -> dict:
    """One edit as Orchestra's ``revise_machine`` op on the machine as the call's earlier edits left
    it (``m``): list fields by add and remove, the rest set whole (a ``spec`` field: the whole spec)."""
    op = e.get("op")
    if op not in EDITS:
        raise StoreError(f"an edit's op is one of {', '.join(EDITS)}")

    def one(k: str) -> str:
        if not isinstance(e.get(k), str) or not e[k].strip():
            raise StoreError(f"{op} takes {k}")
        return e[k].strip()

    def listed(field: str, v: str, add: bool) -> dict:
        cur = m.get(field) or []
        if add == (v in cur):
            raise StoreError(f"{field} {'has' if add else 'has no'} {v}")
        return {"op": "revise_machine", "id": mid, ("add" if add else "remove"): {field: [v]}}

    def spec(k: str, v) -> dict:
        return {"op": "revise_machine", "id": mid, "set": {"spec": {**(m.get("spec") or {}), k: v}}}

    if op in ("add_receive", "remove_receive", "add_emit", "remove_emit"):
        return listed("receives" if "receive" in op else "emits", one("type"), op.startswith("add"))
    if op in ("add_read", "remove_read"):
        return listed("reads", one("element"), op.startswith("add"))
    if op in ("add_invariant", "remove_invariant"):
        cur, text = list((m.get("spec") or {}).get("invariants") or []), one("text")
        if op.startswith("add") == (text in cur):
            raise StoreError(f"invariants {'has' if op.startswith('add') else 'has no'} {text!r}")
        return spec("invariants", cur + [text] if op.startswith("add") else [x for x in cur if x != text])
    if op == "set_engagement":
        return {"op": "revise_machine", "id": mid, "set": {"engagement": _engagement({k: e.get(k) for k in ("mode", "at", "tz", "missed")})}}
    if op == "rewrite_steps":
        if not isinstance(e.get("steps"), list) or not e["steps"]:
            raise StoreError("rewrite_steps takes steps, the whole new numbered list")
        return spec("steps", e["steps"])
    if op == "set_kind":
        return {"op": "revise_machine", "id": mid, "set": {"kind": e.get("kind")}}
    field = (e.get("field") or "").strip()
    if field.startswith("spec."):
        return spec(field[5:], e.get("value"))
    if field not in ("title", "description", "tags", "managed"):
        raise StoreError("set_field sets title, description, tags, managed or spec.<field>")
    return {"op": "revise_machine", "id": mid, "set": {field: e.get("value")}}


def _locally(m: dict, op: dict) -> None:
    for k, v in (op.get("set") or {}).items():
        m[k] = v
    for k, items in (op.get("add") or {}).items():
        m[k] = list(m.get(k) or []) + items
    for k, items in (op.get("remove") or {}).items():
        m[k] = [x for x in m.get(k) or [] if x not in items]


def refine_machine(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    mid = machine_id(a)
    m = current(project, mid)
    edits = a.get("edits")
    if not isinstance(edits, list) or not edits:
        raise StoreError("edits is a list: [{op: add_receive, type: ...}, {op: set_engagement, mode: scheduled, at: \"00:00\"}, ...]")
    ops, after = [], json.loads(json.dumps(m))
    for i, e in enumerate(edits):
        try:
            op = _ops(mid, e if isinstance(e, dict) else {}, after)
            _locally(after, op)
        except StoreError as err:
            raise StoreError(f"edit {i + 1}: {err}; no edit was applied") from None
        ops.append(op)
    check_machine(after)
    if m.get("state") == "declared":            # a confirmed machine changes only when the owner confirms the change (P4 §2.3)
        prov = {**prov, "author": MACHINE}
    done = apply(project, store, ops, prov)
    pending = prov["author"] != "owner"
    if not pending:
        draft(project, after)
    lines = [f"{len(ops)} change(s) to {mid}: " + ", ".join(f"{c['change']} {e['op']}" for c, e in zip(done["changes"], edits, strict=True)),
             ("recorded as a proposed change: the owner confirms or discards it on the Machines page or in the chat" if pending else
              "applied: the Machines page marks it \"just now\", with what it replaced")]
    if not pending and runs_a_day(after) != runs_a_day(m):
        lines.append(cost_line(store, after))
    return answer(project, done, lines, machine=mid, pending=pending)


def declare_event(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    t, purpose = (a.get("type") or "").strip(), (a.get("purpose") or "").strip()
    if not EVENT.match(t) or not purpose:
        raise StoreError("an event type is family.name (work.improvement_proposed), with its purpose in a sentence")
    done = apply(project, store, [{"op": "declare_event", "type": t, "description": purpose}], prov)
    return answer(project, done, [f"event type {t} declared: {purpose}"])


def _edit(e: dict) -> dict:
    """One expected edit in SPEC's ``expect`` kinds (Orchestra checks the grammar again)."""
    if not isinstance(e, dict):
        raise StoreError("an expected edit is an object")
    if "flow" in e and isinstance(e.get("factor"), int | float):
        return e
    if e.get("appears") is True and "from" in e and "to" in e:
        return e
    if "stock" in e and (isinstance(e.get("below"), int | float) or isinstance(e.get("above"), int | float)
                         or e.get("rises") is True or e.get("falls") is True):
        return e
    raise StoreError("an expected edit is {flow, factor}, {from, to, appears: true}, {stock, below|above: x} or "
                     "{stock, rises|falls: true}: rates where you can, not cumulative stocks")


def publishable(layer: dict, e: dict) -> dict | None:
    """The edit as it can be published now (a declared stock read as the series it was linked to),
    or None while a declared stock it names has no series ("waits for a series")."""
    s = (layer.get("stocks") or {}).get(e.get("stock")) if "stock" in e else None
    if s is None:
        return e
    return {**e, "stock": s["found_as"]} if s.get("found_as") else None


def min_horizon(m: dict) -> float:
    """At least three runs: a daily machine is judged no sooner than three days after it starts."""
    return 3 * 86_400.0 if runs_a_day(m) else 0.0


def declare_expected_effect(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    mid = machine_id({"name": a.get("machine")})
    m = current(project, mid)
    expect = a.get("expect")
    if not isinstance(expect, list) or not expect:
        raise StoreError("expect is a list of edits in SPEC's kinds; a machine with no prediction should not be there at all")
    edits = [_edit(e) for e in expect]
    try:
        horizon = parse_duration(a.get("horizon"), 0.0)
    except ConfigError as e:
        raise StoreError(f"horizon: {e}") from None
    floor = min_horizon(m)
    lines = []
    if horizon < floor:
        lines.append(f"horizon raised to {floor / 86_400:.0f} days: at least three runs before it is judged")
        horizon = floor
    if horizon <= 0:
        raise StoreError("horizon is how long after the machine starts to judge it: 3d, 7d")
    done = apply(project, store, [{"op": "set_expected_effect", "machine": mid, "edits": edits, "horizon_s": horizon}], prov)
    layer = intended_api.current(project)
    waiting = [e["stock"] for e in edits if publishable(layer, e) is None]
    lines.insert(0, f"expected effect of {mid}: {len(edits)} edit(s), judged {horizon / 86_400:g} days after it starts; a draft, "
                    "published only when the owner confirms the machine")
    if waiting:
        lines.append(f"{', '.join(waiting)} has no series yet: that edit waits for a series (link it once it is found)")
    return answer(project, done, lines, machine=mid)


COMMANDS = {"declare_machine": declare_machine, "refine_machine": refine_machine, "declare_event": declare_event,
            "declare_expected_effect": declare_expected_effect}
