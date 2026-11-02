"""The Machines side of the design tools (machine-design.md §2.3, §2.5, §4): a machine declared
by name with its envelope and its ``spec`` (the fields of §2.3 per kind and mode, which Orchestra
stores opaquely), refined edit by edit with the value each replaced, the event types it names,
and its expected effect, a draft prediction published only at confirm (``design_confirm``).

A Universal Machine is not described in AI terms: its declaration names no model, prompt or
tool (``ai_terms``). Its implementation is the plugin's alone, drafted in the state directory
(``drafts/<name>/``, in the format a confirmed one is run from: ``state_machines.files``), so a
draft is not official and a discard leaves nothing; each refinement redrafts it.

**The kind switch** (§4, Universal | Programmatic, ``switch_kind``) changes ``kind`` alone, which
is what Orchestra applies at once in answer to the owner's click: the spec is kept as it is, so a
Programmatic Machine keeps its Universal fields (``invariants``, ``steps``, ``uses``) as the
specification the program is written from, and a Universal one keeps the program's fields it had.

**Publishing a draft** (``publishable``): an edit's ``stock``, ``from`` and ``to`` are keys or
declared ids, which hold across versions; the judge reads the era's ids, so each is resolved
against the current model (``intended_api.resolve``) when it is published. With Orchestra's layer
installed, Orchestra's own verdict on each edit (its "unpublishable draft" items) is read too."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from orchestra.config import ConfigError, parse_duration

from . import design_asked, design_named, intended_api, state_machines
from .config import Project
from .design import MACHINE, OWNER, answer, apply, check_request, plural
from .expect_grammar import expect_edit
from .store import AgentStore, StoreError

ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")         # a machine's id: the source it publishes under
MODES = ("ad hoc", "continuous", "scheduled")
UNIVERSAL = ("invariants", "steps", "uses")
PROTOCOLS = ("http", "graphql", "grpc", "other")
BY_MODE = {"ad hoc": ("command", "trigger", "arguments", "environment", "stdin", "exit", "role"),
           "scheduled": ("command", "trigger", "arguments", "environment", "stdin", "exit"),
           "continuous": ("command", "protocol", "send", "receive")}
PROGRAM_FIELDS = tuple(dict.fromkeys(f for fs in BY_MODE.values() for f in fs))
EXTERNAL = ("source", "log")
SPEC_KEYS = tuple(dict.fromkeys((*UNIVERSAL, *PROGRAM_FIELDS, *EXTERNAL)))
AI_WORDS = re.compile(r"\b(AI|LLM|LLMs|GPT|haiku|sonnet|opus|claude|gemini|prompt|prompts|language model|tool call|tool group)\b",
                      re.IGNORECASE)
EVENT = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
DAY = 86_400.0
# the flat shape of one expected edit (tools_design.EXPECT): its kind, and the fields each kind needs
EXPECT_KINDS = {"factor": ("flow", "factor"), "below": ("stock", "value"), "above": ("stock", "value"),
                "rises": ("stock",), "falls": ("stock",), "appears": ("to",)}


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
    walk([m.get("title"), m.get("description"), {k: v for k, v in (m.get("spec") or {}).items() if k in UNIVERSAL}])
    return sorted(set(found))


def check_machine(m: dict, kept: set[str] | frozenset[str] = frozenset()) -> None:
    """P4's checks on the whole declaration, beyond the envelope Orchestra checks. A Programmatic
    Machine may keep the Universal fields as its specification; a Universal one keeps the program's
    fields it already had (``kept``: the stored spec's fields), from before a switch."""
    spec, kind, mode = m.get("spec") or {}, m.get("kind"), (m.get("engagement") or {}).get("mode")
    external = EXTERNAL if m.get("managed") == "external" else ()
    if kind == "universal":
        allowed = (*UNIVERSAL, *[f for f in PROGRAM_FIELDS if f in kept], *external)
    else:
        allowed = (*BY_MODE.get(mode, ()), *external, *UNIVERSAL)
    if extra := sorted(set(spec) - set(allowed)):
        raise StoreError(f"a {kind} {mode or ''} machine's spec has {', '.join(allowed)}; not {', '.join(extra)}")
    for k in UNIVERSAL:
        if not isinstance(spec.get(k, []), list):
            raise StoreError(f"spec.{k} is a list")
    if kind == "universal" and (words := ai_terms(m)):
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
    if e.get("tz"):
        from .clock import zone
        try:
            zone(e["tz"])
        except ConfigError as err:
            raise StoreError(str(err)) from None
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
    from .design import recent_events
    return recent_events(store, n)


def _span(seconds: float) -> str:
    if seconds < 120:
        return f"{seconds:.0f} s"
    if seconds < 7200:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.0f} h"


