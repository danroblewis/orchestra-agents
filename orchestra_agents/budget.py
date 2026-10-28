"""What the Supervisor's own initiative may spend a day: its review rounds and the wake-ups of its
duties (flow-keeping's stalls, quiet machines, divergences). The owner's chat, and what the fixed
core always wakes it for (escalations, a program given up on, a plan reported), never wait on it.

Spending is kept in the agent's state (``spend``: per day, dollars and turns, by what started
the turn), written after every Supervisor turn from what the harness reported (``TurnResult.cost_usd``:
Claude Code reports it; a harness that does not is counted in turns). The caps are the owner's:
``[agents] review_usd_per_day`` (default 2.0) and ``review_turns_per_day`` (default 48)."""

from __future__ import annotations

import time

from .store import AgentStore

MACHINE = "supervisor"
KEEP_DAYS = 14


def day(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t or time.time()))


def spent(store: AgentStore, t: float | None = None) -> dict:
    """Today's spending: ``{usd, turns, initiative_usd, initiative_turns}``."""
    return dict((store.agent_state(MACHINE).get("spend") or {}).get(day(t)) or {})


def record(store: AgentStore, cost_usd: float | None, initiative: bool, t: float | None = None) -> None:
    """One Supervisor turn ended: add what it cost to today's record (``initiative``: a review
    round's or a duty's wake-up, which the caps limit)."""
    st = store.agent_state(MACHINE)
    spend = dict(st.get("spend") or {})
    today = dict(spend.get(day(t)) or {})
    usd = float(cost_usd or 0.0)
    today["usd"] = round(today.get("usd", 0.0) + usd, 6)
    today["turns"] = today.get("turns", 0) + 1
    if cost_usd is None:
        today["turns_without_cost"] = today.get("turns_without_cost", 0) + 1
    if initiative:
        today["initiative_usd"] = round(today.get("initiative_usd", 0.0) + usd, 6)
        today["initiative_turns"] = today.get("initiative_turns", 0) + 1
    spend[day(t)] = today
    for k in sorted(spend)[:-KEEP_DAYS]:
        spend.pop(k)
    store.set_agent_state(MACHINE, spend=spend, spend_total=round(float(st.get("spend_total") or 0.0) + usd, 6))


def over(store: AgentStore, cfg, t: float | None = None) -> str | None:
    """Why the Supervisor's initiative must wait until tomorrow, or None."""
    s = spent(store, t)
    if s.get("initiative_usd", 0.0) >= cfg.review_usd_per_day > 0 or cfg.review_usd_per_day == 0:
        return (f"its review rounds and duty wake-ups spent ${s.get('initiative_usd', 0.0):.2f} today, the cap is "
                f"${cfg.review_usd_per_day:.2f} ([agents] review_usd_per_day)")
    if s.get("initiative_turns", 0) >= cfg.review_turns_per_day:
        return (f"its review rounds and duty wake-ups took {s.get('initiative_turns', 0)} turns today, the cap is "
                f"{cfg.review_turns_per_day} ([agents] review_turns_per_day)")
    return None
