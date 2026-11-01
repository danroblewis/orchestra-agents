"""The refinement passes over the event map (machine-design.md §3; the owner: "an AI pass should
probably run a few times, try to make sure no events have publishers but no subscribers, make sure
the purposes of events aren't duplicated, make sure that events aren't excessively deduplicated
and serving multiple distinct purposes, other refinement things").

The Supervisor's ``refine_map`` duty, built into the plugin (its text, ``prompts/duties/refine_map.md``,
is part of the Supervisor's fixed prompt, as ``operator.md`` is), so every Supervisor has it with no
change to its spec and no commit. This quick part is **heuristic** and calls no model: word overlap for one
purpose under two names, "or" in a purpose for one name serving several, event counts for load.
The judgement (which listener, which name to keep, how to split) is the Supervisor's, in the one
wake-up a round sends. It runs in the review round (``review.py``) when the declared layer changed
since the last round, and after the owner's confirm or discard is answered (``design_confirm``).

A round is at most ``[agents] design_passes`` passes (default 3). The first runs every check of §3
(numbered as there); **a later pass follows the neighbours**: check 6 on the machines the previous
pass proposed to redraft, whose own neighbours may then need redrafting too, and the round stops
when a pass finds nothing new. **It proposes, never declares**: the round's findings are batched
into one proposal (the Supervisor's agent state ``refinement``, shown by ``read_design``), each with
its reason, for the owner to confirm or discard. The wake-up counts against ``review_usd_per_day``."""

from __future__ import annotations

import re
import time

from .design import MACHINE, plural

BUSY_PER_DAY = 200          # a machine woken by events more often than this is a candidate for a schedule
EXPIRING_S = 2 * 3600.0     # a draft instrument this close to expiring unkept is a loose end
ABSENT_VERSIONS = 3         # an expected part absent from this many discovered versions in a row is a loose end


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (s or "").casefold()) if len(w) > 3}


def _seq(change: str | None) -> int:
    m = re.match(r"c(\d+)\.", str(change or ""))
    return int(m.group(1)) if m else 0


def changed_since(layer: dict, version: int | None) -> set[str]:
    """The machines added or refined after declared version ``version`` (all of them when None)."""
    out = set()
    for mid, m in (layer.get("machines") or {}).items():
        seqs = [_seq(m.get("change")), _seq((m.get("latest") or {}).get("change")), *(_seq(p.get("change")) for p in m.get("pending") or [])]
        if version is None or max(seqs) > version:
            out.add(mid)
    return out


def collector(seen: set[str]) -> tuple[list[dict], object]:
    """A pass's findings, and the function that adds one not seen before (True when it did)."""
    out: list[dict] = []

    def add(key: str, n: int, kind: str, text: str, propose: str) -> bool:
        if key in seen or any(f["key"] == key for f in out):
            return False
        out.append({"key": key, "n": n, "check": kind, "text": text, "propose": propose})
        return True
    return out, add


def neighbours(machines: dict, focus: set[str], seen: set[str], add) -> set[str]:
    """Check 6: a machine whose receives or emits touch a focused machine's events, proposed for a
    redraft so the two fit; the machines proposed."""
    proposed = set()
    for mid in sorted(focus):
        m = machines.get(mid) or {}
        for other, d in machines.items():
            if other == mid:
                continue
            touch = sorted((set(m.get("emits") or []) & set(d.get("receives") or [])) |
                           (set(m.get("receives") or []) & set(d.get("emits") or [])))
            if touch and add(f"neighbour:{other}:{mid}", 6, "neighbours of a new or refined machine",
                             f"{other} and {mid} meet at {', '.join(touch)}",
                             f"redraft {other}'s steps, receives and emits so they fit {mid}'s"):
                proposed.add(other)
    return proposed