def runs_a_day(store: AgentStore, m: dict, now: float | None = None) -> tuple[float, str]:
    """How many turns a day it would take, and how that was worked out: a scheduled machine by its
    schedule (once a day at its ``at``), one woken by events by how often they came in the last day,
    read by time; when what was read covers less than a day (a young bus, or a busy one), scaled up
    to a day, saying so."""
    e = m.get("engagement") or {}
    if e.get("mode") == "scheduled":
        return 1.0, f"scheduled once a day at {e.get('at') or '?'}" + (f" {e['tz']}" if e.get("tz") else "")
    now = now or time.time()
    since, types = now - DAY, set(m.get("receives") or [])
    read = _recent(store)
    n = sum(1 for ev in read if ev.get("type") in types and _t(ev) >= since)
    woken = f"woken by {', '.join(sorted(types)) or 'nothing'}"
    covered = now - min((_t(ev) for ev in read), default=since)          # what was read reaches back this far
    if not n or covered >= DAY - 60:
        return float(n), f"{woken}: {plural(n, 'such event')} in the last day"
    per_day = n * DAY / max(covered, 60.0)
    return per_day, (f"{woken}: {plural(n, 'such event')} in the last {_span(covered)}, about {round(per_day):,} a day")


def turn_usd(project: Project) -> float | None:
    """What one turn of a designed machine costs on this project's harness and model (the harness's
    estimate, ``Harness.turn_usd``), or None when the harness does not know."""
    from .harness import get_harness
    h = get_harness(project.config.harness)
    return h.turn_usd(h.model_for(state_machines.DEFAULT_MODEL) or project.config.model or state_machines.DEFAULT_MODEL)


def cost_line(project: Project, store: AgentStore, m: dict) -> str:
    per_day, how = runs_a_day(store, m)
    usd = turn_usd(project)
    cost = f"about ${per_day * usd:.2f} a day" if usd is not None else "at a cost per turn this harness does not report"
    return (f"as drafted: about {plural(round(per_day), 'turn')} a day on {state_machines.runs_on(project)}, {cost} "
            f"({how}); unconfirmed, it never runs and costs nothing")


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


def reads_of(project: Project, store: AgentStore | None, m: dict) -> dict[str, str]:
    """Each stock or effect the machine reads, as a line its role can act on: a declared one through
    its link (``read_series <key>``), else the series its instrument records (not linked yet), else
    that nothing records it; anything else is a discovered key, read as it is."""
    from .design_instruments import drafts
    lay = intended_api.current(project)
    recs = drafts(store) if store is not None else {}
    out = {}
    for r in m.get("reads") or []:
        decl = (lay.get("stocks") or {}).get(r) or (lay.get("effects") or {}).get(r)
        if decl is None:
            out[r] = f"{r}: read_series {r}"
            continue
        name = decl.get("name") or r
        if decl.get("found_as"):
            out[r] = f"{name}: read_series {decl['found_as']}"
            continue
        measure = decl.get("measure") or {}
        chosen = next((o for o in measure.get("options") or [] if o.get("key") == measure.get("chosen")), None) or {}
        mine = [n for n, rec in recs.items() if str(rec.get("element", "")).partition(":")[2] == r]
        inst = chosen.get("instrument") or next((n for n in mine if recs[n].get("kept")), None) or next(iter(mine), None)
        series = (recs.get(inst) or {}).get("series") or []
        out[r] = (f"{name}: read_series {', '.join(series)} (instrument {inst}); not linked in the model yet" if series else
                  f"{name}: recorded by instrument {inst}, its series not read yet; not linked in the model yet" if inst else
                  f"{name}: nothing records it yet")
    return out


def rewrite_readers(project: Project, store: AgentStore, eid: str) -> list[str]:
    """The confirmed machines run here that read ``eid``, their records written again (after a
    link or a keep changed what they read); their ids."""
    done = []
    for mid, m in (intended_api.current(project).get("machines") or {}).items():
        if m.get("state") == "declared" and eid in (m.get("reads") or []) and draft(project, m, store) is not None:
            done.append(mid)
    return done


