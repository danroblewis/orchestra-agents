"""What the "since" digest says first (``since_digest``): the window's events that look wrong, before
any count, so the owner sees them at a glance (R3 b: three readings of the day's board flagged
nothing, though two Done tickets had new pull requests and a merged one was labelled REJECTED).

Four kinds, each from the events alone and said as what happened, never why:
- new work on a ticket already Done: a pull request opened, or an item requested or offered, for a
  ticket after an event said it was Done (its transition, or a ``status`` of Done on any of its
  events: a poller's replay of a closed ticket);
- a label put on a pull request more than a minute after it merged (the label and the merge that
  one check caused arrive seconds apart, and are not this);
- attention raised more than an hour ago on an item not closed since;
- an item still open whose pull request was closed unmerged (``*.pr.closed``)."""

from __future__ import annotations

from .event_tally import DONE, field

HOUR, GRACE = 3600.0, 60.0
WORK = (".pr.opened", "item.requested", "offer.made")


def _ty(e: dict) -> str:
    return str(e.get("type") or "")


def _done(e: dict) -> bool:
    ty = _ty(e)
    if ".issue." not in ty:
        return False
    return ty.endswith((".issue.closed", ".issue.resolved")) or any(
        str(field(e, k) or "").lower() in DONE for k in ("to", "status"))


def _work(d, xs: list) -> str:
    """``#33 at 04:16, a request at 05:38 and 3 offers``: what was started, the offers only counted."""
    named = [f"#{field(e, 'number')} opened at {d.clock.at(t)}" if ty.endswith(".pr.opened") else
             f"requested at {d.clock.at(t)}" for ty, t, e in xs if ty != "offer.made"]
    offers = sum(1 for ty, _, _ in xs if ty == "offer.made")
    if offers:
        named.append("1 offer" if offers == 1 else f"{offers} offers")
    return ", ".join(named)


def anomalies(d, now: float) -> list[tuple[str, list[str]]]:
    """``[(what, [one line per case])]`` for the kinds that have any, in the order above. ``d`` is
    the digest's ``Day`` (the window's events in time order, with each item's ticket and PR)."""
    done_at: dict[str, float] = {}
    merged_at: dict[int, float] = {}
    closed_pr: dict[int, float] = {}
    attention: dict[str, float] = {}
    closed_item: set[str] = set()
    after_done: dict[str, list[str]] = {}
    late_labels: list[str] = []
    for t, e in d.events:
        ty, subject = _ty(e), str(e.get("subject") or "")
        n = field(e, "number")
        if _done(e) and subject:
            done_at.setdefault(subject, t)
        elif ty.endswith(WORK) and subject:
            key = d.key_of(subject)
            if key and key in done_at and t > done_at[key]:
                after_done.setdefault(key, []).append((ty, t, e))
        if ty.endswith(".pr.merged") and isinstance(n, int):
            merged_at.setdefault(n, t)
        elif ty.endswith(".pr.closed") and isinstance(n, int) and not field(e, "merged"):
            closed_pr.setdefault(n, t)
        elif ty.endswith(".pr.labeled") and isinstance(n, int) and n in merged_at and t - merged_at[n] > GRACE:
            late_labels.append(f"{field(e, 'label') or '?'} on {d.pull(e)} at {d.clock.at(t)}, merged at "
                               f"{d.clock.at(merged_at[n])}")
        if ty == "attention.needed" and subject and subject not in attention:
            attention[subject] = t
        elif ty == "item.closed" and subject:
            closed_item.add(subject)
            attention.pop(subject, None)
    out: list[tuple[str, list[str]]] = []
    if after_done:
        out.append(("new work on a ticket already Done", [
            f"{k} (Done at {d.clock.at(done_at[k])}): {_work(d, xs)}" for k, xs in after_done.items()]))
    if late_labels:
        out.append(("a label on a merged pull request", late_labels))
    old = [f"{d.item(i)} since {d.clock.at(t)}" for i, t in attention.items() if now - t > HOUR]
    if old:
        out.append(("attention over an hour old, the item open", old))
    waiting = [f"{d.item(i)} on #{n} (closed at {d.clock.at(closed_pr[n])})" for i, n in d.pr.items()
               if n in closed_pr and n not in merged_at and i not in closed_item]
    if waiting:
        out.append(("an open item whose pull request closed unmerged", waiting))
    return out
