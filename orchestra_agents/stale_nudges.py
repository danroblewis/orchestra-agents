"""A nudge about a pull request that is done is withdrawn before it is offered (T2k s16: the
"since" digest offered an Outbox draft reminding #51's reviewer about "1200 minutes for SAIL's
verdict" for approval, an hour after #51 merged; approving it would have posted a wrong reminder).

A draft is a **nudge** when its kind says it reminds (``nudge``, ``remind`` in its last part:
``live.nudge``) and it is **about a pull request** when its target names one (``number``, ``pr`` or
``pull``, or a ``/pull/<n>`` link, with the repository when it gives one). The request's state is
read from the bus (``pr_states``: the newest ``*.pr.merged``, ``*.pr.closed`` or ``*.pr.reopened``
for that number, and repository when both say one); a merged or closed one is withdrawn: taken out
of what waits on the owner (``owner_waits``) and of ``read_outbox``'s waiting drafts, and said apart
with its state and time. The Outbox itself has no way for a drafter to withdraw a draft (rejecting
is the owner's, on its page), so the draft stays there until the owner rejects it; the digest says
so. Nothing is written here."""

from __future__ import annotations

import re

from .event_tally import field

NUDGE = ("nudge", "remind")
LINK = re.compile(r"/pull/(\d+)")


def is_nudge(kind) -> bool:
    last = str(kind or "").rsplit(".", 1)[-1].lower()
    return any(w in last for w in NUDGE)


def about(draft: dict) -> tuple[str | None, int] | None:
    """``(repository or None, number)`` of the pull request a draft's target names, or None."""
    t = draft.get("target")
    if not isinstance(t, dict):
        return None
    repo = t.get("repo") or t.get("repository")
    for k in ("number", "pr", "pull"):
        v = t.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            return (str(repo) if repo else None), v
        if isinstance(v, str) and (m := LINK.search(v) or re.fullmatch(r"#?(\d+)", v.strip())):
            return (str(repo) if repo else None), int(m.group(1))
    return None


def _ts(e: dict) -> float | None:
    from orchestra.events import event_time
    try:
        return event_time(e)
    except Exception:  # noqa: BLE001 - an event without a readable time is not used
        return None


def pr_states(events: list[dict]) -> dict[int, list[tuple[str | None, str, float | None]]]:
    """``number -> [(repository, merged | closed | reopened, time)]`` from the events, in order."""
    out: dict[int, list] = {}
    for e in events:
        ty = str(e.get("type") or "")
        verb = ty.rsplit(".", 1)[-1]
        if ".pr." not in ty or verb not in ("merged", "closed", "reopened"):
            continue
        n = field(e, "number")
        if not isinstance(n, int):
            continue
        state = "merged" if verb == "merged" or (verb == "closed" and field(e, "merged") is True) else verb
        repo = field(e, "repository") or field(e, "repo")
        out.setdefault(n, []).append((str(repo) if repo else None, state, _ts(e)))
    return out


def state_of(states: dict, repo: str | None, n: int) -> tuple[str, float | None] | None:
    """The pull request's last state on the bus (merged or closed), or None when it is open or unknown."""
    rows = [(s, t) for r, s, t in states.get(n) or [] if not (repo and r and r != repo)]
    if not rows:
        return None
    merged = [(s, t) for s, t in rows if s == "merged"]
    last = rows[-1]
    if merged:
        return merged[0]                               # a merge is final, whatever the close that came with it
    return last if last[0] == "closed" else None


def split(drafts: list[dict], events: list[dict]) -> tuple[list[dict], list[dict]]:
    """``(still waiting, withdrawn)``: each withdrawn draft with the pull request's number, state and time."""
    states = pr_states(events)
    keep, gone = [], []
    for d in drafts:
        pr = about(d) if isinstance(d, dict) and is_nudge(d.get("kind")) else None
        st = state_of(states, *pr) if pr else None
        if st:
            gone.append({"id": d.get("id"), "kind": d.get("kind"), "number": pr[1], "state": st[0], "at": st[1]})
        else:
            keep.append(d)
    return keep, gone


def withdraw_stale(waiting: dict, events: list[dict]) -> dict:
    """What waits on the owner with its stale nudges withdrawn (``withdrawn``); a part not read stays."""
    if not isinstance(waiting, dict) or not isinstance(waiting.get("outbox"), list):
        return waiting
    keep, gone = split(waiting["outbox"], events)
    return {**waiting, "outbox": keep, **({"withdrawn": gone} if gone else {})}
