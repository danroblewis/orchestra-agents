"""The instrument ``agents.valves`` (``Plugin.collectors``, web.py): ``python -m
orchestra_agents.valve.metrics`` prints every valve's decisions by outcome, from each valve's
``tally.json`` (``ledger.py``), as one plain counter named for what it measures:

    valve_decisions_total{valve="oncall_filter", outcome="page"} 12

The valve writes no metric itself (Orchestra's rule 7: instruments measure, they are never
machines); discovery can then see the split of the flow the valve regulates. A valve with no
decision yet prints nothing."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .ledger import DIR, Ledger

SERIES = "valve_decisions_total"


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def text(state_dir: Path | str) -> str:
    base = Path(state_dir).joinpath(*DIR)
    lines = []
    for d in sorted(base.iterdir()) if base.is_dir() else []:
        if d.is_dir():
            for outcome, n in sorted(Ledger(d).counts().items()):
                lines.append(f'{SERIES}{{valve="{_q(d.name)}",outcome="{_q(outcome)}"}} {n}')
    if not lines:
        return ""
    return f"# HELP {SERIES} a valve's decisions, by outcome\n# TYPE {SERIES} counter\n" + "\n".join(lines) + "\n"


def main() -> int:
    from orchestra.config import load_project
    sys.stdout.write(text(load_project(os.environ.get("ORCHESTRA_REPO") or None).state_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