def check(layer: dict, events: list[dict], drafts: dict, seen: set[str], focus: set[str], now: float | None = None,
          absent: list[set[str]] | None = None) -> tuple[list[dict], set[str]]:
    """The first pass: every check of §3, check 6 on ``focus``; the findings not in ``seen``, each
    ``{key, n, check, text, propose}``, and the machines it proposed to redraft."""
    now = now or time.time()
    machines, types = dict(layer.get("machines") or {}), dict(layer.get("events") or {})
    observed = {e.get("type") for e in events}
    heard = {t for e in events if e.get("type") == "orchestra.machine.started" for t in e.get("listens_for") or []}
    emitted = {t: [m for m, d in machines.items() if t in d.get("emits", [])] for d in machines.values() for t in d.get("emits", [])}
    received = {t: [m for m, d in machines.items() if t in d.get("receives", [])] for d in machines.values() for t in d.get("receives", [])}
    out, add = collector(seen)
    for t, by in emitted.items():                                                    # 1
        if not received.get(t) and t not in heard:
            users = [m for m, d in machines.items() if m not in by and _words(t) & _words(" ".join(map(str, (d.get("spec") or {}).get("steps") or [])))]
            add(f"unheard:{t}", 1, "emitted, never heard", f"{t} is emitted by {', '.join(by)} and nobody listens",
                f"add it to the receives of {', '.join(users)}" if users else f"name a listener, or remove the emit from {', '.join(by)}")
    for t, by in received.items():                                                   # 2
        if not emitted.get(t) and t not in observed:
            listen = f"{', '.join(by)} listen{'s' if len(by) == 1 else ''} for {t}, which no declared machine emits"
            if owners := family_publishers(events, t):                               # a known machine owns the family
                add(f"unemitted:{t}", 2, "heard, never emitted", f"{listen} and is not heard on the bus yet; "
                    f"{', '.join(owners)} publish{'es' if len(owners) == 1 else ''} {t.split('.')[0]}.* already",
                    f"ask whether {owners[0]} publishes {t} (then it is the expected publisher), or use a type it does publish")
            else:
                add(f"unemitted:{t}", 2, "heard, never emitted", f"{listen} and is not heard on the bus yet",
                    "declare the machine that would emit it (an external one under the relayed actor's source), or drop "
                    "the receive")
    described = {t: d.get("description") or "" for t, d in types.items() if d.get("description")}
    names = sorted(described)
    for i, a in enumerate(names):                                                    # 3
        for b in names[i + 1:]:
            wa, wb = _words(described[a]), _words(described[b])
            if wa and wb and len(wa & wb) / len(wa | wb) >= 0.8:
                keep = a if a in observed or b not in observed else b
                add(f"same_purpose:{a}|{b}", 3, "one purpose, two names", f"{a} and {b} have the same purpose",
                    f"keep {keep} ({'heard on the bus' if keep in observed else 'the first'}) and use it for both")
    for t, purpose in described.items():                                             # 4
        if re.search(r"\b(or|either)\b|;", purpose, re.IGNORECASE):
            add(f"overloaded:{t}", 4, "one name, several purposes", f"{t}'s purpose needs \"or\": {purpose}",
                f"split it into a family ({t}.<each purpose>), one type a purpose")
    for m, d in machines.items():                                                    # 5
        if not (d.get("expected_effect") or {}).get("edits"):
            add(f"no_prediction:{m}", 5, "no prediction", f"{m} has no expected effect",
                "propose one (declare_expected_effect), or ask the owner what it should change: a machine with none should not be there")
    proposed = neighbours(machines, focus, seen, add)                                # 6
    since = now - 86_400
    for m, d in machines.items():                                                    # 7
        if (d.get("engagement") or {}).get("mode") == "scheduled":
            continue
        n = sum(1 for e in events if e.get("type") in set(d.get("receives") or []) and _ts(e) >= since)
        if n > BUSY_PER_DAY:
            add(f"busy:{m}", 7, "engagement against load", f"{m} would be woken {n} times a day by {', '.join(d.get('receives') or [])}",
                "make it scheduled (once a day reads the same events in one turn)")
    instrumented = {r.get("element") for r in drafts.values()}
    for part in ("stock", "effect"):                                                 # 8
        for s, d in dict(layer.get(f"{part}s") or {}).items():
            series = d.get("found_as") or (d.get("measure") or {}).get("chosen")
            if not series and f"{part}:{s}" not in instrumented:
                add(f"unmeasured:{s}", 8, "loose end", f"the declared {part} {s} has no instrument and no series",
                    "propose_measurements and add the first option")
            if d.get("target") and not series:
                add(f"target_unmeasured:{s}", 8, "loose end", f"the target on {s} has no series to be judged on",
                    "measure it, or link it to the series that measures it")
    for name, rec in drafts.items():
        if not rec.get("kept") and rec.get("until") and 0 < rec["until"] - now < EXPIRING_S:
            add(f"expiring:{name}", 8, "loose end", f"the draft instrument {name} expires unkept in under two hours",
                "ask the owner whether to keep it")
    if absent and len(absent) >= ABSENT_VERSIONS:
        for key in (layer.get("marks") or {}).get("expected") or {}:
            if all(key not in v for v in absent[-ABSENT_VERSIONS:]):
                add(f"absent:{key}", 8, "loose end", f"the expected {key} is absent from the last {ABSENT_VERSIONS} discovered versions",
                    "ask the owner whether it is still expected, or what replaced it")
    return out, proposed


