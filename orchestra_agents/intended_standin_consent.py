"""The stand-in's consent and revision rules (``intended_standin.py``), ported from Orchestra's at
C1 fe185af0 (``orchestra/sd/intended/consent.py`` and ``revise.py``) so the design tools meet the
same rules before and after Orchestra's own is installed. Deleted with the stand-in.

- The owner **asks**: ``author: owner`` with the owner's words quoted.
- A call **answers the owner's request**: ``provenance.request`` names a request the stand-in
  noted (``StandIn.note_request``, as Orchestra's web server notes one) that no call has answered
  yet, about the same part and id, with the same control and the same ``change`` when it names
  one. A bare event on the bus from source ``owner`` is not a noted request.
- What the owner declared is changed or removed only with the owner's consent.
- A revision by the owner applies at once, except on a machine or attachment no longer proposed,
  where it waits as a pending change; one the call has the owner's consent for (it answers their
  request) applies at once. Where the owner's pending change and another's touch the same fields,
  the other is confirmed only when the owner's request names it."""

from __future__ import annotations

import copy

from .store import StoreError

ASK_THE_OWNER = "the owner declared this: ask the owner"


def at_once(prov: dict) -> bool:
    return prov.get("author") == "owner" and bool(prov.get("quote"))


owner_asks = at_once                          # the stand-in has no page: the owner asks only through a quote


def request(ctx: dict) -> dict | None:
    rid = ctx["provenance"].get("request")
    return (ctx.get("requests") or {}).get(rid) if rid else None


def verified(ctx: dict, part: str, eid, control: str, change: str | None = None, *, exact: bool = False) -> bool:
    r = request(ctx)
    if r is None or (r.get("part"), r.get("id"), r.get("control")) != (part, eid, control):
        return False
    return r.get("change") == change or (not exact and r.get("change") is None)


def may_settle(ctx: dict, part: str, eid, control: str, change: str | None = None) -> None:
    prov = ctx["provenance"]
    if prov.get("author") == "owner":
        return
    if not prov.get("request"):
        raise StoreError("a proposal is confirmed by the owner: give the owner's quote or the request's event id")
    if not verified(ctx, part, eid, control, change):
        raise StoreError(f"provenance.request {prov['request']} is not the owner's unanswered request to {control} {part} {eid}"
                         + (f" (change {change})" if change else ""))


def guard_owners(owned: dict | None, ctx: dict, part: str, eid, control: str = "discard") -> None:
    if (owned or {}).get("author") != "owner":
        return
    if owner_asks(ctx["provenance"]) or verified(ctx, part, eid, control):
        return
    raise StoreError(ASK_THE_OWNER)


def _apply(el: dict, set_: dict, add: dict, remove: dict) -> dict:
    added, removed, before = {}, {}, {}
    for k, v in set_.items():
        if k == "spec" and isinstance(el.get("spec"), dict):
            for sk in dict.fromkeys([*el["spec"], *v]):
                if el["spec"].get(sk) != v.get(sk):
                    before[sk] = copy.deepcopy(el["spec"].get(sk))
        elif el.get(k) != v:
            before[k] = copy.deepcopy(el.get(k))
        el[k] = v
    for k, items in add.items():
        cur = list(el.get(k) or [])
        if new := [x for x in items if x not in cur]:
            el[k] = cur + new
            added[k] = new
    for k, items in remove.items():
        cur = list(el.get(k) or [])
        if gone := [x for x in items if x in cur]:
            el[k] = [x for x in cur if x not in gone]
            removed[k] = gone
    return {"added": added, "removed": removed, "before": before}


def _guesses(el: dict, touched: set[str], guessed: list[str]) -> None:
    kept = [g for g in el.get("guessed") or [] if g.split("[", 1)[0] not in touched]
    el["guessed"] = kept + [g for g in guessed if g not in kept]


def revise(el: dict, set_: dict, add: dict, remove: dict, guessed: list[str], ctx: dict, *,
           waits_once_confirmed: bool = False, consent: bool = False) -> None:
    prov, cid = ctx["provenance"], ctx["change"]
    now = consent or (at_once(prov) and (not waits_once_confirmed or el.get("state") == "proposed"))
    if not now:
        el.setdefault("pending", []).append({"change": cid, "set": set_, "add": add, "remove": remove, "guessed": guessed,
                                             "provenance": prov})
        return
    diff = _apply(el, set_, add, remove)
    _guesses(el, set(set_) | set(add) | set(remove), guessed)
    el["latest"] = {"change": cid, "at": prov["at"], "quotes": [prov["quote"]] if prov.get("quote") else [],
                    **({"request": prov["request"]} if consent else {}), **diff}


def _fields(p: dict) -> set[str]:
    return set(p.get("set") or {}) | set(p.get("add") or {}) | set(p.get("remove") or {})


def overridden(pending: list[dict]) -> dict[str, str]:
    owners = [p for p in pending if (p.get("provenance") or {}).get("author") == "owner"]
    out = {}
    for p in pending:
        if (p.get("provenance") or {}).get("author") == "owner":
            continue
        if (by := next((o["change"] for o in owners if _fields(o) & _fields(p)), None)) is not None:
            out[p["change"]] = by
    return out


def settle_pending(el: dict, change: str, accept: bool, ctx: dict, part: str) -> None:
    pend = el.get("pending") or []
    p = next((x for x in pend if x["change"] == change), None)
    if p is None:
        raise StoreError(f"{el.get('id')} has no pending change {change}")
    by = overridden(pend).get(change)
    if accept and by is not None and not verified(ctx, part, el.get("id"), "confirm", change, exact=True):
        raise StoreError(f"{part} {el.get('id')}: the owner's pending change {by} touches the same fields as {change}; "
                         "it is confirmed only when the owner asks for it")
    el["pending"] = [x for x in pend if x is not p]
    if accept:
        diff = _apply(el, p["set"], p["add"], p["remove"])
        _guesses(el, set(p["set"]) | set(p["add"]) | set(p["remove"]), p.get("guessed") or [])
        el["latest"] = {"change": change, "at": ctx["provenance"]["at"], "confirmed_by": ctx["change"],
                        "quotes": [q for q in (p["provenance"].get("quote"), ctx["provenance"].get("quote")) if q], **diff}
