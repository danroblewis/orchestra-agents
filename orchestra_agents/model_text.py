"""What the modeling tools say back about the model, the eras, observability and series
(modeling.py; the predictions and their verdicts are ``run_text``): Orchestra's answers
(``orchestra.sd.reads``) as short lines with the key numbers first, so a model reads them
without wading through JSON. Orchestra's own answer is given whole only when the agent asks
(``raw``). Every function takes Orchestra's JSON as it is and returns a list of lines; nothing
here reads or changes anything."""

from __future__ import annotations

import time

FLOWS_SHOWN = 40         # flows listed per model, the rest counted


def num(x) -> str:
    if x is None:
        return "?"
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return str(x)
    if x == int(x) and abs(x) < 1e7:
        return str(int(x))
    return f"{x:.4g}"


def dur(s) -> str:
    """Seconds as 45s, 30m, 2h, 1h30m, 3d."""
    if s is None:
        return "?"
    s = round(float(s))
    if s < 120 and s % 60:
        return f"{s}s"
    m = s // 60
    if m < 120 and m % 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}h" + (f"{m}m" if m else "")
    d, h = divmod(h, 24)
    return f"{d}d" + (f"{h}h" if h else "")


def when(t) -> str:
    return "?" if t is None else time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(float(t)))


def params(p: dict | None) -> str:
    return f" [{', '.join(f'{k}={num(v)}' for k, v in p.items())}]" if p else ""


def end(x) -> str:
    return x if x else "outside"


def rate_text(f: dict) -> str:
    eq = f.get("equation")
    return (eq if eq is not None else "(cannot be written in the language)") + params(f.get("params"))


def label_line(model: dict) -> str:
    lab = model.get("label") or {}
    if lab.get("version") is not None and "latest_discovery_at" not in lab:
        which = f"saved version {lab['version']}"
    elif lab.get("latest_discovery_at"):
        which = f"the latest discovery, at {when(lab['latest_discovery_at'])} (current saved version {lab.get('current_version')})"
    else:
        which = "the current version"
    era = model.get("era") or "none announced yet (the first era)"
    return f"model: {which}; era: {era}; rates are per {model.get('time_unit')}"


def flow_line(f: dict, unit: str | None = None) -> str:
    trust = f.get("origin") or "no equation"
    if f.get("origin") == "fitted" and f.get("events") is not None:
        trust = f"fitted on {num(f['events'])} events"
    if f.get("spread") is not None:
        trust += f", ±{round(100 * f['spread'])}%"
    line = f"  {f['id']} ({end(f.get('from'))} -> {end(f.get('to'))}) = {rate_text(f)}; {trust}"
    if not f.get("measured"):
        line += "; no counter measures it"
    if f.get("not_trusted"):
        line += "; NOT TRUSTED: " + "; ".join(f["not_trusted"])
    return line


def stock_line(s: dict) -> str:
    """A stock's level now and where it came from, in the Dynamics page's words (``model_levels``)."""
    by = s.get("measured_by")
    said = f" (measured by {by})" if by and by.startswith("source ") else f" ({by})" if by else ""
    if "level" in s:
        return f"  {s['id']}: {(num(s['level']) + ' ' + (s.get('unit') or '')).rstrip()}{said}"
    if s.get("level_unread"):
        return f"  {s['id']}: level not read{said}: {s['level_unread']}"
    return f"  {s['id']}: no level read{said}"


def model_lines(model: dict) -> list[str]:
    """A model as ``GET /model`` gives it (``orchestra.sd.reads``)."""
    out = [label_line(model) if "label" in model else f"rates are per {model.get('time_unit')}", "stocks (level now):"]
    for s in model.get("stocks") or []:
        if s.get("hidden"):
            out.append(f"  {s['id']}: hidden (no series measures it), starts at {num(s.get('initial'))}")
        else:
            out.append(stock_line(s))
    flows = model.get("flows") or []
    out.append(f"flows (rate = equation [parameters]; origin and error; per {model.get('time_unit')}):")
    out += [flow_line(f) for f in flows[:FLOWS_SHOWN]]
    if len(flows) > FLOWS_SHOWN:
        out.append(f"  ... and {len(flows) - FLOWS_SHOWN} more (raw: true lists them all)")
    if model.get("inputs"):
        out.append("measured inputs an equation may read: " + ", ".join(model["inputs"]))
    if model.get("loops"):
        out.append("feedback loops: " + "; ".join(" -> ".join(lp["stocks"]) for lp in model["loops"]))
    if model.get("machines"):
        out.append("machines associated with flows (hypotheses from their events): "
                   + "; ".join(f"{m['id']}: {', '.join(m['flows'])}" for m in model["machines"]))
    return out


