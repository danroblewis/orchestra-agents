"""What is wrong now, the first thing the "since" digest says (``since_digest``): the open items the
Workflows plugin has (``item_state.read``) that look stuck, before anything the window's events say
(T2j s16: "Look first" named only the stale events replayed at a resume, and never SCRUM-47, 12
hours at ``sail_check`` with no SAIL result).

Six kinds, one line per item (the first kind that fits it), each said as what is so, never why:
- **stalled**, first (T48): the item is at its workflow's ``stalled`` step (its tries spent, or a
  result that never came), or its tries are spent with no next try ("SCRUM-56 at stalled 3 h, from
  write, last: git push refused (publickey)");
- **in retry**: its turn at the step finished ``blocked`` or ``error`` and the floor tries it again
  (orchestra-workflows W16), with the tries, the next try and the last try's summary ("SCRUM-56 at
  write: 3 tries, next at 14:05, last: git push refused (publickey)"), so a cause the Supervisor can
  fix is said before the floor's hour-long back-off (``retry_step``, once it is fixed);
- **an outside result that never came**: the item waits at a step on an outside system (the step's
  actor, or the wait's own ``by``) and has waited ``FACTOR`` times the step's typical time (at least
  ``FLOOR``), or ``STALE`` when the step has no typical time yet; with the last thing the window
  heard of it ("SAIL put on #51 at 19:59, nothing since");
- **attention raised, unanswered**: the item's wait is an attention the floor raised;
- **past its step's limit**: longer at its step than the step's ``within`` guard (the policy);
- **longer than its step usually takes**: ``FACTOR`` times the step's typical time, at least ``FLOOR``.

Each line names the item with its ticket and pull request, the step, how long, and the measure it
passed. **The newest first** within each kind (the shortest wait: T2k s16 named the three
day-old attentions and hid the night's under "… and 1 more"); an item ``skip`` names (one a stopped
process blocks, said above it by ``since_dead``) is left out here."""

from __future__ import annotations

from datetime import datetime

from .floor import RETRIED, STALLED

FACTOR = 3.0
FLOOR = 1800.0          # never stuck under half an hour, whatever the step usually takes
STALE = 6 * 3600.0      # an outside wait with no typical time yet: stuck past this
PEOPLE = ("owner", "supervisor", "person", None, "")


def spoken(s: float) -> str:
    if s < 90:
        return f"{round(s)} s"
    if s < 5400:
        return f"{round(s / 60)} min"
    if s < 48 * 3600:
        h = s / 3600
        return f"{h:.0f} h" if h >= 10 else f"{h:.1f} h".replace(".0 h", " h")
    return f"{round(s / 86400)} days"


def _last_heard(d, item: str) -> str | None:
    """The last event the window has of the item, in words."""
    for t, e in reversed(d.events):
        if str(e.get("subject") or "") != item:
            continue
        ty = str(e.get("type") or "")
        if ty.startswith(("orchestra.", "offer.", "session.", "agent.")):
            continue
        from .event_tally import field
        if ty.endswith(".pr.labeled"):
            return f"{field(e, 'label') or '?'} put on #{field(e, 'number')} at {d.clock.at(t)}, nothing since"
        return f"{ty} at {d.clock.at(t)}, nothing since"
    return None


SUMMARY = 80            # a last try's summary, at most, in a line


def _clip(text) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= SUMMARY else t[:SUMMARY - 1].rstrip() + "…"


def _last_failure(d, item: str) -> str | None:
    """The summary of the item's last turn that finished ``blocked`` or ``error`` in the window."""
    from .event_tally import field
    for _, e in reversed(d.events):
        if e.get("type") == "offer.finished" and str(e.get("subject") or "") == item and field(e, "outcome") in RETRIED:
            return _clip(field(e, "summary")) or None
    return None


def _retrying(r: dict) -> dict | None:
    """The row's failed turn while the floor tries the step again (its ``retry``; ``last_try`` from a
    plugin whose rows carry no ``retry``), or None."""
    if r.get("held") or r.get("step") == STALLED:
        return None
    last = r.get("last_try") if isinstance(r.get("last_try"), dict) else {}
    retry = r.get("retry") if isinstance(r.get("retry"), dict) else None
    if retry is None and last.get("outcome") not in RETRIED:
        return None
    return {**last, **(retry or {})}


