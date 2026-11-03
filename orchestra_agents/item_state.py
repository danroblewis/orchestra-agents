"""The open items as the Workflows plugin has them now, for the "since" digest's first paragraph
(``since_state``): each open item's step, how long it has been there, what its wait is, the step's
policy (its ``within`` guard) and how long the step usually takes (T2j s16: SCRUM-47 waited 12
hours at ``sail_check`` for a SAIL result that never came, and the digest named only the day's
replays).

"Usually" is measured, never configured: each item the window names (and each open one) is walked
by the plugin's own position view over its record (``define.view.walk``), and the time between a
move into a pile and the move out of it is one sample; a pile's ``typical`` is the median of at
least ``SAMPLES`` of them. Nothing here writes; a plugin that is not installed, or a read that
fails, gives None and the digest says what the bus says alone."""

from __future__ import annotations

import time
from datetime import datetime
from statistics import median

from .config import Project

SAMPLES = 3            # a pile's typical time needs at least this many stays in it
WALKED = 80            # items walked for the typical times, at most (the open ones first)


def _seconds(iso) -> float | None:
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except ValueError:
        return None


def stays(moves) -> dict[tuple[str, str], list[float]]:
    """``(workflow, pile) -> [seconds]``: each stay of an item in a pile, from its moves in order."""
    out: dict[tuple[str, str], list[float]] = {}
    entered: dict[str, tuple[str, float]] = {}
    for m in moves:
        t = _seconds(m.time)
        if t is None:
            continue
        was = entered.get(m.workflow)
        if was and was[0] == m.source and t >= was[1]:
            out.setdefault((m.workflow, m.source), []).append(t - was[1])
        entered[m.workflow] = (m.to, t)
    return out


def typical(samples: dict[tuple[str, str], list[float]]) -> dict[tuple[str, str], float]:
    return {k: median(xs) for k, xs in samples.items() if len(xs) >= SAMPLES}


def walker():
    """The plugin's position view (``define.view.walk``), or None for a plugin without it (tests replace this)."""
    try:
        from orchestra_workflows.define.view import walk
    except ImportError:
        return None
    return walk


def _moves(walk, defs, record, item: str) -> list:
    try:
        return walk(defs.workflows, record.log(item))[1]
    except Exception:  # noqa: BLE001 - one item's log unreadable: the others still measure the steps
        return []


def read(project: Project, subjects: list[str]) -> dict | None:
    """``{"rows": [open item rows], "typical": {(workflow, pile): s}, "within": {(workflow, step): s},
    "outside": {(workflow, step): system}}``, or None without the plugin or its Items list."""
    from .workflow_tools import _outside_steps, plugin
    p = plugin()
    if p is None or getattr(p, "item_list", None) is None:
        return None
    o = project.orchestra
    defs = p.define.load(o)
    record = p.Record.for_project(o)
    got = p.item_list(record, defs, time.time())
    rows = [r for w in got.get("workflows") or [] for r in w.get("items") or [] if not r.get("closed")]
    within = {(w.name, st.name): st.guards.within_s for w in defs.workflows for st in w.steps
              if getattr(st, "guards", None) is not None and st.guards.within_s}
    samples: dict[tuple[str, str], list[float]] = {}
    walk = walker()
    if walk is not None:
        names = list(dict.fromkeys([*(str(r.get("item")) for r in rows), *subjects]))[:WALKED]
        for item in names:
            for k, xs in stays(_moves(walk, defs, record, item)).items():
                samples.setdefault(k, []).extend(xs)
    return {"rows": rows, "typical": typical(samples), "within": within, "outside": _outside_steps(defs)}