def eras_lines(e: dict) -> list[str]:
    eras = e.get("eras") or []
    out = [f"eras: {len(eras)} announced; the latest discovery is of {e.get('current') or 'the first era (none announced)'}"]
    for x in eras:
        out.append(f"  {x['id']} from {when(x.get('start'))}: {x.get('label')} (cause event {x.get('cause')})")
    vs = e.get("versions") or {}
    if vs:
        out.append("saved model versions by era: " + "; ".join(f"{'first' if k in ('None', None) else k}: {', '.join(map(str, v))}"
                                                             for k, v in vs.items() if v))
    c = e.get("comparison")
    if not c:
        out.append("no comparison yet: the current era has no boundary behind it, or no evidence of its own")
        return out
    out.append(f"what changed from {c.get('from') or 'the first era'} to {c.get('to')} (z: change over its standard error; "
               "|z| >= 2 is a change):")
    for f in c.get("flows") or []:
        ps = "; ".join(f"{p.get('param')} {num(p.get('before'))} -> {num(p.get('after'))} (×{num(p.get('ratio'))}, z {num(p.get('z'))})"
                       for p in f.get("params") or [])
        out.append(f"  flow {f['id']}: {f.get('status')}" + (f": {ps}" if ps else ""))
    for part in ("stocks", "loops"):
        for k, ids in (c.get(part) or {}).items():
            if ids:
                out.append(f"  {part} {k}: {', '.join(i if isinstance(i, str) else str(i) for i in ids)}")
    return out


def observability_lines(o: dict) -> list[str]:
    out = [(f"observable: {'yes' if o.get('observable') else 'NO'} (rank {o.get('rank')} of {o.get('states')} stocks); "
           f"hidden stocks: {', '.join(o.get('hidden') or []) or 'none'}")]
    for s in o.get("stocks") or []:
        out.append(f"  {s['id']}: {'determined' if s.get('determined') else 'NOT determined'}: {s.get('why')}")
    for m in o.get("modes") or []:
        tc = m.get("time_constant")
        out.append(f"  mode: {m.get('kind')}" + (f", time constant {num(tc)} {o.get('time_unit')}" if tc else ""))
    ctl = o.get("controllability") or {}
    if ctl.get("unreachable"):
        out.append(f"  no machine-associated flow reaches: {', '.join(ctl['unreachable'])}")
    out += [f"  caveat: {c}" for c in o.get("caveats") or []]
    return out


def series_lines(s: dict) -> list[str]:
    out = [(f"series from {when(s.get('start'))} to {when(s.get('end'))} ({dur(s.get('end', 0) - s.get('start', 0))}); "
           f"flows are rates per {s.get('time_unit')}")]
    for sid, row in (s.get("series") or {}).items():
        m = row.get("summary") or {}
        if not m.get("n"):
            out.append(f"  {sid} ({row.get('kind')}): no readings in the window")
            continue
        unit = row.get("unit") or ""
        line = (f"  {sid} ({row.get('kind')}, {unit}): {num(m['first'])} -> {num(m['last'])}; min {num(m['min'])}, max {num(m['max'])}, "
                f"mean {num(m['mean'])} ± {num(m['sd'])}; trend {'+' if m['slope_per_hour'] >= 0 else ''}{num(m['slope_per_hour'])} an hour; "
                f"{m['n']} readings")
        if row.get("recent"):
            line += "; recent: " + ", ".join(num(v) for v in row["recent"])
        out.append(line)
    return out
