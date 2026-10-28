"""The Supervisor's overload signals, measured at each review round (the owner, 2026-10-28: "too
much" is a set of signals, not one threshold; docs/proposals/agent-first-machines.md §7.1).

The ones measurable now, each with the owner's threshold:

| signal | measured as | crosses at |
|---|---|---|
| prompt | its system prompt, in tokens (characters / 4) | over 45k |
| tools | the tools its groups give it | over 30 |
| compactions | its context compacted in the last day (``agent.compacted``) | 2 or more: regularly reaching compaction |
| busy, with a backlog | its share of the last day in turns, and wake-ups queued now | over 0.7, with wake-ups waiting (may mean scaling out, not splitting) |
| central on the event map | the share of the machines seen in a day whose events woke it; the event types it listens for | over half of four or more machines; over 20 types |
| head-of-line blocking | how long its wake-ups waited behind a running turn (p90, last day) | over 10 minutes |
| cost per handled event | today's dollars per turn, against the days before | over 1.5 times |
| quality as duties grow | the share of its judged predictions that did not pass, last 7 days against the 7 before | up by 0.2 or more |

The values are kept in its agent state (``load``) and served as series by the plugin's instrument
(``measures.py``: ``agent_load{machine, signal}``; AGENTS.md rule 7 of Orchestra: instruments
measure, machines never write metrics). Crossing is a reason to propose a split
(``split_proposal.py``), never to make one."""

from __future__ import annotations

import time
from statistics import quantiles

from orchestra.events import event_time, machine_and_instance

from .harness.tools import tools_for
from .org import event_matches

DAY = 86_400.0
THRESHOLDS = {"prompt_tokens": 45_000, "tools": 30, "compactions_day": 2, "utilisation_day": 0.7, "in_share": 0.5,
              "subscriptions": 20, "wait_p90_s": 600.0, "cost_per_turn_ratio": 1.5, "failed_share_rise": 0.2}
WHY = {"prompt_tokens": "load", "tools": "load", "compactions_day": "load", "utilisation_day": "load (or scale out)",
       "in_share": "coupling: central on the event map", "subscriptions": "coupling: subscribes to too much",
       "wait_p90_s": "head-of-line blocking", "cost_per_turn_ratio": "cost per handled event rising",
       "failed_share_rise": "quality worsening as duties grow"}


def measure(project, store, agent, groups: tuple[str, ...], prompt: str, evs: list[dict], now: float | None = None) -> dict:
    """Every signal's value now (None when it cannot be measured yet)."""
    now = now or time.time()
    name = agent.name
    day = [e for e in evs if now - (event_time(e) or 0) < DAY]
    st = store.agent_state(name)
    v: dict = {"prompt_tokens": len(prompt) // 4, "tools": len(tools_for(groups))}
    from .machine_spec import load as load_spec
    spec = load_spec(project, name)
    v["duties"] = len(spec.duties) if spec else 0
    v["duty_tokens"] = sum(len(spec.duty_text(d)) for d in spec.duties) // 4 if spec else 0
    v["compactions_day"] = sum(1 for e in day if e.get("type") == "agent.compacted" and e.get("agent") == name)
    v["utilisation_day"] = _utilisation(day, agent.session_id, now)
    v["wakeups_queued"] = sum(1 for w in store.runtime().get("wakeups") or [] if w.get("target") == name)
    machines = {machine_and_instance(e.get("source"))[0] for e in day} - {None, "orchestra-host", name}
    senders = {machine_and_instance(e.get("source"))[0] for e in day if event_matches(agent.wake_on, e)} - {None, name}
    v["machines_day"] = len(machines)
    v["in_degree"] = len(senders)
    v["in_share"] = round(len(senders) / len(machines), 3) if len(machines) >= 4 else None
    from .listening import listens_for
    v["subscriptions"] = len(listens_for(agent, project.config.work, groups))
    v["out_types_day"] = len({e.get("type") for e in day if machine_and_instance(e.get("source"))[0] == name})
    waits = [w for w in st.get("wake_waits") or [] if now - w[0] < DAY]
    v["wait_p90_s"] = round(quantiles([w[1] for w in waits], n=10)[-1], 1) if len(waits) >= 5 else None
    v.update(_cost(st, now))
    v["failed_share_rise"] = _quality(st, evs, now)
    return v


def _utilisation(day: list[dict], session: str, now: float) -> float | None:
    started, busy, first = None, 0.0, None
    for e in day:
        if e.get("session") != session:
            continue
        t = event_time(e) or 0.0
        first = t if first is None else first
        if e.get("type") == "session.started":
            started = t
        elif e.get("type") == "session.settled" and started is not None:
            busy, started = busy + max(0.0, t - started), None
    if started is not None:
        busy += max(0.0, now - started)
    if first is None:
        return None
    return round(busy / max(3600.0, now - first), 3)


def _cost(st: dict, now: float) -> dict:
    spend = st.get("spend") or {}
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    per_turn = {d: s.get("usd", 0.0) / s["turns"] for d, s in spend.items() if s.get("turns")}
    out = {"usd_today": round((spend.get(today) or {}).get("usd", 0.0), 4), "turns_today": (spend.get(today) or {}).get("turns", 0),
           "cost_per_turn": round(per_turn[today], 4) if today in per_turn else None}
    before = [x for d, x in per_turn.items() if d != today and x > 0]
    out["cost_per_turn_ratio"] = round(per_turn[today] / (sum(before) / len(before)), 2) if before and per_turn.get(today) else None
    return out


def _quality(st: dict, evs: list[dict], now: float) -> float | None:
    mine = {p.get("id") for p in st.get("predictions") or [] if p.get("id")}
    recent, prior = [], []
    for e in evs:
        if e.get("type") != "orchestra.prediction.judged" or e.get("prediction") not in mine:
            continue
        age = now - (event_time(e) or 0)
        (recent if age < 7 * DAY else prior if age < 14 * DAY else []).append(e.get("verdict") != "passed")
    if len(recent) < 3 or len(prior) < 3:
        return None
    return round(sum(recent) / len(recent) - sum(prior) / len(prior), 3)


def crossed(values: dict) -> list[dict]:
    """The signals past their thresholds: ``[{signal, value, threshold, why}]``. Busy all day
    counts only with wake-ups waiting."""
    out = []
    for k, limit in THRESHOLDS.items():
        x = values.get(k)
        if x is None:
            continue
        hit = x >= limit if k == "compactions_day" else x > limit
        if k == "utilisation_day":
            hit = hit and (values.get("wakeups_queued") or 0) > 0
        if hit:
            out.append({"signal": k, "value": x, "threshold": limit, "why": WHY[k]})
    return out
