"""What the modeling tools say back about the model, an experiment, the eras, observability and
series (modeling.py; the runs and the verdicts are ``run_text``): Orchestra's answers
(``orchestra.sd.experiments``) as short lines with the key numbers first, so a model reads them
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


def model_lines(model: dict) -> list[str]:
    """A model as ``GET /model`` gives it (``views.model_view``), or an experiment's draft."""
    out = [label_line(model) if "label" in model else f"rates are per {model.get('time_unit')}", "stocks (level now):"]
    for s in model.get("stocks") or []:
        if s.get("hidden"):
            out.append(f"  {s['id']}: hidden (no series measures it), starts at {num(s.get('initial'))}")
        else:
            out.append(f"  {s['id']}: {num(s['level']) + ' ' + (s.get('unit') or '') if 'level' in s else 'no level read'}".rstrip())
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


def experiment_head(e: dict, me: str | None = None) -> str:
    owner = "yours" if me and e.get("owner") == me else f"{e.get('owner')}'s"
    base = e.get("base") or {}
    lab = label_line({"label": base.get("label") or {}, "era": base.get("era"), "time_unit": (e.get("draft") or {}).get("time_unit")})
    out = f"experiment {e['id']} \"{e.get('title')}\" ({e.get('status')}, {owner}); based on {lab.removeprefix('model: ')}"
    if e.get("origin"):
        out += f"; made by the {e['origin'].get('mode')} run of {e['origin'].get('experiment')}"
    return out


def scenario_lines(sc: dict | None) -> list[str]:
    out = []
    for name, s in ((sc or {}).get("inputs") or {}).items():
        out.append(f"  input {name} in a forward run: " + ", ".join(f"{k} {num(v) if not isinstance(v, list) else len(v)}"
                                                             for k, v in s.items()))
    for sid, lv in ((sc or {}).get("levels") or {}).items():
        out.append(f"  stock {sid} starts a forward run at {num(lv)}")
    return out


def diff_lines(d: dict) -> list[str]:
    flows, stocks = d.get("flows") or {}, d.get("stocks") or {}
    out = [f"changes from the base: {d.get('edits', 0)}"]
    for c in flows.get("changed") or []:
        a, b = c["before"], c["after"]
        what = []
        if "from" in c["what"] or "to" in c["what"]:
            what.append(f"ends {end(a['from'])} -> {end(a['to'])} became {end(b['from'])} -> {end(b['to'])}")
        what.append(f"{rate_text(a)}  ->  {rate_text(b)}")
        out.append(f"  flow {c['id']} changed: " + "; ".join(what))
    for f in flows.get("added") or []:
        out.append(f"  flow {f['id']} added ({end(f.get('from'))} -> {end(f.get('to'))}) = {rate_text(f)}")
    for f in flows.get("removed") or []:
        out.append(f"  flow {f['id']} removed (it was {end(f.get('from'))} -> {end(f.get('to'))} = {rate_text(f)})")
    for s in stocks.get("added") or []:
        out.append(f"  stock {s['id']} added (hidden: no series measures it; starts at {num(s.get('initial'))})")
    for s in stocks.get("removed") or []:
        out.append(f"  stock {s} removed")
    out += scenario_lines(d.get("scenario"))
    if d.get("edits"):
        out.append("  (an equation you write is declared: its band is ±50% and it is not trusted until a discovery fits it)")
    return out


def run_line(r: dict) -> str:
    s = r.get("summary") or {}
    if r.get("mode") == "forward":
        tail = f"{dur(s.get('horizon_s'))} ahead, {'trusted' if s.get('trusted') else 'NOT trusted'}"
    elif r.get("mode") == "replay":
        st, fl = s.get("stocks") or {}, s.get("flows") or {}
        tail = (f"stocks {st.get('better', 0)} better/{st.get('same', 0)} same/{st.get('worse', 0)} worse, "
                f"flows {fl.get('better', 0)}/{fl.get('same', 0)}/{fl.get('worse', 0)}")
    else:
        tail = (f"{'reached' if s.get('reached') else 'not reached'} with {s.get('count')} change(s)"
                + (", ambiguous" if s.get("ambiguous") else "") + (f", new draft {s['experiment']}" if s.get("experiment") else ""))
    return f"  {r.get('id')} {r.get('mode')} by {r.get('by')} at {when(r.get('at'))}: {tail}"


def experiment_lines(e: dict, diff: dict | None = None, me: str | None = None, model: bool = False) -> list[str]:
    out = [experiment_head(e, me)]
    if e.get("purpose"):
        out.append(f"purpose: {e['purpose']}")
    if diff is not None:
        out += diff_lines(diff)
    else:
        out.append(f"changes from the base: {e.get('changes', 0)} (diff_experiment lists them)")
    if e.get("runs"):
        out.append("runs (read_run gives any again):")
        out += [run_line(r) for r in e["runs"][-8:]]
    if e.get("prediction"):
        p = e["prediction"]
        out.append(f"committed as prediction {p.get('id')} for change {p.get('change')}, horizon {dur(p.get('horizon_s'))}"
                   + ("" if p.get("trajectories") else f" (without trajectories: {p.get('no_trajectories')})"))
    refused = [x for x in e.get("log") or [] if x.get("ok") is False][-1:]
    if refused:
        out.append(f"last refused edit batch: {refused[0].get('error')} (edit {refused[0].get('index')})")
    if model:
        out.append("the draft:")
        out += ["  " + x for x in model_lines(e.get("draft") or {})]
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