def family_publishers(events: list[dict], t: str) -> list[str]:
    """The machines heard publishing other types of ``t``'s family (``gate.*``), by source, most first."""
    family = t.split(".", 1)[0] + "."
    counts: dict[str, int] = {}
    for e in events:
        typ, src = str(e.get("type") or ""), str(e.get("source") or "").split("/", 1)[0]
        if typ.startswith(family) and typ != t and src and src != "owner":
            counts[src] = counts.get(src, 0) + 1
    return sorted(counts, key=lambda s_: (-counts[s_], s_))


def _ts(e: dict) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(e.get("ts"))).timestamp()
    except ValueError:
        return 0.0


def round_of(layer: dict, events: list[dict], drafts: dict, passes: int, already: set[str] = frozenset(), now: float | None = None,
             focus: set[str] | None = None, absent: list[set[str]] | None = None) -> dict:
    """A round: the first pass, then at most ``passes - 1`` passes following the neighbours, each
    with the previous ones' findings, stopping when one finds nothing new; one proposal of it all."""
    seen = set(already)
    focus = set(layer.get("machines") or {}) if focus is None else set(focus)
    findings, proposed = check(layer, events, drafts, seen, focus, now, absent)
    seen |= {f["key"] for f in findings}
    ran, followed = 1, set(focus)
    while ran < max(1, passes) and (todo := proposed - followed):
        ran += 1
        followed |= todo
        new, add = collector(seen)
        proposed = neighbours(dict(layer.get("machines") or {}), todo, seen, add)
        if not new:
            break
        findings += new
        seen |= {f["key"] for f in new}
    return {"at": now or time.time(), "version": layer.get("version"), "passes": ran, "findings": findings}


def last_proposal(store) -> dict | None:
    return store.agent_state(MACHINE).get("refinement")


def _absent(project) -> list[set[str]]:
    """The stock and flow keys of the last saved versions, oldest first, as the declared layer's
    marks name them (``name{labels}`` for a stock, the level its counters imply for one no series
    measures, ``a -> b by counter`` for a flow)."""
    from .intended_api import Keys, all_keys, saved_models
    return [all_keys(Keys(m)) for m in saved_models(project, ABSENT_VERSIONS)]


def run(project, store, events: list[dict], force: bool = False) -> dict | None:
    """The round, when the declared layer changed since the last one (or ``force``, after the
    owner's confirm or discard); the proposal, or None."""
    from . import intended_api
    from .design_instruments import drafts
    got = intended_api.layer(project).read()
    layer = {**got["layer"], "version": got["version"]}
    last = last_proposal(store) or {}
    if not layer.get("version") or (layer.get("version") == last.get("version") and not force):
        return None
    shown = set(last.get("shown") or [])
    try:
        absent = _absent(project)
    except Exception:  # noqa: BLE001 - the model cannot be read now: that check waits
        absent = []
    proposal = round_of(layer, events, drafts(store), project.config.design_passes, shown,
                        focus=changed_since(layer, last.get("version")), absent=absent)
    proposal["shown"] = sorted(shown | {f["key"] for f in proposal["findings"]})[-300:]
    store.set_agent_state(MACHINE, refinement=proposal)
    return proposal


def reason(proposal: dict, mark: str) -> str:
    lines = [(f"{mark} The refinement pass over the event map ({plural(proposal['passes'], 'pass', 'passes')}, declared layer "
              f"v{proposal['version']}; a quick heuristic check, no model) proposes, for the owner to confirm or discard:")]
    lines += [f"{i}. (check {f['n']}) {f['text']}: {f['propose']}" for i, f in enumerate(proposal["findings"][:12], 1)]
    lines.append("Work it as your refine_map duty says: judge each, then one message to the owner with them, declaring "
                 "nothing until they answer.")
    return "\n".join(lines)
