"""The refinement passes over the event map (machine-design.md §3; the owner: "an AI pass should
probably run a few times, try to make sure no events have publishers but no subscribers, make sure
the purposes of events aren't duplicated, make sure that events aren't excessively deduplicated
and serving multiple distinct purposes, other refinement things").

The Supervisor's ``refine_map`` duty. Its quick part runs here without a model, in the review
round (``review.py``) when the declared layer changed since the last pass: up to ``[agents]
design_passes`` passes (default 3), each reading the map with the previous passes' findings on it,
stopping when a pass finds nothing new. **It proposes, never declares**: the round's findings are
batched into one proposal (the Supervisor's agent state ``refinement``, shown by ``read_design``),
each with its reason, and the Supervisor is woken once with them to put them to the owner, who
confirms or discards; the judgement a check needs (which listener, which name to keep) is the
Supervisor's turn. The wake-up counts against ``review_usd_per_day`` like the round's own."""

from __future__ import annotations

import re
import time

from .design import MACHINE

BUSY_PER_DAY = 200          # a machine woken by events more often than this is a candidate for a schedule
EXPIRING_S = 2 * 3600.0     # a draft instrument this close to expiring unkept is a loose end


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (s or "").casefold()) if len(w) > 3}


def _live(layer: dict, box: str) -> dict:
    return dict(layer.get(box) or {})


def check(layer: dict, events: list[dict], drafts: dict, seen: set[str], now: float | None = None) -> list[dict]:
    """One pass: the findings not in ``seen``, each ``{key, check, text, propose}``."""
    now = now or time.time()
    machines, types = _live(layer, "machines"), dict(layer.get("events") or {})
    observed = {e.get("type") for e in events}
    heard = {t for e in events if e.get("type") == "orchestra.machine.started" for t in e.get("listens_for") or []}
    emitted = {t: [m for m, d in machines.items() if t in d.get("emits", [])] for d in machines.values() for t in d.get("emits", [])}
    received = {t: [m for m, d in machines.items() if t in d.get("receives", [])] for d in machines.values() for t in d.get("receives", [])}
    out: list[dict] = []

    def add(key: str, kind: str, text: str, propose: str) -> None:
        if key not in seen and all(f["key"] != key for f in out):
            out.append({"key": key, "check": kind, "text": text, "propose": propose})

    for t, by in emitted.items():                                                    # 1. emitted, never heard
        if not received.get(t) and t not in heard:
            users = [m for m, d in machines.items() if m not in by and _words(t) & _words(" ".join(map(str, (d.get("spec") or {}).get("steps") or [])))]
            add(f"unheard:{t}", "emitted, never heard", f"{t} is emitted by {', '.join(by)} and nobody listens",
                f"add it to the receives of {', '.join(users)}" if users else f"name a listener, or remove the emit from {', '.join(by)}")
    for t, by in received.items():                                                   # 2. heard, never emitted
        if not emitted.get(t) and t not in observed:
            add(f"unemitted:{t}", "heard, never emitted", f"{', '.join(by)} listen(s) for {t}, which nothing emits or was ever heard",
                "declare the machine that would emit it (an external one under the relayed actor's source), or drop the receive")
    described = {t: d.get("description") or "" for t, d in types.items() if d.get("description")}
    names = sorted(described)
    for i, a in enumerate(names):                                                    # 3. one purpose, two names
        for b in names[i + 1:]:
            wa, wb = _words(described[a]), _words(described[b])
            if wa and wb and len(wa & wb) / len(wa | wb) >= 0.8:
                keep = a if a in observed or b not in observed else b
                add(f"same_purpose:{a}|{b}", "one purpose, two names", f"{a} and {b} have the same purpose",
                    f"keep {keep} ({'heard on the bus' if keep in observed else 'the first'}) and use it for both")
    for t, purpose in described.items():                                             # 4. one name, several purposes
        if re.search(r"\b(or|either)\b|;", purpose, re.IGNORECASE):
            add(f"overloaded:{t}", "one name, several purposes", f"{t}'s purpose needs \"or\": {purpose}",
                f"split it into a family ({t}.<each purpose>), one type a purpose")
    for m, d in machines.items():                                                    # 5. no prediction
        if not (d.get("expected_effect") or {}).get("edits"):
            add(f"no_prediction:{m}", "no prediction", f"{m} has no expected effect",
                "propose one (declare_expected_effect), or ask the owner what it should change: a machine with none should not be there")
    since = now - 86_400
    for m, d in machines.items():                                                    # 6. engagement against load
        if (d.get("engagement") or {}).get("mode") == "scheduled":
            continue
        n = sum(1 for e in events if e.get("type") in set(d.get("receives") or []) and _ts(e) >= since)
        if n > BUSY_PER_DAY:
            add(f"busy:{m}", "engagement against load", f"{m} would be woken {n} times a day by {', '.join(d.get('receives') or [])}",
                "make it scheduled (once a day reads the same events in one turn)")
    for s, d in _live(layer, "stocks").items():                                      # 7. loose ends
        if not d.get("found_as") and not (d.get("measure") or {}).get("chosen"):
            add(f"unmeasured:{s}", "loose end", f"the declared stock {s} has no instrument and no series",
                "propose_measurements and add the first option")
    for name, rec in drafts.items():
        if not rec.get("kept") and rec.get("until") and 0 < rec["until"] - now < EXPIRING_S:
            add(f"expiring:{name}", "loose end", f"the draft instrument {name} expires unkept in under two hours",
                "ask the owner whether to keep it")
    return out


def _ts(e: dict) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(e.get("ts"))).timestamp()
    except ValueError:
        return 0.0


def round_of(layer: dict, events: list[dict], drafts: dict, passes: int, already: set[str] = frozenset(), now: float | None = None) -> dict:
    """A round: at most ``passes`` passes, each with the previous ones' findings, stopping when one
    finds nothing new; one proposal of everything new."""
    findings: list[dict] = []
    seen = set(already)
    ran = 0
    for _ in range(max(1, passes)):
        ran += 1
        new = check(layer, events, drafts, seen, now)
        if not new:
            break
        findings += new
        seen |= {f["key"] for f in new}
    return {"at": now or time.time(), "version": layer.get("version"), "passes": ran, "findings": findings}


def last_proposal(store) -> dict | None:
    return store.agent_state(MACHINE).get("refinement")


def run(project, store, events: list[dict]) -> dict | None:
    """The round, when the declared layer changed since the last one; the proposal, or None."""
    from . import intended_api
    from .design_instruments import drafts
    got = intended_api.layer(project).read()
    layer = {**got["layer"], "version": got["version"]}
    last = last_proposal(store) or {}
    if not layer.get("version") or layer.get("version") == last.get("version"):
        return None
    shown = set(last.get("shown") or [])
    proposal = round_of(layer, events, drafts(store), project.config.design_passes, shown)
    proposal["shown"] = sorted(shown | {f["key"] for f in proposal["findings"]})[-300:]
    store.set_agent_state(MACHINE, refinement=proposal)
    return proposal


def reason(proposal: dict, mark: str) -> str:
    lines = [(f"{mark} The refinement pass over the event map ({proposal['passes']} pass(es), declared layer "
              f"v{proposal['version']}) proposes, for the owner to confirm or discard:")]
    lines += [f"{i}. {f['text']}: {f['propose']}" for i, f in enumerate(proposal["findings"][:12], 1)]
    lines.append("Work it as your refine_map duty says: one message to the owner with these, declaring nothing until they answer.")
    return "\n".join(lines)
