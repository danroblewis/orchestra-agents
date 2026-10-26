"""Time as the agents' records and schedules use it: a timestamp for a record, whether a daily
time has come, and the one grammar for every interval in ``[agents]`` and in an agent's or a
probe's record. Nothing here imports the rest of the package, so every module may use it."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from orchestra.config import ConfigError

_INTERVAL = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?\s*")
_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def now() -> str:
    """When a record was written, as an ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def parse_interval(text: str | float) -> float:
    """Seconds for an interval written as Orchestra writes durations (a bare number of seconds,
    or a number with ms, s, m or h), or with ``d`` for days: ``90``, ``90s``, ``10m``, ``6h``, ``1d``."""
    if isinstance(text, (int, float)):
        return float(text)
    m = _INTERVAL.fullmatch(str(text))
    if not m:
        raise ConfigError(f"bad interval {text!r}: write seconds (90), or a number with ms, s, m, h or d (90s, 10m, 6h, 1d)")
    return float(m.group(1)) * _UNITS[m.group(2) or "s"]


def due_at(at: str, last: float | None, now_ts: float) -> bool:
    """True if the daily ``at`` time (HH:MM, local) has passed today and has not fired since."""
    hh, mm = (int(x) for x in at.split(":"))
    today = datetime.fromtimestamp(now_ts).replace(hour=hh, minute=mm, second=0, microsecond=0).timestamp()
    return now_ts >= today and (last is None or last < today)
