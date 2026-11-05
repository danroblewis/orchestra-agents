"""What wakes the Supervisor, in three kinds, and what its wake-ups cost today, for the Agents page
(``/supervisor/wakes``) and the spend record's ``by_reason`` (budget.py): **immediate** (never held,
never capped), **capped** (its rounds: they count against the day's cap, each on its own interval),
and **coalesced** (its notices, notices.py: capped too, and gathered into one turn at most every
``[agents] attention_every``). Each duty's events and its own bindings (bindings.py) come with their
count in the last hour, the busy ones marked (bus_rates.py), and one it unbound marked so."""

from __future__ import annotations

import re

from . import budget, notices
from .builtins import ROLES
from .bus_rates import busy, rates
from .events import INTENT_REQUESTED
from .explore_round import MARK as EXPLORE
from .machine_spec import unbinds
from .review import MARK as REVIEW

MACHINE = "supervisor"
ESCALATION = re.compile(r"^\[[^\]\s][^\]]*\] ")
IMMEDIATE = (
    {"what": f"{INTENT_REQUESTED} from owner", "why": "your click on a page (confirm, discard, the kind switch)"},
    {"what": "escalate_to_supervisor", "why": "another agent's escalation"},
    {"what": "a crash loop", "why": "an agent paused after repeated crashes"},
    {"what": "Orchestra's own", "why": "the first run's setup, a ticket made for you done, a prediction's verdict, a hand-off "
                                        "the Coordinator has not planned in time, a harness's notice: each once"},
)
CAPPED = (
    {"what": "the review round", "why": "its spec's review_every, only when the round's quick check finds something new"},
    {"what": "the explore round", "why": "[agents] explore_every, only when an experiment is due"},
)


def label(reason: str) -> str:
    """The kind of a wake-up's reason, as the spend record counts it."""
    if reason.startswith(REVIEW):
        return "review round"
    if reason.startswith(EXPLORE):
        return "explore round"
    if reason.startswith("event "):
        return reason.split()[1].rstrip(":")
    if reason.startswith("Orchestra notice:"):
        return "harness notice"
    if reason.startswith("agent ") and " crashed " in reason:
        return "crash loop"
    if reason.startswith(("scheduled check-in", "scheduled daily session")):
        return "schedule"
    if ESCALATION.match(reason):
        return "escalation"
    return "other"


def labels(reasons: list[str]) -> list[str]:
    return list(dict.fromkeys(label(r) for r in reasons))


def view(project, store, cfg, spec=None) -> dict:
    """The Supervisor's listening, its held notices and today's wake turns with their spend."""
    rate = rates(store)
    core = [w for w in ROLES[MACHINE].wake_on if w.split(":")[0] != INTENT_REQUESTED]
    unbound = spec.unbound if spec is not None else []

    def row(w: str, src: str) -> dict:
        r = {"what": w, "from": src, "per_hour": rate.get(w.split(":")[0], 0), "busy": busy(w, rate)}
        return {**r, "unbound": True} if unbinds(unbound, w) else r
    coalesced = [row(w, "the fixed core") for w in core]
    for d in (spec.duties if spec is not None else []):
        coalesced += [row(w, f"duty {d.name}") for w in d.listens]
    coalesced += [{**row(b.event, "its own binding"), "unbound": False} for b in (spec.bindings if spec is not None else [])
                  if b.event.split(":")[0] != INTENT_REQUESTED]
    st = store.agent_state(MACHINE)
    today = budget.spent(store)
    held = notices.held(store)
    return {"ok": True, "attention_every_s": float(getattr(cfg, "attention_every", 600.0) or 0.0),
            "immediate": list(IMMEDIATE), "capped": list(CAPPED), "coalesced": coalesced,
            "held": {"count": len(held), "times": sum(n.get("count", 1) for n in held), "why": st.get("notices_why"),
                     "dropped": int(st.get("notices_dropped") or 0),
                     "cap_spent": bool(budget.over(store, cfg)) and not notices.paused(store),
                     "newest": [n["reason"].splitlines()[0][:200] for n in held[-5:]]},
            "paused": notices.paused(store), "day": budget.day(),
            "today": {"turns": today.get("turns", 0), "usd": today.get("usd", 0.0),
                      "initiative_turns": today.get("initiative_turns", 0), "initiative_usd": today.get("initiative_usd", 0.0),
                      "chat_turns": today.get("chat_turns", 0), "chat_usd": today.get("chat_usd", 0.0),
                      "by_reason": today.get("by_reason") or {}},
            "caps": {"review_usd_per_day": cfg.review_usd_per_day, "review_turns_per_day": cfg.review_turns_per_day,
                     "chat_usd_per_day": cfg.chat_usd_per_day}}
