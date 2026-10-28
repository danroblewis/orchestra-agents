"""What the modeling tools say back about a run of an experiment (forward, replay, inverse) and
about a prediction and its verdict (modeling.py): the key numbers first, a line a stock or flow;
the trajectories themselves only with ``raw``. Nothing here reads or changes anything."""

from __future__ import annotations

from .model_text import dur, end, num, params, when

BAND = "10th–90th percentile"
SIDE_SHOWN = 6           # side effects listed by an inverse run, the largest first
VERDICTS = {
    "passed": "what you predicted happened, and the stocks moved as the model said they would",
    "no effect": "a predicted edit did not appear: the change did not do what you thought, or did not take effect",
    "side effect": "something you did not predict changed, or a predicted flow changed by another amount",
    "model wrong elsewhere": "what you predicted happened, but a stock did not move as the model said: the model of the rest "
                             "of the system is wrong there",
    "interrupted": "another change began a new era before the horizon; what could be judged until then is partial",
    "undecided": "the new era still had no evidence of its own on a predicted flow at twice the horizon",
}


def _at(xs: list | None, i: int):
    return xs[i] if xs else None


def _band_end(b: dict | None) -> tuple:
    if not b:
        return None, None, None, None
    return _at(b.get("central"), 0), _at(b.get("central"), -1), _at(b.get("lo"), -1), _at(b.get("hi"), -1)


def forward_lines(run: dict) -> list[str]:
    r = run["result"]
    h = dur(r.get("horizon_s"))
    band = r.get("band") or {}
    out = [(f"forward run {run.get('id')}: from now ({when(r.get('now'))}) to +{h}; each stock as the central run, and its "
           f"band ({BAND} of {band.get('members')} runs with every rate perturbed by its error)"),
           "TRUSTED" if r.get("trusted") else "NOT TRUSTED: read the reasons on each stock before believing the numbers"]
    why = {x["id"]: x["why"] for x in r.get("not_trusted") or [] if x.get("kind") == "stock"}
    t = r.get("t") or []
    for sid, s in (r.get("stocks") or {}).items():
        b0, b1, _, _ = _band_end(s.get("base"))
        e0, e1, elo, ehi = _band_end(s.get("experiment"))
        parts = []
        if s.get("base"):
            parts.append(f"base {num(b0)} -> {num(b1)}")
        if s.get("experiment"):
            parts.append(f"experiment {num(e0)} -> {num(e1)} by +{h} (band {num(elo)}–{num(ehi)})")
        d = s.get("difference")
        if d and max(abs(v) for v in (d.get("central") or [0]) + (d.get("lo") or [0]) + (d.get("hi") or [0])) < 1e-9:
            parts.append("no difference")
        elif d:
            c = d.get("central") or [0]
            k = max(range(len(c)), key=lambda i: abs(c[i]))
            parts.append(f"difference {num(c[-1])} at +{h} (band {num(_at(d.get('lo'), -1))}–{num(_at(d.get('hi'), -1))})"
                         + (f", largest {num(c[k])} at +{dur(t[k])}" if t and abs(c[k]) > abs(c[-1]) * 1.05 else ""))
        line = f"  {sid}: " + "; ".join(parts)
        if why.get(sid):
            line += "; not trusted: " + "; ".join(why[sid])
        out.append(line)
    for x in r.get("limits") or []:
        if x.get("at_s") is None and not x.get("members"):
            continue
        what = {"floor": "reaches 0", "below bound": f"falls below {num(x.get('bound'))}",
                "above bound": f"rises above {num(x.get('bound'))}"}.get(x["kind"], x["kind"])
        at = f"at +{dur(x['at_s'])}" if x.get("at_s") is not None else "not in the central run"
        out.append(f"  limit: {x['stock']} {what} in the {x['run']} {at} ({round(100 * x.get('members', 0))}% of the band's runs)"
                   + (" — new: the base does not" if x.get("new") else ""))
    for fid, rn in (r.get("rates_now") or {}).items():
        seen = rn.get("observed")
        out.append(f"  changed flow {fid} now: base {num(rn.get('base'))}, experiment {num(rn.get('experiment'))} per "
                   f"{r.get('time_unit')}" + (f" (measured {num(seen[0])}–{num(seen[1])})" if seen else " (never measured)"))
    weak = [x for x in r.get("not_trusted") or [] if x.get("kind") == "flow"]
    if weak:
        out.append("flows not trusted: " + "; ".join(f"{x['id']}: {', '.join(x['why'])}" for x in weak))
    bad = {k: v for k, v in (r.get("nonfinite") or {}).items() if v}
    if bad:
        out.append(f"rates that were not numbers (taken as 0): {bad}")
    return out


def _err(row: dict, k: str) -> str:
    return num((row.get("error") or {}).get(k))


