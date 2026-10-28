"""The Supervisor's scheduled review round, inside the host (docs/proposals/agent-first-machines.md
§4.3; the owner, 2026-10-28: every 30 minutes by default, cheap when nothing is wrong, with a daily
spending cap).

Every ``[review] every`` of the Supervisor's spec (default 30m, never under ``FLOOR_S``), the
host runs the quick check without a model call:

1. the flow's signals (``flow_signals.check``) since the last round;
2. the Supervisor's own overload signals (``load.measure``), kept in its agent state for the
   plugin's instrument; when they cross, a split proposal is written into its spec
   (``split_proposal``);
3. only when something new was found (a finding whose key the last rounds did not already wake
   it for) or a proposal was written: a wake-up, ``[Review round] ...``, a full turn. Past the day's
   cap (``budget.over``) the wake-up is not queued; the round says so in its record.

The record is the Supervisor's agent state ``review``: when it ran, what it found, whether it woke
the Supervisor or why not, and the next round's time (``next_review_at``, for the Agents page)."""

from __future__ import annotations

import time

from . import budget, flow_signals, load, prompting, split_proposal
from .clock import parse_interval
from .machine_spec import DEFAULT_REVIEW_EVERY
from .machine_spec import load as load_spec
from .org import effective_tools

MACHINE = "supervisor"
MARK = "[Review round]"
FLOOR_S = 300.0
LOOK_BACK = 20_000           # events read back for a round (a day or more on a busy bus)
REMEMBER = 200               # finding keys remembered, so the same finding wakes it once


def due_every(spec) -> float:
    try:
        return max(FLOOR_S, parse_interval(spec.review_every or DEFAULT_REVIEW_EVERY))
    except Exception:  # noqa: BLE001 - a spec's bad interval falls back to the default
        return max(FLOOR_S, parse_interval(DEFAULT_REVIEW_EVERY))


class ReviewRound:
    def __init__(self, host):
        self.host = host
        self.started = time.time()

    def tick(self, now: float | None = None) -> dict | None:
        """Run a round when one is due; what it did, or None."""
        h = self.host
        now = now or time.time()
        agent = h.agents.get(MACHINE)
        spec = load_spec(h.project, MACHINE)
        if agent is None or spec is None:
            return None
        st = h.store.agent_state(MACHINE)
        if st.get("paused") or st.get("deleted"):
            return None
        every = due_every(spec)
        last = (st.get("review") or {}).get("at") or self.started
        if st.get("next_review_at") != last + every:
            h.store.set_agent_state(MACHINE, next_review_at=last + every)
        if now - last < every:
            return None
        return self.run(agent, spec, last, now)

    def run(self, agent, spec, since: float, now: float) -> dict:
        h = self.host
        end = h.events.end()
        evs, _ = h.events.read(max(0, end - LOOK_BACK), LOOK_BACK)
        findings = flow_signals.check(h.project, h.store, evs, since, now)
        groups = effective_tools(agent, h.role_defs, h.teams)
        values = load.measure(h.project, h.store, agent, groups, prompting.agent_prompt(h.project, agent, h.role_defs, h.teams), evs, now)
        hit = load.crossed(values)
        proposal = split_proposal.maybe_write(h.project, h.store, spec, values, hit, now)
        st = h.store.agent_state(MACHINE)
        seen = list((st.get("review") or {}).get("seen") or [])
        new = [f for f in findings if f["key"] not in seen]
        record = {"at": now, "findings": [f["text"] for f in findings], "new": len(new), "crossed": [x["signal"] for x in hit],
                  "seen": (seen + [f["key"] for f in new])[-REMEMBER:]}
        reason = None
        if new or proposal:
            why_not = budget.over(h.store, h.cfg, now) if new and not proposal else None
            if why_not:
                record["woke"], record["skipped"] = False, why_not
                h.log(f"review round: {len(new)} new finding(s); the Supervisor is not woken: {why_not}")
            else:
                reason = self.reason(new, proposal)
                h.store.add_wakeup(MACHINE, reason)
                record["woke"] = True
        else:
            record["woke"] = False
        h.store.set_agent_state(MACHINE, review=record, load={**values, "at": now, "crossed": record["crossed"]},
                                next_review_at=now + due_every(spec))
        if not record["woke"] and not record.get("skipped"):
            h.log(f"review round: nothing new ({len(findings)} known finding(s)); no turn")
        return {**record, "reason": reason, "proposal": proposal}

    @staticmethod
    def reason(new: list[dict], proposal: dict | None) -> str:
        lines = [f"{MARK} The quick check of the flow found:"] if new else [f"{MARK}"]
        lines += [f"- {f['text']}" for f in new[:12]]
        if len(new) > 12:
            lines.append(f"- and {len(new) - 12} more")
        if proposal:
            lines.append(f"Your own overload signals crossed ({', '.join(proposal['signals'])}): a split proposal was written at "
                         f"{proposal['path']}. Read it and tell the owner in two lines which duties you would hand to a new "
                         "machine and why; split nothing.")
        if new:
            lines.append("Work it as your review_round duty says: the one thing that would improve the flow most.")
        return "\n".join(lines)


def initiative(reasons: list[str], duty_types: set[str]) -> bool:
    """Whether a Supervisor wake-up is its own initiative (what the day's cap limits): every
    reason a review round's, or an event only a duty of its spec listens for."""
    def own(r: str) -> bool:
        if r.startswith(MARK):
            return True
        if r.startswith("event "):
            return r.split()[1] in duty_types
        return False
    return bool(reasons) and all(own(r) for r in reasons)
