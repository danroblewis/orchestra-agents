"""What a valve keeps, in the agents' state directory (``<state>/agents/valves/<name>/``, SPEC §7):

- ``cache.json``: fingerprint → the model's answer (label, score, reason, when), so an unchanged
  item is never asked twice; only answers in the declared labels are kept, at most ``CACHE_MAX``;
- ``day.json``: the UTC day and the calls made in it (each attempt counts: each may cost), against
  ``max_calls_per_day``;
- ``tally.json``: the decisions by outcome since the valve was declared, cumulative, which the
  instrument ``agents.valves`` prints (``metrics.py``);
- ``calls.jsonl``: one line per decision: when, the inputs' fingerprint, cached or not, the label,
  score, outcome, why it fell back, the HTTP status, attempts, latency and the usage and cost the API
  reported. Never the token, the inputs' text or the reason; cut to its last ``LOG_KEEP`` lines.

One valve's instances take ``lock`` for a whole decision, so two never ask for the same item."""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

CACHE_MAX = 5000
LOG_KEEP = 5000
DIR = ("agents", "valves")


def valve_dir(state_dir: Path | str, name: str) -> Path:
    return Path(state_dir).joinpath(*DIR, name)


def _read(path: Path, default):
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return got if isinstance(got, type(default)) else default


def _write(path: Path, value) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class Ledger:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self):
        with open(self.root / "lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield self
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def cached(self, fp: str) -> dict | None:
        got = _read(self.root / "cache.json", {}).get(fp)
        return got if isinstance(got, dict) else None

    def keep(self, fp: str, label: str, score: float, reason: str) -> None:
        cache = _read(self.root / "cache.json", {})
        cache[fp] = {"label": label, "score": score, "reason": reason, "at": time.time()}
        if len(cache) > CACHE_MAX:
            for k, _ in sorted(cache.items(), key=lambda kv: kv[1].get("at", 0))[:len(cache) - CACHE_MAX]:
                del cache[k]
        _write(self.root / "cache.json", cache)

    def calls_today(self) -> int:
        d = _read(self.root / "day.json", {})
        return int(d.get("calls", 0)) if d.get("day") == today() else 0

    def spend(self, cap: int) -> bool:
        """Count one call against today's cap; False (nothing counted) when the cap is reached."""
        n = self.calls_today()
        if n >= cap:
            return False
        _write(self.root / "day.json", {"day": today(), "calls": n + 1})
        return True

    def tally(self, outcome: str) -> None:
        t = _read(self.root / "tally.json", {})
        t[outcome] = int(t.get(outcome, 0)) + 1
        _write(self.root / "tally.json", t)

    def counts(self) -> dict[str, int]:
        return {k: int(v) for k, v in _read(self.root / "tally.json", {}).items()}

    def log(self, line: dict) -> None:
        path = self.root / "calls.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, sort_keys=True) + "\n")
        try:
            if path.stat().st_size > LOG_KEEP * 400:
                lines = path.read_text(encoding="utf-8").splitlines()[-LOG_KEEP:]
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    def calls(self) -> list[dict]:
        try:
            return [json.loads(x) for x in (self.root / "calls.jsonl").read_text(encoding="utf-8").splitlines() if x]
        except (OSError, ValueError):
            return []
