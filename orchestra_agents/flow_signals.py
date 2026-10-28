"""The review round's quick check: is anything off in the flow, without a model call
(docs/proposals/agent-first-machines.md §5.1; the owner, 2026-10-28: cheap when nothing is wrong).

Read from what is already recorded, in this order, each a finding with a ``key`` (the same
thing found again keeps its key, so a round does not wake the Supervisor twice for it):

- **stalls still open**: ``orchestra.model.stalled`` with no ``orchestra.model.resumed`` for its
  subject since (Orchestra's own statements about the model: domain-free);
- **machines still quiet**: ``orchestra.machine.quiet`` with nothing from that machine since;
- **stocks still out of band**: ``orchestra.model.diverged`` with no ``returned`` since;
- **a subject bouncing**: one subject handed back (``changes.requested``, ``gate.rejected``,
  ``gate.conflict``) ``BOUNCES`` times or more in a day;
- **a plugin stuck**: ``gate.base_broken`` or ``pipeline.paused`` with no ``pipeline.resumed``
  since, an ``outbox.rate_limit.unresolvable``;
- **a measurement failing**: an instrument whose last round failed (Orchestra's runtime record,
  ``instruments``);
- **its own record**: a verdict on one of the Supervisor's predictions that did not pass, and
  fixes made without a prediction since the last round.

Event ``data`` is read only for Orchestra's own types and the subject of the others (the
envelope); a plugin's payload is not interpreted here."""

from __future__ import annotations

import time
from collections import Counter

from orchestra import runtime
from orchestra.events import event_time, machine_and_instance

from .config import Project
from .store import AgentStore

BOUNCES = 3
DAY = 86_400.0
FIX_TOOLS = frozenset({"restart_machine", "set_setting", "set_capacity_limit", "set_gate_unit", "set_machine", "unblock_ticket",
                       "restart_agent", "resume_pipeline", "propose_patch", "write_override", "remove_entry"})
BACK = ("changes.requested", "gate.rejected", "gate.conflict")


def _t(ev: dict) -> float:
    return event_time(ev) or 0.0


def _open(evs: list[dict], opened: str, closed: tuple[str, ...], key) -> dict[str, dict]:
    """The latest ``opened`` event per key with no ``closed`` one after it."""
    out: dict[str, dict] = {}
    for ev in evs:
        k = key(ev)
        if k is None:
            continue
        if ev.get("type") == opened:
            out[k] = ev
        elif ev.get("type") in closed:
            out.pop(k, None)
    return out


