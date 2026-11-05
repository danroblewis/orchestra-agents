"""The Supervisor's notices: the events it is woken for that are not the owner's, held and gathered
into one wake-up turn at most every ``[agents] attention_every`` (default 10m), each counted against
its own initiative's day cap (``budget.over``). The owner's report, 2026-11-04: with Jira tickets as
Workflows items and roles nobody fills, the floor's ``attention.needed`` for every guard woke the
Supervisor without a cap, and the owner's chat waited behind those turns.

What wakes it at once, never held: the owner's page request (``orchestra.intent.requested`` from
``owner``), an agent's ``escalate_to_supervisor``, a crash loop pausing an agent (those two are
queued directly, never through here). Every other event in its ``wake_on`` (the fixed core's
attention events, ``orchestra.process.gave_up``, a plan reported, and every event a duty of its
spec listens for) is a notice: ``hold`` keeps it in the Supervisor's agent state (``notices``),
the same item and guard once, with how many times it came; ``release`` turns them into wake-ups
when the interval has passed, no turn of the Supervisor's is running, no message of the owner's is
waiting, and the day's cap is not spent nor the owner's pause on. Held past the cap, they are not
dropped: the Agents page shows how many wait, and the next chat turn is handed them as a short
digest (``take_digest``), which clears them.

The owner's pause (``pause``, the Agents page's "Pause the Supervisor's own initiative until
tomorrow", the plugin's own operator control): its review rounds, explore rounds and notices wait
until the day ends (``budget.over`` says so); chat and the immediate wake-ups go on. Recorded with
its provenance (``initiative_pause``: the day, when, by whom, from where) and kept in a short
history (``initiative_pauses``)."""

from __future__ import annotations

import time

from . import budget
from .store import AgentStore

MACHINE = "supervisor"
KEEP = 200                  # distinct notices held; past it the oldest go, counted in ``notices_dropped``
DIGEST_LINES = 12           # a chat turn's digest says this many, then how many more
REASON_MAX = 400
KEY_FIELDS = ("subject", "to", "workflow", "step", "guard", "name", "ticket", "probe", "request")


# Said in the next chat turn's digest, never a turn of their own (the owner, 2026-11-04: "The Supervisor
# keeps wasting tokens on this quiet reminder"): a machine gone quiet is news, not work.
DIGEST_ONLY = frozenset({"orchestra.machine.quiet"})


def key_of(ev: dict) -> str:
    """The same item and guard (or process, ticket, request) once: the event's type and the fields
    that name what it is about, never its time or id."""
    return "|".join([str(ev.get("type") or "")] + [f"{k}={ev[k]}" for k in KEY_FIELDS if ev.get(k) not in (None, "")])


def hold(store: AgentStore, ev: dict, reason: str, now: float | None = None) -> None:
    """One notice held: a new one appended, one already held counted again (its newest wording kept)."""
    now = time.time() if now is None else now
    key = key_of(ev)
    dropped = 0

    def fn(held):
        nonlocal dropped
        held = list(held or [])
        for n in held:
            if n["key"] == key:
                n.update(count=n.get("count", 1) + 1, last=now, reason=reason[:REASON_MAX * 4])
                return held
        held.append({"key": key, "type": ev.get("type"), "reason": reason[:REASON_MAX * 4], "first": now, "last": now, "count": 1,
                     **({"digest": True} if ev.get("type") in DIGEST_ONLY else {})})
        if len(held) > KEEP:
            dropped = len(held) - KEEP
            held = held[-KEEP:]
        return held
    store.update_agent_state(MACHINE, "notices", fn)
    if dropped:
        store.update_agent_state(MACHINE, "notices_dropped", lambda n: int(n or 0) + dropped)


def held(store: AgentStore) -> list[dict]:
    return list(store.agent_state(MACHINE).get("notices") or [])


def wording(n: dict) -> str:
    """A held notice as a wake-up reason: its reason, and how often it came when more than once.
    The reason keeps its ``event <type>`` start, so ``review.initiative`` knows it."""
    times = n.get("count", 1)
    return n["reason"] + (f"\n(said {times} times since {time.strftime('%H:%M', time.localtime(n['first']))}; the newest wording above)"
                          if times > 1 else "")