def draft(project: Project, m: dict, store: AgentStore | None = None) -> Path | None:
    """The machine's implementation, written again from the declaration: a proposed Universal
    one's draft in ``drafts/``; a declared one's live files in ``machines/`` (``state_machines``),
    unless it is managed elsewhere (nothing runs here). ``store``: to name the series its reads
    are recorded as (``reads_of``)."""
    d = drafts_dir(project) / m["id"]
    if m.get("kind") != "universal" or m.get("managed") == "external":
        shutil.rmtree(d, ignore_errors=True)
        return None
    reads = reads_of(project, store, m)
    if m.get("state") == "declared":
        return state_machines.write(project, m, reads)
    out = state_machines.files(m, reads)
    out["machine.toml"] = "# A draft, not official: written by the Supervisor from the declaration; confirm moves it to machines/.\n" \
        + out["machine.toml"]
    return state_machines.write_files(d, out)


def spec_of(a: dict) -> dict:
    """The kind's fields from declare_machine's flat arguments."""
    return {k: a[k] for k in SPEC_KEYS if a.get(k) not in (None, "", [])}


NO_RECEIVES = ("receives is empty: the events it watches, or reads at each run (a scheduled machine too: scheduling it never "
               "takes them out), are event types in receives (add_receives), never only words in steps or uses; read_events "
               "lists the types machines speak")


SCHEDULE_KEEPS = ("nothing changed: a change of engagement (mode, at, tz, missed) never takes out receives. A scheduled "
                  "machine reads its receives at each run instead of waking on each one, so scheduling it keeps them all; "
                  "give the engagement alone. remove_receives is only for events the owner says it should stop "
                  "watching, in a call of its own")


def type_lines(project: Project, store: AgentStore, mid: str, receives: list[str], emits: list[str]) -> list[str]:
    """What to say of event types nothing speaks (``bus_reads.known_types``): receives must be
    heard, announced, or emitted by another declared machine; a new emit is declared first."""
    from .bus_reads import unknown_line
    return [x for x in (unknown_line(project, store, receives, "receives", mid),
                        unknown_line(project, store, emits, "emits", mid)) if x]


def runs_it(m: dict) -> str:
    """What runs a machine a plugin proposed, in its proposal's words (its source and description)."""
    src = (m.get("provenance") or {}).get("source") or (m.get("provenance") or {}).get("author") or "a plugin"
    desc = (m.get("description") or "").strip().rstrip(".")
    return f"{src}" + (f" ({desc})" if desc else "")


def proposed_elsewhere(project: Project, mid: str, title: str) -> None:
    """The owner's name matches a machine a plugin already proposed (the Workflows plugin's
    ``review_nudger``, run by its workflow's program): that one is refined and confirmed, never a
    second declared beside it."""
    machines = intended_api.current(project).get("machines") or {}
    want = {mid, slug(title or "")}
    m = next(({**x, "id": k} for k, x in machines.items()
              if (x.get("provenance") or {}).get("author") not in (OWNER, MACHINE, None)
              and (k in want or slug(x.get("title") or k) in want)), None)
    if m is None:
        return
    author = (m.get("provenance") or {}).get("author")
    raise StoreError(f"not declared again: the name is machine {m['id']}'s, which {author} proposed; {runs_it(m)} runs it. "
                     f"This is the machine the owner means: refine it now, in this turn (refine_machine name {m['id']}, their "
                     "quote, only what their words change: title, receives, mode with at and tz, steps), record what they "
                     f"expect (declare_expected_effect), then confirm it if they asked; tell them it is {m['id']}, run by "
                     f"{runs_it(m)}, and ask any follow-up after")