def _next_try(d, wait: dict) -> str:
    until = wait.get("until")
    try:
        ts = datetime.fromisoformat(str(until)).timestamp() if until else None
    except ValueError:
        ts = None
    if str(wait.get("reason") or "").startswith("offered"):
        return "offered again now"
    if ts is None:
        return "no next try (its tries are spent)"
    return "next try now" if ts <= d.clock.now else f"next at {d.clock.at(ts)}"


def stuck(d, state: dict | None, skip: set[str] | frozenset = frozenset()) -> list[tuple[str, list[str]]]:
    """``[(what, [line])]`` for the kinds that have any, in the order above; [] without the plugin."""
    if not state:
        return []
    typical, within, outside = state.get("typical") or {}, state.get("within") or {}, state.get("outside") or {}
    kinds: dict[str, list[tuple[float, str]]] = {}
    for r in state.get("rows") or []:
        item, wf, step, pile = str(r.get("item") or "?"), r.get("workflow"), r.get("step"), r.get("pile")
        age = r.get("age_s")
        if not isinstance(age, int | float) or item in skip:
            continue
        wait = r.get("wait") if isinstance(r.get("wait"), dict) else {}
        usual = typical.get((wf, pile))
        acts = r.get("acts") if isinstance(r.get("acts"), dict) else {}
        system = (None if acts.get("who") else                 # a person acts (an approval): not an outside result
                  outside.get((wf, step)) or (wait.get("by") if wait.get("by") not in PEOPLE else None))
        where = f"{d.item(item)} at {step}"
        retry = _retrying(r)
        if step == STALLED:
            frm = r.get("came_from")
            last = _last_failure(d, item)
            kinds.setdefault("stalled", []).append(
                (age, f"{where} {spoken(age)}" + (f", from {frm}" if frm else "") + (f", last: {last}" if last else ""), None))
        elif retry is not None:
            n = int(retry.get("failed") or r.get("attempts") or 1)
            nxt = _next_try(d, wait)
            last = _clip(retry.get("summary")) or _last_failure(d, item)
            line = f"{where}: {n} {'try' if n == 1 else 'tries'}, {nxt}" + (f", last: {last}" if last else "")
            kinds.setdefault("stalled" if nxt.startswith("no next") else "in retry", []).append((age, line, None))
        elif str(wait.get("reason") or "").startswith("attention"):
            kinds.setdefault("attention raised, unanswered", []).append((age, f"{where} {spoken(age)}", wait.get("why")))
        elif system and age > (max(FACTOR * usual, FLOOR) if usual else STALE):
            heard = _last_heard(d, item)
            kinds.setdefault("an outside result that never came", []).append(
                (age, f"{where} {spoken(age)}, waiting on {system}" +
                 (f" (usually {spoken(usual)})" if usual else "") + (f", last heard: {heard}" if heard else ""), None))
        elif (limit := within.get((wf, step))) and age > limit:
            kinds.setdefault("past its step's limit", []).append((age, f"{where} {spoken(age)} (its limit {spoken(limit)})", None))
        elif usual and age > max(FACTOR * usual, FLOOR):
            kinds.setdefault("longer than its step usually takes", []).append(
                (age, f"{where} {spoken(age)} (usually {spoken(usual)})", None))
    order = ("stalled", "in retry", "an outside result that never came", "attention raised, unanswered", "past its step's limit",
             "longer than its step usually takes")
    out = []
    for k in order:
        if k in kinds:
            rows = sorted(kinds[k], key=lambda x: x[0])                # the newest first
            whys = {w for _, _, w in rows}
            if len(rows) > 1 and len(whys) == 1 and (w := whys.pop()):     # one cause for all: said once
                out.append((f"{k} ({w})", [line for _, line, _ in rows]))
            else:
                out.append((k, [line + (f" ({w})" if w else "") for _, line, w in rows]))
    return out