def release(store: AgentStore, cfg, why_not: str | None, busy: bool, owner_waiting: bool, now: float | None = None) -> int:
    """The held notices queued as wake-ups, all at once, when their turn may run: ``why_not``
    (``budget.over``: the cap spent, the owner's pause) is None, no Supervisor turn runs (``busy``),
    no owner's message waits, and ``attention_every`` has passed since the last release. How many
    were queued. The reason they wait is kept (``notices_why``) for the Agents page."""
    now = time.time() if now is None else now
    st = store.agent_state(MACHINE)
    notes = [n for n in st.get("notices") or [] if not n.get("digest")]
    if not notes:
        return 0
    every = float(getattr(cfg, "attention_every", 600.0) or 0.0)
    wait = None if why_not else ("a message of the owner's goes first" if owner_waiting else
                                 "a turn of the Supervisor's is running" if busy else
                                 None if now - float(st.get("notice_turn_at") or 0.0) >= every else
                                 f"gathered for one turn every {int(every // 60) or 1}m ([agents] attention_every)")
    said = why_not or wait
    if said:
        if st.get("notices_why") != said:
            store.set_agent_state(MACHINE, notices_why=said)
        return 0
    taken: list[dict] = []

    def take(cur):
        taken.extend(n for n in cur or [] if not n.get("digest"))
        return [n for n in cur or [] if n.get("digest")]          # digest-only notices wait for the next chat
    store.update_agent_state(MACHINE, "notices", take)
    for n in taken:
        store.add_wakeup(MACHINE, wording(n))
    store.set_agent_state(MACHINE, notice_turn_at=now, notices_why=None)
    return len(taken)


def take_digest(store: AgentStore) -> str:
    """The held notices as a few lines for a chat turn, and cleared: the owner is talking to it
    now, so what waited is said once there instead of in a turn of its own. Empty when none."""
    taken: list[dict] = []

    def take(cur):
        taken.extend(cur or [])
        return []
    if not held(store):
        return ""
    store.update_agent_state(MACHINE, "notices", take)
    if not taken:
        return ""
    why = store.agent_state(MACHINE).get("notices_why")
    store.set_agent_state(MACHINE, notices_why=None)
    lines = [f"- {n['reason'].splitlines()[0][:REASON_MAX]}" + (f" (x{n['count']})" if n.get("count", 1) > 1 else "")
             for n in taken[:DIGEST_LINES]]
    more = f"\n- and {len(taken) - DIGEST_LINES} more" if len(taken) > DIGEST_LINES else ""
    return (f"[Orchestra: {len(taken)} notice(s) held for you" + (f" ({why})" if why else "") +
            "; they had no turn of their own. Mention what matters to the owner in a line; act only on what they ask]\n"
            + "\n".join(lines) + more + "\n\n")


def pause(store: AgentStore, by: str = "owner", via: str = "the Agents page", t: float | None = None) -> dict:
    """The owner's pause of the Supervisor's own initiative until the day ends (host time)."""
    t = time.time() if t is None else t
    rec = {"day": budget.day(t), "at": t, "by": by, "via": via}
    store.set_agent_state(MACHINE, initiative_pause=rec)
    store.update_agent_state(MACHINE, "initiative_pauses", lambda h: (list(h or []) + [{**rec, "act": "pause"}])[-20:])
    return rec


def resume(store: AgentStore, by: str = "owner", via: str = "the Agents page", t: float | None = None) -> None:
    t = time.time() if t is None else t
    store.set_agent_state(MACHINE, initiative_pause=None)
    store.update_agent_state(MACHINE, "initiative_pauses",
                             lambda h: (list(h or []) + [{"day": budget.day(t), "at": t, "by": by, "via": via, "act": "resume"}])[-20:])


def paused(store: AgentStore, t: float | None = None) -> dict | None:
    """The owner's pause, when it holds today; None once the day it was made has ended."""
    rec = store.agent_state(MACHINE).get("initiative_pause")
    return rec if isinstance(rec, dict) and rec.get("day") == budget.day(t) else None