def check(project: Project, store: AgentStore, evs: list[dict], since: float, now: float | None = None) -> list[dict]:
    """The findings of one quick check: ``[{key, kind, text}]``, most pressing first. ``evs``: the
    bus's recent events (a day or more), oldest first; ``since``: the last round."""
    now = now or time.time()
    out: list[dict] = []
    for subj, ev in _open(evs, "orchestra.model.stalled", ("orchestra.model.resumed",), lambda e: e.get("subject")).items():
        what = "flow" if subj.startswith("flow:") else "stock"
        out.append({"key": f"stalled:{subj}:{ev.get('since')}", "kind": "stalled",
                    "text": f"{what} {subj.split(':', 1)[-1]} stalled since {_ago(ev.get('since') or _t(ev), now)}"
                            + (f" (level {ev.get('level')}, rose by {ev.get('rose_by')})" if what == "stock" else "")})
    last_seen: dict[str, float] = {}
    for ev in evs:
        m = machine_and_instance(ev.get("source"))[0]
        if m:
            last_seen[m] = max(last_seen.get(m, 0.0), _t(ev))
    for subj, ev in _open(evs, "orchestra.machine.quiet", (), lambda e: e.get("subject") or e.get("machine")).items():
        if last_seen.get(subj, 0.0) > _t(ev):
            continue                                             # heard from since
        out.append({"key": f"quiet:{subj}:{ev.get('last_seen')}", "kind": "quiet",
                    "text": f"machine {subj} quiet since {_ago(ev.get('last_seen') or _t(ev), now)} (usually every "
                            f"{round(ev.get('usual_gap') or 0)} s)"})
    diverged: dict[str, dict] = {}
    for ev in evs:
        for s in ev.get("stocks") or [] if ev.get("type") in ("orchestra.model.diverged", "orchestra.model.returned") else []:
            sid = s.get("stock") if isinstance(s, dict) else None
            if sid and ev["type"] == "orchestra.model.diverged":
                diverged[sid] = s
            elif sid:
                diverged.pop(sid, None)
    for sid, s in diverged.items():
        out.append({"key": f"diverged:{sid}:{s.get('since')}", "kind": "diverged",
                    "text": f"stock {sid} out of its predicted band since {_ago(s.get('since') or now, now)} (gap {s.get('gap')})"})
    bounces = Counter(ev.get("subject") for ev in evs if ev.get("type") in BACK and ev.get("subject") and now - _t(ev) < DAY)
    for subj, n in bounces.most_common():
        if n >= BOUNCES:
            out.append({"key": f"bouncing:{subj}:{time.strftime('%Y-%m-%d', time.localtime(now))}", "kind": "bouncing",
                        "text": f"{subj} handed back {n} times in a day (changes requested, gate rejected or conflicted)"})
    for ev in _open(evs, "gate.base_broken", ("pipeline.resumed",), lambda e: "gate").values():
        out.append({"key": f"base_broken:{ev.get('id')}", "kind": "plugin", "text": f"the base branch broke the gate {_ago(_t(ev), now)}"})
    for ev in _open(evs, "pipeline.paused", ("pipeline.resumed",), lambda e: "pipeline").values():
        out.append({"key": f"paused:{ev.get('id')}", "kind": "plugin", "text": f"the pipeline paused {_ago(_t(ev), now)}"})
    for ev in evs:
        if ev.get("type") == "outbox.rate_limit.unresolvable" and _t(ev) >= since:
            out.append({"key": f"outbox:{ev.get('id')}", "kind": "plugin", "text": "the outbox cannot resolve a rate limit"})
    out += _instruments(project)
    out += _own_record(store, evs, since)
    return out


def _instruments(project: Project) -> list[dict]:
    try:
        rec = runtime.read(project.orchestra)
    except Exception:  # noqa: BLE001 - no runtime record: nothing is known to fail
        return []
    out = []
    for name, h in sorted((rec.get("instruments") or {}).items()):
        err_at, ok_at = h.get("last_error_at") or 0.0, h.get("last_write") or 0.0
        if err_at and err_at > ok_at:
            out.append({"key": f"instrument:{name}:{round(ok_at)}", "kind": "measurement",
                        "text": f"instrument {name} failing ({h.get('failures')} failures): {str(h.get('last_error'))[:160]}"})
    return out


def _own_record(store: AgentStore, evs: list[dict], since: float) -> list[dict]:
    st = store.agent_state("supervisor")
    mine = {p.get("id") for p in st.get("predictions") or [] if p.get("id")}
    out = []
    for ev in evs:
        if ev.get("type") == "orchestra.prediction.judged" and _t(ev) >= since and ev.get("prediction") in mine \
                and ev.get("verdict") not in ("passed", None):
            out.append({"key": f"verdict:{ev.get('prediction')}", "kind": "verdict",
                        "text": f"your prediction {ev.get('prediction')} was judged {ev.get('verdict')!r}"})
    fixes = [a for a in st.get("actions") or [] if a.get("at", 0) >= since and a.get("tool") in FIX_TOOLS]
    predicted = [a for a in st.get("actions") or [] if a.get("at", 0) >= since and a.get("tool") in ("predict_fix", "commit_prediction")]
    if len(fixes) > len(predicted):
        out.append({"key": f"unpredicted:{round(since)}", "kind": "unpredicted",
                    "text": f"{len(fixes) - len(predicted)} fix(es) since the last round without a prediction: "
                            + "; ".join(a.get("did", "")[:80] for a in fixes[:3])})
    return out


def _ago(t, now: float) -> str:
    try:
        s = max(0.0, now - float(t))
    except (TypeError, ValueError):
        return "a while"
    return f"{round(s / 60)} min ago" if s < 7200 else f"{s / 3600:.1f} h ago"
