"""What is wrong now, the first thing the "since" digest says (``since_digest``): the open items the
Workflows plugin has (``item_state.read``) that look stuck, before anything the window's events say
(T2j s16: "Look first" named only the stale events replayed at a resume, and never SCRUM-47, 12
hours at ``sail_check`` with no SAIL result).

Four kinds, one line per item (the first kind that fits it), each said as what is so, never why:
- **an outside result that never came**: the item waits at a step on an outside system (the step's
  actor, or the wait's own ``by``) and has waited ``FACTOR`` times the step's typical time (at least
  ``FLOOR``), or ``STALE`` when the step has no typical time yet; with the last thing the window
  heard of it ("SAIL put on #51 at 19:59, nothing since");
- **attention raised, unanswered**: the item's wait is an attention the floor raised;
- **past its step's limit**: longer at its step than the step's ``within`` guard (the policy);
- **longer than its step usually takes**: ``FACTOR`` times the step's typical time, at least ``FLOOR``.

Each line names the item with its ticket and pull request, the step, how long, and the measure it
passed. The longest wait comes first."""

from __future__ import annotations

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


def stuck(d, state: dict | None) -> list[tuple[str, list[str]]]:
    """``[(what, [line])]`` for the kinds that have any, in the order above; [] without the plugin."""
    if not state:
        return []
    typical, within, outside = state.get("typical") or {}, state.get("within") or {}, state.get("outside") or {}
    kinds: dict[str, list[tuple[float, str]]] = {}
    for r in state.get("rows") or []:
        item, wf, step, pile = str(r.get("item") or "?"), r.get("workflow"), r.get("step"), r.get("pile")
        age = r.get("age_s")
        if not isinstance(age, int | float):
            continue
        wait = r.get("wait") if isinstance(r.get("wait"), dict) else {}
        usual = typical.get((wf, pile))
        acts = r.get("acts") if isinstance(r.get("acts"), dict) else {}
        system = (None if acts.get("who") else                 # a person acts (an approval): not an outside result
                  outside.get((wf, step)) or (wait.get("by") if wait.get("by") not in PEOPLE else None))
        where = f"{d.item(item)} at {step}"
        if str(wait.get("reason") or "").startswith("attention"):
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
    order = ("an outside result that never came", "attention raised, unanswered", "past its step's limit",
             "longer than its step usually takes")
    out = []
    for k in order:
        if k in kinds:
            rows = sorted(kinds[k], key=lambda x: -x[0])
            whys = {w for _, _, w in rows}
            if len(rows) > 1 and len(whys) == 1 and (w := whys.pop()):     # one cause for all: said once
                out.append((f"{k} ({w})", [line for _, line, _ in rows]))
            else:
                out.append((k, [line + (f" ({w})" if w else "") for _, line, w in rows]))
    return out
