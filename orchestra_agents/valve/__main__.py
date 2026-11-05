"""``python -I -m orchestra_agents.valve --declaration <valve.json>``: one decision for the item the
Workflows program host offers (its environment: ``ORCHESTRA_ITEM``, ``ORCHESTRA_WORKFLOW``,
``ORCHESTRA_STEP``, ``ORCHESTRA_FIELDS``, ``ORCHESTRA_REPO``). It prints the decision in one line
(the step's summary), then its result line, ``{"facts": {"set": {"valve_<name>": {outcome, label,
score, reason, cached}}}}``, which the host carries on the finish, and exits with the outcome's code
(``declaration.exit_map``). A declaration that does not parse, or a project that cannot be read,
exits 1 (``error``), saying why."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .decide import decide
from .declaration import parse
from .inputs import gather
from .ledger import Ledger, valve_dir


def state_dir(repo: str | None) -> Path:
    from orchestra.config import load_project
    return Path(load_project(repo or None).state_dir)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="orchestra_agents.valve")
    ap.add_argument("--declaration", required=True, help="the valve's valve.json")
    args = ap.parse_args(argv)
    try:
        raw = json.loads(Path(args.declaration).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"the valve's declaration {args.declaration} cannot be read: {type(e).__name__}")
        return 1
    v, problems = parse(raw)
    if v is None:
        print(f"the valve's declaration {args.declaration} has problems: " + "; ".join(problems))
        return 1
    repo = os.environ.get("ORCHESTRA_REPO")
    try:
        ledger = Ledger(valve_dir(state_dir(repo), v.name))
    except Exception as e:  # noqa: BLE001 - no state directory is no decision, said
        print(f"the project's state directory cannot be read: {type(e).__name__}: {e}")
        return 1
    d = decide(v, gather(v, os.environ), ledger)
    print(d.line())
    print(json.dumps({"facts": {"set": {f"valve_{v.name}": d.fact(v)}}}, ensure_ascii=False))
    sys.stdout.flush()
    return v.code(d.outcome)


if __name__ == "__main__":
    sys.exit(main())