def declare_machine(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    mid = machine_id(a)
    kind = a.get("kind")
    named, named_lines, dropped = design_named.resolve(project, store, a.get("owner_named"), mid)
    m = {"id": mid, "title": (a.get("title") or "").strip() or mid.replace("_", " ").title(),
         "description": (a.get("description") or "").strip(), "tags": list(a.get("tags") or []), "kind": kind,
         "managed": a.get("managed") or "orchestra", "engagement": engagement(a),
         "receives": list(dict.fromkeys([*(t for t in a.get("receives") or [] if t not in dropped), *named])), "emits": list(a.get("emits") or []),
         "reads": list(a.get("reads") or []), "spec": {k: v for k, v in spec_of(a).items() if v not in (None, "", [])}}
    if kind not in ("universal", "programmatic"):
        raise StoreError("kind is universal (an abstract machine, described by its steps) or programmatic (a program)")
    if not m["description"]:
        raise StoreError("describe what it achieves, in the work's terms")
    check_machine(m)
    proposed_elsewhere(project, mid, m["title"])
    design_asked.check(store, mid, m["managed"])
    guessed = list(dict.fromkeys("engagement" if g in ("mode", "at", "tz", "missed") else g for g in a.get("guesses") or []))
    done = apply(project, [{"op": "propose_machine", **m, "guessed": guessed}], prov)
    design_asked.note(store, mid)
    d = draft(project, {**m, "state": "proposed"}, store)
    who = "added by you (from your message)" if prov["author"] == OWNER else "proposed by the Supervisor"
    lines = [f"machine {mid} ({m['title']}) declared, {kind}, {who}: unconfirmed",
             "the Machines page draws it dashed, \"unconfirmed\"", cost_line(project, store, m),
             state_machines.unmapped_line(m)]
    if a.get("guesses"):
        lines.append(f"guessed: {', '.join(a['guesses'])}")
    unheard = [t for t, who_ in listeners(project, store, m["emits"], mid).items() if not who_]
    if unheard:
        lines.append(f"no listener yet for {', '.join(unheard)} (the refinement pass will propose one)")
    lines += type_lines(project, store, mid, m["receives"], m["emits"]) + named_lines
    if not m["receives"]:
        lines.append(NO_RECEIVES)
    if d:
        lines.append(f"draft implementation: {d} (not official until confirmed)")
    return answer(project, done, lines, machine=mid, draft=str(d) if d else None)


LIST_EDITS = (("add_receives", "receives", True), ("remove_receives", "receives", False), ("add_emits", "emits", True),
              ("remove_emits", "emits", False), ("add_reads", "reads", True), ("remove_reads", "reads", False),
              ("add_tags", "tags", True), ("remove_tags", "tags", False))
SPEC_LISTS = (("add_invariants", "invariants", True), ("remove_invariants", "invariants", False),
              ("add_uses", "uses", True), ("remove_uses", "uses", False))
TEXT_FIELDS = ("title", "description")
SPEC_TEXT = ("command", "trigger", "arguments", "environment", "stdin", "exit", "protocol", "send", "receive", "source", "role")


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

    for arg, field, add in SPEC_LISTS:
        for text in a.get(arg) or []:
            cur = list(spec().get(field) or [])
            if add == (text in cur):
                raise StoreError(f"{arg}: {field} {'has' if add else 'has no'} {text!r}")
            one(f"{arg} {text[:40]}", {"set": {"spec": {**spec(), field: cur + [text] if add else [x for x in cur if x != text]}}})
    if a.get("steps") is not None:
        if not isinstance(a["steps"], list) or not a["steps"]:
            raise StoreError("steps is the whole new numbered list")
        one("steps", {"set": {"spec": {**spec(), "steps": list(a["steps"])}}})
    if (e := engagement(a, default=None)) is not None:
        one(f"engagement {e['mode']}", {"set": {"engagement": e}})
    if field := (a.get("set_field") or "").strip():
        value = a.get("value")
        if not isinstance(value, str):
            raise StoreError("value is the field's new text")
        if field.startswith("spec.") and field[5:] in SPEC_TEXT:
            one(f"set {field}", {"set": {"spec": {**spec(), field[5:]: value}}})
        elif field in TEXT_FIELDS:
            one(f"set {field}", {"set": {field: value}})
        else:
            raise StoreError(f"set_field sets a text field: {', '.join(TEXT_FIELDS)}, or spec.<{'|'.join(SPEC_TEXT)}>; "
                             "the lists have their add_ and remove_ edits, the engagement its mode")
    return ops


def fold_spec(ops: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """A confirmed machine's spec edits of one call as one ``revise_machine`` (one pending change):
    each sets the whole spec as the earlier ones left it, so as separate pending changes, settling
    one would restore or drop another. The folded op takes the first one's place."""
    spec = [i for i, (_, op) in enumerate(ops) if set(op.get("set") or {}) == {"spec"} and not op.get("add") and not op.get("remove")]
    if len(spec) < 2:
        return ops
    one = ("spec: " + "; ".join(ops[i][0] for i in spec), {**ops[spec[-1]][1]})
    return [one if i == spec[0] else x for i, x in enumerate(ops) if i == spec[0] or i not in spec]


def _pending(project: Project, mid: str, done: dict) -> tuple[dict, bool]:
    now = current(project, mid)
    waiting = {p["change"] for p in now.get("pending") or []}
    return now, any(c["change"] in waiting for c in done["changes"])


def refine_machine(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    mid = machine_id(a)
    m = current(project, mid)
    if prov.get("request"):
        raise StoreError("a page request backs a switch of kind (switch_kind), a confirm or a discard; a change of the "
                         "declaration is quoted or proposed")
    if a.get("remove_receives") and any(a.get(k) for k in ("mode", "at", "tz", "missed")):
        raise StoreError(SCHEDULE_KEEPS)
    named, named_lines, dropped = design_named.resolve(project, store, a.get("owner_named"), mid)
    if dropped and a.get("add_receives"):                # a type the owner's words name is in receives only once heard
        a = {**a, "add_receives": [t for t in a["add_receives"] if t not in dropped]}
    if extra := [t for t in named if t not in (m.get("receives") or []) and t not in (a.get("add_receives") or [])]:
        a = {**a, "add_receives": [*(a.get("add_receives") or []), *extra]}
    after = json.loads(json.dumps(m))
    ops = edits_of(a, after)
    folded = m.get("state") == "declared" and len(fold_spec(ops)) < len(ops)
    if m.get("state") == "declared":
        ops = fold_spec(ops)
    if not ops:
        raise StoreError("say what changes: add_/remove_ receives, emits, reads, tags, invariants or uses; steps; mode (with "
                         "at, tz, missed); or set_field and value. A switch of kind is switch_kind")
    check_machine(after, kept=set(m.get("spec") or {}))
    done = apply(project, [op for _, op in ops], prov)
    now, pending = _pending(project, mid, done)
    if not pending:
        draft(project, now, store)
    lines = [f"{plural(len(ops), 'change')} to {mid}: " + ", ".join(f"{c['change']} {label}"
                                                                   for c, (label, _) in zip(done["changes"], ops, strict=True))]
    if folded:
        spec_change = next(c["change"] for c, (label, _) in zip(done["changes"], ops, strict=True) if label.startswith("spec: "))
        lines.append(f"the spec edits are one change, {spec_change}: confirmed or discarded together")
    if pending:
        lines.append("recorded as a proposed change: the owner confirms or discards it in the chat")
    else:
        lines += ["applied", "the Machines page marks it \"just now\", with what it replaced",
                  state_machines.unmapped_line(now)]
        if runs_a_day(store, after)[0] != runs_a_day(store, m)[0]:
            lines.append(cost_line(project, store, after))
    lines += type_lines(project, store, mid, list(a.get("add_receives") or []), list(a.get("add_emits") or [])) + named_lines
    if any(a.get(k) for k in ("mode", "at", "tz")) and (m.get("provenance") or {}).get("author") not in (OWNER, MACHINE, None):
        lines.append(f"{mid} is run by {runs_it(m)}: the engagement recorded here is the declaration and changes no run "
                     "time. When a workflow's clock starts it (list_workflows: its every start and clock), declare that "
                     "start with at and tz (declare_workflow) for the time the owner named, and state only the run time the "
                     "workflow's clock holds")
    if (a.get("steps") or a.get("add_uses") or a.get("remove_receives")) and not after.get("receives"):
        lines.append(NO_RECEIVES)
    return answer(project, done, lines, machine=mid, pending=pending, kind=now.get("kind"))


def runs_here(m: dict) -> bool:
    """Whether the agents' host runs it: a Universal Machine managed here."""
    return m.get("kind") == "universal" and m.get("managed") != "external"


def apply_kind(project: Project, store: AgentStore, before: dict, after: dict, prov: dict) -> tuple[list[str], dict]:
    """A confirmed machine whose ``kind`` or ``managed`` changed, made so here: no longer run here
    (Programmatic, or managed elsewhere), its agent stopped, its records removed, its wake-ups
    drained and, for a program to be written here, the program handed to the Coordinator; run here
    again (Universal, managed here), its records written. What to say, and the hand-off's id."""
    from .handoff import hand_to_coordinator
    mid = after["id"]
    lines: list[str] = []
    extra: dict = {}
    if runs_here(before) and not runs_here(after):
        stopped = state_machines.remove(project, mid)
        drained = store.drain_wakeups(mid)
        shutil.rmtree(drafts_dir(project) / mid, ignore_errors=True)
        lines.append(("its agent is stopped and its records in the state directory removed" + (
            f" ({plural(len(drained), 'queued wake-up')} dropped)" if drained else "") + ": nothing runs it here")
                     if stopped else "no agent ran it")
        if after.get("kind") == "programmatic" and after.get("managed") != "external":
            req = hand_to_coordinator(store, {"requirements": (
                f"Write the program for the confirmed machine {mid} ({after.get('title')}), now Programmatic: "
                f"{after.get('description')}. It receives {', '.join(after.get('receives') or []) or 'nothing'}, emits "
                f"{', '.join(after.get('emits') or []) or 'nothing'} and is engaged {json.dumps(after.get('engagement'))}; its "
                f"specification is {json.dumps(after.get('spec'))}."), "context": prov["reason"]}, MACHINE)
            lines.append(f"the program is handed to the Coordinator ({req['request']}): a worker writes it on a ticket")
            extra["request"] = req["request"]
        elif after.get("managed") == "external":
            lines.append("it is managed elsewhere now: the declaration waits to be heard on the bus")
    elif runs_here(after) and not runs_here(before):
        dst = state_machines.write(project, after, reads_of(project, store, after))
        lines.append(f"it runs as an agent here again: its spec, role and agent records are written to {dst}")
    return lines, extra


def switch_kind(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    """The Universal | Programmatic switch: ``kind`` alone, on the owner's click (``request``) or words."""
    mid = machine_id(a)
    m = current(project, mid)
    to = "programmatic" if m.get("kind") == "universal" else "universal"
    check_request(project, store, prov, "switch_kind", "machine", mid)
    check_machine({**m, "kind": to}, kept=set(m.get("spec") or {}))
    done = apply(project, [{"op": "revise_machine", "id": mid, "set": {"kind": to}}], prov)
    now, pending = _pending(project, mid, done)
    lines = [f"{mid} is {'to be ' if pending else ''}{to} ({done['changes'][0]['change']}); its spec is kept as it was"
             + (": the Universal description is the program's specification" if to == "programmatic" else "")]
    extra: dict = {}
    if pending:
        lines.append("recorded as a proposed change: the owner confirms or discards it (when confirmed, it is made so here)")
    elif now.get("state") != "declared":
        draft(project, now, store)
        lines.append("unconfirmed: nothing runs" + (", and its draft implementation is gone" if to == "programmatic" else
                                                    "; its draft implementation is written again"))
    else:
        said, extra = apply_kind(project, store, m, now, prov)
        lines += said
    return answer(project, done, lines, machine=mid, pending=pending, kind=now.get("kind"), **extra)


def declare_event(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    t, purpose = (a.get("type") or "").strip(), (a.get("purpose") or "").strip()
    if not EVENT.match(t) or not purpose:
        raise StoreError("an event type is family.name (work.improvement_proposed), with its purpose in a sentence")
    done = apply(project, [{"op": "declare_event", "type": t, "description": purpose}], prov)
    return answer(project, done, [f"event type {t} declared: {purpose}"])


def publishable(project: Project, lay: dict, mid: str, e: dict, keys: intended_api.Keys | None = None,
                core: dict | None = None) -> tuple[dict | None, str | None]:
    """The edit as the judge reads it now (its ends the current model's ids), or None and why it
    waits. ``core``: Orchestra's own verdicts (``intended_api.unpublishable``), read when not given;
    ``keys``: the current model's, read when not given."""
    core = intended_api.unpublishable(project) if core is None else core
    if (why := core.get((mid, json.dumps(e, sort_keys=True)))) is not None:
        return None, why
    keys = keys if keys is not None else intended_api.Keys(intended_api.current_model(project))
    out = dict(e)
    for k in ("stock", "from", "to"):
        if k in e:
            sid, _, why = intended_api.resolve(e[k], lay, keys)
            if why is not None:
                return None, why
            out[k] = sid
    return out, None


def waiting_line(project: Project, store: AgentStore, lay: dict, e: dict, why: str) -> str:
    """Why one edit waits, and what publishes it: a declared stock or effect waits for its link
    (discovery never finds a declared id); anything else for discovery to find it."""
    from .design_instruments import drafts
    name = next((e[k] for k in ("stock", "flow", "to", "from") if e.get(k)), "?")
    decl = (lay.get("stocks") or {}).get(name) or (lay.get("effects") or {}).get(name)
    if decl is not None and decl.get("found_as"):
        return (f"{decl.get('name') or name} is linked to {decl['found_as']}, which is not in the current model: published "
                "once discovery finds it")
    if decl is not None:                     # a declared stock or effect: a link publishes it, whatever reason is given
        rec = [n for n, r in drafts(store).items() if r.get("element", "").endswith(f":{name}")]
        return (f"{decl.get('name') or name} is not linked to a discovered stock yet: published once you say which one it is "
                f"(link)" + (f"; {', '.join(rec)} records it now" if rec else ""))
    return f"{name} is not in the current model: published once discovery finds it"


def min_horizon(project: Project, store: AgentStore, m: dict) -> tuple[float, str]:
    """The least horizon, and why: three of its runs, or what discovery needs of a new era,
    whichever is longer."""
    era = intended_api.min_era_s(project.orchestra.config)
    per_day, how = runs_a_day(store, m)
    runs = 3 * DAY / per_day if per_day > 0 else 0.0
    if runs >= era:
        return runs, f"three of its runs ({how})"
    return era, "what discovery needs to see a new era" + (f" (three of its runs, {how}, take less)" if per_day else "")


def flat_edit(e) -> dict:
    """One expected edit from its flat form (``kind`` and the fields it needs), checked here with
    an error that names what is missing; an edit already in SPEC's shape is taken as it is."""
    if not isinstance(e, dict):
        raise StoreError(f"each expected edit is an object with its kind, not {e!r}")
    e = {k: v for k, v in e.items() if v is not None}
    kind = e.pop("kind", None)
    if kind is None:
        return e
    if kind not in EXPECT_KINDS:
        raise StoreError(f"an expected edit's kind is one of {', '.join(EXPECT_KINDS)}, not {kind!r}")
    if missing := [f for f in EXPECT_KINDS[kind] if e.get(f) in (None, "")]:
        raise StoreError(f"kind {kind} needs {' and '.join(EXPECT_KINDS[kind])} ({', '.join(missing)} missing)")
    if extra := sorted(set(e) - set(EXPECT_KINDS[kind]) - ({"from"} if kind == "appears" else set())):
        raise StoreError(f"kind {kind} takes {', '.join(EXPECT_KINDS[kind])}" + (" and from" if kind == "appears" else "")
                         + f", not {', '.join(extra)}")
    if kind == "factor":
        return {"flow": e["flow"], "factor": e["factor"]}
    if kind in ("below", "above"):
        return {"stock": e["stock"], kind: e["value"]}
    if kind in ("rises", "falls"):
        return {"stock": e["stock"], kind: True}
    return {"from": e.get("from"), "to": e["to"], "appears": True}


def flows_line(project: Project, into: str | None = None) -> str:
    """The current model's flows, as a factor names them (id: from -> to), the ones into ``into`` first."""
    model = intended_api.current_model(project)
    if model is None:
        return "the model has no flows yet (no discovery saved): read_model once discovery has run"
    names = {s_.id: s_.name or s_.id for s_ in model.stocks}
    rows = sorted(model.flows, key=lambda f: (into is None or into not in (f.dst, f.src), f.id))
    shown = [f"{f.id} ({names.get(f.src, 'outside') if f.src else 'outside'} -> {names.get(f.dst, 'outside') if f.dst else 'outside'})"
             for f in rows[:12]]
    return "the model's flows: " + "; ".join(shown) + (f"; and {len(rows) - 12} more (read_model)" if len(rows) > 12 else "")


def stock_as_flow(project: Project, edits: list[dict], lay: dict | None = None) -> None:
    """Refused: an edit that names a stock (a level), or anything else that is not one of the
    model's flows (a measured input, a series), where a flow (a rate) is expected, as
    ``{flow: repo_commits, factor: 1.2}`` for "the commit rate": the message lists the flows. With
    no model saved yet nothing is refused (the edit waits for discovery)."""
    model = intended_api.current_model(project)
    keys = intended_api.Keys(model)
    lay = lay if lay is not None else intended_api.current(project)
    stock_ids = {s_.id: s_.id for s_ in model.stocks} if model else {}
    for e in edits:
        name = e.get("flow")
        if not isinstance(name, str) or name in keys.flows or name in {f.id for f in (model.flows if model else [])}:
            continue
        sid = stock_ids.get(name) or keys.stocks.get(name) or next(
            (v for k, v in keys.stocks.items() if k.split("{", 1)[0] == name), None)
        declared = name in (lay.get("stocks") or {}) or name in (lay.get("effects") or {})
        if sid or declared:
            raise StoreError(f"{name} is a stock (a level), not a flow: a factor multiplies a flow's rate. A rate of "
                             f"{name} is a flow into it; {flows_line(project, sid)}. For a level, use rises, falls, above or "
                             "below on the stock")
        if model is not None and model.flows:            # an input, a series, or nothing the model has: no rate to multiply
            raise StoreError(f"{name} is not a flow of the model (a measured input or a series is not a rate it judges): a "
                             f"factor names one of its flows; {flows_line(project, sid)}. For a level, use rises, falls, "
                             "above or below on a stock")


def declare_expected_effect(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    mid = machine_id({"name": a.get("machine")})
    m = current(project, mid)
    expect = a.get("expect")
    if not isinstance(expect, list) or not expect:
        raise StoreError("expect is a list of edits in SPEC's kinds; a machine with no prediction should not be there at all")
    edits = [expect_edit(flat_edit(e)) for e in expect]
    stock_as_flow(project, edits)
    try:
        horizon = parse_duration(a.get("horizon"), 0.0)
    except ConfigError as e:
        raise StoreError(f"horizon: {e}") from None
    floor, why = min_horizon(project, store, m)
    lines: list[str | None] = []
    if horizon < floor:
        lines.append(f"horizon raised to {floor / DAY:.3g} days: {why}")
        horizon = floor
    if horizon <= 0:
        raise StoreError("horizon is how long after the machine starts to judge it: 3d, 7d")
    done = apply(project, [{"op": "set_expected_effect", "machine": mid, "edits": edits, "horizon_s": horizon}], prov)
    lay = intended_api.current(project)
    confirmed = m.get("state") != "proposed"
    lines.insert(0, f"expected effect of {mid}: {plural(len(edits), 'edit')}, judged {horizon / DAY:.3g} days after it starts; "
                    + ("recorded as a proposed change; published once confirmed" if confirmed else
                       "a draft, published only when the owner confirms the machine"))
    stored = (lay["machines"][mid].get("expected_effect") or {}).get("edits") or [] if not confirmed else edits
    core, keys = intended_api.unpublishable(project), intended_api.Keys(intended_api.current_model(project))
    for e in stored:
        if (w := publishable(project, lay, mid, e, keys=keys, core=core)[1]) is not None:
            lines.append(waiting_line(project, store, lay, e, w))
        elif (line := flow_waits(project, e, keys)) is not None:
            lines.append(line)
    return answer(project, done, lines, machine=mid)


def flow_waits(project: Project, e: dict, keys: intended_api.Keys) -> str | None:
    """What a factor on a flow the current model does not have waits for (no model saved yet, or
    one without that flow): it is recorded, as the owner said it, and judged once discovery finds
    the flow. None when the flow is the model's."""
    name = e.get("flow")
    if not isinstance(name, str):
        return None
    model = intended_api.current_model(project)
    if model is not None and (name in keys.flows or name in {f.id for f in model.flows}):
        return None
    now = "there is no model yet" if model is None else "the current model has no such flow"
    return (f"recorded, waiting for discovery: {name} ×{e.get('factor')} is kept as the owner said it; {now}, so it is "
            "judged once discovery finds that flow (read_model says what discovery needs). Nothing more to do now")


COMMANDS = {"declare_machine": declare_machine, "refine_machine": refine_machine, "switch_kind": switch_kind,
            "declare_event": declare_event, "declare_expected_effect": declare_expected_effect}
