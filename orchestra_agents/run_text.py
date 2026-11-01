"""What the modeling tools say back about a prediction and its verdict (modeling.py): the key
numbers first, a line an expectation or stock; the trajectories themselves only with ``raw``.
Nothing here reads or changes anything."""

from __future__ import annotations

from .model_text import dur, end, num, params, when

VERDICTS = {
    "passed": "what you predicted happened, and the stocks moved as the model said they would",
    "no effect": "a predicted edit did not appear: the change did not do what you thought, or did not take effect",
    "side effect": "something you did not predict changed, or a predicted flow changed by another amount",
    "model wrong elsewhere": "what you predicted happened, but a stock did not move as the model said: the model of the rest "
                             "of the system is wrong there",
    "interrupted": "another change began a new era before the horizon; what could be judged until then is partial",
    "undecided": "the new era still had no evidence of its own on a predicted flow at twice the horizon",
}


def expectation(x: dict) -> str:
    what = x.get("flow") or x.get("stock") or x.get("loop") or x.get("id") or f"{end(x.get('from'))} -> {end(x.get('to'))}"
    k = x.get("kind")
    head = {"factor": f"flow {what} ×{num(x.get('factor'))}" + (f" ({x['param']})" if x.get("param") else ""),
            "unchanged": f"flow {what} unchanged", "equation": f"flow {what} = {x.get('equation')}{params(x.get('params'))}",
            "appears": f"{x.get('part') or 'flow'} {what} appears", "disappears": f"{x.get('part') or 'flow'} {what} disappears",
            "level": f"stock {what} " + " ".join(f"{w} {num(x[w])}" for w in ("below", "above") if w in x)}.get(k, f"{k} {what}")
    if "status" not in x:
        return head
    out = f"{head}: {x['status']}"
    if x.get("law"):
        lw, old = x["law"], x.get("old_law") or {}
        out += (f" (your equation expected {num(lw.get('expected'))} events, {num(lw.get('events'))} were counted, z {num(lw.get('z'))}"
                + (f"; the old equation's z {num(old.get('z'))}" if old else "") + ")")
    for p in x.get("params") or []:
        if isinstance(p, dict) and "before" in p:
            out += f" ({p.get('param')} {num(p['before'])} -> {num(p.get('after'))}, z {num(p.get('z'))})"
    if x.get("reason"):
        out += f" ({x['reason']})"
    return out


def prediction_lines(p: dict) -> list[str]:
    out = [(f"prediction {p.get('id')} by {p.get('source')} for change {p.get('change')} (about {p.get('subject')}), made "
           f"{when(p.get('made_at'))}, horizon {dur(p.get('horizon_s'))}; era {p.get('era') or 'not begun yet'}: {p.get('status')}")]
    if p.get("status") == "invalid":
        return out + [f"  invalid: {p.get('why')}"]
    v = p.get("verdict") or {}
    if not v:
        out.append("  expects: " + ("nothing to change (the null test)" if not p.get("expect") else
                                    "; ".join(expectation(x) for x in p.get("expect") or [])))
        return out
    out.append(f"  VERDICT: {v.get('verdict')} — {VERDICTS.get(v.get('verdict'), '')}" + (" (made after its change began)" if v.get("late") else ""))
    out += [f"  {expectation(x)}" for x in v.get("expectations") or []]
    for u in v.get("unpredicted") or []:
        out.append("  unpredicted: " + ", ".join(f"{k} {num(val) if not isinstance(val, (dict, list)) else val}" for k, val in u.items()))
    rows = (v.get("stocks") or {}).get("rows") or []
    for s in rows:
        out.append(f"  stock {s.get('stock')}: measured {num(s.get('level'))} against predicted {num(s.get('central'))} "
                   f"(z {num(s.get('z'))}, {s.get('readings')} readings)" + (" OUTSIDE the band" if s.get("out") else ""))
    if v.get("partial"):
        out.append(f"  partial (until the next change): {v['partial'].get('verdict') if isinstance(v['partial'], dict) else v['partial']}")
    return out
