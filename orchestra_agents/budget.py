"""What the Supervisor may spend a day. Its own initiative: its review rounds, its explore rounds and
the wake-ups of its duties (flow-keeping's stalls, quiet machines, divergences), capped by
``[agents] review_usd_per_day`` and ``review_turns_per_day``; what the fixed core always wakes it for
(escalations, a program given up on, a plan reported) never waits on it. The owner's chat turns,
capped apart by ``chat_usd_per_day`` (default 10.0; 0: no cap): past it a message is refused before
any turn starts, with what was spent and when it resets (``chat_over``; the D5 finding, 2026-11-04:
chat turns were not capped at all).

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
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if t is None else t))


def spent(store: AgentStore, t: float | None = None) -> dict:
    """Today's spending: ``{usd, turns, initiative_usd, initiative_turns}``."""
    return dict((store.agent_state(MACHINE).get("spend") or {}).get(day(t)) or {})


def record(store: AgentStore, cost_usd: float | None, initiative: bool, t: float | None = None, chat: bool = False) -> None:
    """One Supervisor turn ended: add what it cost to today's record (``initiative``: a review
    round's or a duty's wake-up, which the caps limit; ``chat``: the owner's message)."""
    st = store.agent_state(MACHINE)
    spend = dict(st.get("spend") or {})
    today = dict(spend.get(day(t)) or {})
    usd = float(cost_usd or 0.0)
    today["usd"] = round(today.get("usd", 0.0) + usd, 6)
    today["turns"] = today.get("turns", 0) + 1
    if cost_usd is None:
        today["turns_without_cost"] = today.get("turns_without_cost", 0) + 1
    if chat:
        today["chat_usd"] = round(today.get("chat_usd", 0.0) + usd, 6)
        today["chat_turns"] = today.get("chat_turns", 0) + 1
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


def chat_over(store: AgentStore, cfg, t: float | None = None) -> str | None:
    """Why the owner's next chat message must wait until tomorrow (``chat_usd_per_day`` spent), or
    None. A harness that reports no cost is never stopped here (its turns are counted)."""
    cap = float(getattr(cfg, "chat_usd_per_day", 0.0) or 0.0)
    if cap <= 0:
        return None
    usd = float(spent(store, t).get("chat_usd", 0.0))
    if usd < cap:
        return None
    return (f"The Supervisor's chat turns spent ${usd:.2f} today ({day(t)}), and the cap is ${cap:.2f} a day "
            "([agents] chat_usd_per_day). Your message was not sent and nothing was spent on it. The cap resets at "
            "midnight, host time; to talk again sooner, raise chat_usd_per_day in your private overlay "
            "(0 removes the cap), and the next message goes through.")