def replay_lines(run: dict) -> list[str]:
    r = run["result"]
    s = r.get("summary") or {}
    st, fl = s.get("stocks") or {}, s.get("flows") or {}
    out = [(f"replay {run.get('id')} of {when(r.get('start'))} to {when(r.get('end'))} ({dur(r.get('end', 0) - r.get('start', 0))}), "
           f"both models run free from the levels measured at its start, on its measured inputs"),
           (f"experiment against base: stocks {st.get('better', 0)} better, {st.get('same', 0)} same, {st.get('worse', 0)} worse; "
           f"counted flows {fl.get('better', 0)} better, {fl.get('same', 0)} same, {fl.get('worse', 0)} worse")]
    worse, better = [], []
    out.append("stocks (error: how far the run misses the measured level, in the level's own spread; 0 is perfect, 1 as far "
               "off as it varies):")
    for sid, row in (r.get("stocks") or {}).items():
        m = row.get("measured") or []
        ends = [f"measured {num(m[0][1])} -> {num(m[-1][1])}"] if m else []
        ends += [f"{k} run -> {num(row[k][-1])}" for k in ("base", "experiment") if row.get(k)]
        v = row.get("versus_base")
        errs = f"error base {_err(row, 'base')}, experiment {_err(row, 'experiment')}" if m else "not measured, nothing to compare"
        out.append(f"  {sid}: {errs}" + (f" ({v})" if v else "") + "; " + ", ".join(ends))
        (worse if v == "worse" else better if v == "better" else []).append(f"stock {sid}")
    out.append("counted flows (d2: the share of a constant rate's misfit the equation explains on the measured levels; "
               "1 is perfect, 0 no better than the mean rate, below 0 worse; moved: the total over the window):")
    for fid, row in (r.get("flows") or {}).items():
        bits = []
        for k in ("base", "experiment"):
            one = (row.get(k) or {}).get("one_step") or {}
            if row.get(k) is None:
                bits.append(f"{k}: none")
            elif one:
                bits.append(f"{k} d2 {num(one.get('d2'))}, moved {num(row[k].get('total'))}")
            else:
                bits.append(f"{k} moved {num(row[k].get('total'))}")
        v = row.get("versus_base")
        counted = f"counted {num(row['measured_total'])}" if row.get("measured_total") is not None else "not counted"
        out.append(f"  {fid}: {counted}; " + "; ".join(bits) + (f" ({v})" if v else ""))
        (worse if v == "worse" else better if v == "better" else []).append(f"flow {fid}")
    if worse:
        out.append("the experiment explains this window WORSE than the base on: " + ", ".join(worse))
    if better:
        out.append("the experiment explains it better on: " + ", ".join(better))
    if not worse and not better:
        out.append("the experiment explains this window as well as the base")
    return out


def _change(c: dict) -> str:
    if c.get("kind") == "new_flow":
        return f"new flow {c['flow']} ({end(c.get('from'))} -> {end(c.get('to'))}) = {c.get('equation')}{params(c.get('params'))}"
    if c.get("kind") == "rate":
        return f"flow {c['flow']}: its whole rate ×{num(c.get('factor'))}"
    return f"flow {c['flow']}: {c.get('param')} {num(c.get('base'))} -> {num(c.get('value'))} (×{num(c.get('factor'))})"


def inverse_lines(run: dict) -> list[str]:
    r = run["result"]
    w = r.get("window") or [None, None]
    out = [f"inverse run {run.get('id')} over {when(w[0])} to {when(w[1])}: the targets were "
           + ("REACHED" if r.get("reached") else "NOT reached (this is the best it found)")
           + f" with {r.get('count')} change(s)" + (f"; stopped at the {r['stopped']}" if r.get("stopped") else "")]
    out += [f"  change: {_change(c)}" for c in r.get("changes") or []]
    for t in r.get("targets") or []:
        if t.get("kind") == "hold":
            out.append(f"  held: {t['part']} {t['id']}" + ("" if t.get("met") else " (it moved: not held)"))
            continue
        out.append(f"  target {t['part']} {t['id']} {t['kind']} {num(t.get('value'))}: goal {num(t.get('goal'))}, solved "
                   f"{num(t.get('solved'))} (base replay {num(t.get('base_replay'))}, measured {num(t.get('measured'))}): "
                   + ("met" if t.get("met") else "MISSED"))
    side = r.get("side_effects") or []
    if side:
        out.append("side effects (what else would have moved; hold what must not):")
        out += [f"  {x['part']} {x['id']} {x.get('what')}: {num(x.get('base_replay'))} -> {num(x.get('solved'))}"
                + (f" (×{num(x['ratio'])})" if x.get("ratio") else "") for x in side[:SIDE_SHOWN]]
        if len(side) > SIDE_SHOWN:
            out.append(f"  ... and {len(side) - SIDE_SHOWN} more")
    if r.get("ambiguous"):
        out.append("AMBIGUOUS: treat this answer as one possibility, not the cause:")
        for a in r.get("alternatives") or []:
            if a.get("met"):
                out.append("  another change meets the targets as well: " + "; ".join(_change(c) for c in a.get("changes") or []))
        u = r.get("uniqueness") or {}
        if u.get("checked") and not u.get("consistent"):
            out.append(f"  the data the solved model would make, discovered again, shows changes on {', '.join(u.get('recovered') or []) or 'nothing'}"
                       + (f", and also on {', '.join(u['extra'])}" if u.get("extra") else "")
                       + (f", but not on {', '.join(u['missed'])}" if u.get("missed") else ""))
    elif (r.get("uniqueness") or {}).get("checked"):
        out.append("not ambiguous: no other change does as well, and the data it would make shows the same change")
    if r.get("experiment"):
        out.append(f"the solution is a new draft of yours: experiment {r['experiment']} (run_forward it, or commit it)")
    return out


RUN_LINES = {"forward": forward_lines, "replay": replay_lines, "inverse": inverse_lines}


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
