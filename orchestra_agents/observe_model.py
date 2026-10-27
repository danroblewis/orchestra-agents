"""What the Supervisor reads of the project's measurements: the metrics catalogue (every series in
the project's own store, as Orchestra's Core page lists it) and the system dynamics model Orchestra
discovered from them (as its Dynamics page shows it: ``orchestra.web.dynamics.model_view``),
shortened to what a diagnosis needs. Read only, through Orchestra as a library."""

from __future__ import annotations

import time

from orchestra.metrics import for_project
from orchestra.web.dynamics import model_view

from .config import Project

LABEL_VALUES = 8          # values shown per label


def list_metrics(project: Project, a: dict) -> dict:
    """Every series name in the project's store (or those starting with ``prefix``): how many
    series, their labels, when each was last written, and how many points in the last hour."""
    prefix, now = a.get("prefix") or "", time.time()
    store = for_project(project.orchestra)
    try:
        dump = store.dump(prefix, {"project": project.key})
        backend = getattr(store, "name", "")
    finally:
        store.close()
    names: dict[str, dict] = {}
    for name, labels, pts in dump:
        d = names.setdefault(name, {"series": 0, "labels": {}, "last": None, "last_hour": 0})
        d["series"] += 1
        for k, v in labels.items():
            if k != "project":
                d["labels"].setdefault(k, set()).add(v)
        if pts:
            d["last"] = max(d["last"] or 0, pts[-1][0])
            d["last_hour"] += sum(1 for t, _ in pts if t >= now - 3600)
    rows = [{"name": n, "series": d["series"], "labels": {k: sorted(v)[:LABEL_VALUES] for k, v in sorted(d["labels"].items())},
             "last_written_s_ago": round(now - d["last"]) if d["last"] else None, "points_last_hour": d["last_hour"],
             **({"note": "Orchestra's own: discovery leaves it out"} if n.startswith("orchestra_") else {})}
            for n, d in sorted(names.items())]
    return {"ok": True, "store": backend or project.orchestra.config.metrics_url or "file", "names": len(rows), "metrics": rows}


def _ref(r: dict | None) -> str | None:
    if not r:
        return None
    inner = ",".join(f'{k}="{v}"' for k, v in sorted((r.get("labels") or {}).items()) if k != "project")
    return r["name"] + (f"{{{inner}}}" if inner else "")


def get_model(project: Project) -> dict:
    """The current model: its stocks and the series each is, its flows with their ends, current
    equation (and its origin: fitted, discovered or declared) and the machine attributed to it,
    and what the last discovery left out and why."""
    view = model_view(project.orchestra)
    if not view.get("ok"):
        return {"ok": True, "model": None, "why": view.get("error"), "discovery": view.get("discovery")}
    m = view["model"]
    d = m.get("discovery") or {}
    eq = lambda f: f.get("current") or {}
    return {"ok": True, "version": m.get("version"), "live": m.get("live"), "as_of": m.get("as_of"),
            "stocks": [{"id": s["id"], "name": s["name"], "series": _ref(s.get("series"))} for s in m["stocks"]],
            "flows": [{"id": f["id"], "name": f["name"], "from": f.get("from"), "to": f.get("to"), "machine": f.get("machine"),
                       "equation": eq(f).get("form"), "params": eq(f).get("params"), "origin": eq(f).get("origin")}
                      for f in m["flows"]],
            "machines": [{"id": x["id"], "kind": x["kind"], "flows": x.get("attributed")} for x in m.get("machines") or []],
            "discovery": {k: d[k] for k in ("start", "end", "step", "looked_at", "series") if k in d},
            "left_out": d.get("left_out") or {}}
