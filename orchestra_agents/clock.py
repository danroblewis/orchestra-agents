"""Time as the agents' records and schedules use it: a timestamp for a record, whether a daily
time has come, and the one grammar for every interval in ``[agents]`` and in an agent's or a
probe's record. Nothing here imports the rest of the package, so every module may use it."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from orchestra.config import ConfigError

_INTERVAL = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?\s*")
_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
LATE_S = 600.0          # a daily run more than this late was missed (the host was down): skipped when missed = skip


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


def zone(tz: str | None):
    """The time zone ``tz`` names (IANA, America/Los_Angeles), or None for the host's own."""
    if not tz:
        return None
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"unknown time zone {tz!r}: an IANA name such as America/Los_Angeles") from None


LOCAL_WORDS = re.compile(r"\b(local|here|this (machine|computer|host|box)|the (machine|computer|host)|host)\b")


def host_zone() -> str | None:
    """The IANA name of the host's own zone (``TZ`` when it names one, else what ``/etc/localtime``
    links to), or None when the host does not say."""
    import os
    tz = (os.environ.get("TZ") or "").lstrip(":")
    if tz:
        try:
            ZoneInfo(tz)
            return tz
        except (ZoneInfoNotFoundError, ValueError):
            pass
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        return None
    name = target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else None
    try:
        return name if name and ZoneInfo(name) else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def resolve_tz(tz: str | None) -> str | None:
    """``tz`` as the owner answered it: "this machine's clock", "local" or "here" is the host's zone,
    by its IANA name (neither Orchestra nor the agents configure a project zone); anything else is
    returned as given."""
    if not tz or not LOCAL_WORDS.search(re.sub(r"[^a-z ]+", " ", str(tz).lower().replace("'s", ""))):
        return tz
    got = host_zone()
    if not got:
        raise ConfigError(f"{tz!r} is the host's clock, but the host names no time zone (no TZ, no /etc/localtime): "
                          "ask the owner for the zone's name, America/Los_Angeles")
    return got


def _slot(at: str, now_ts: float, tz: str | None, days: int = 0) -> float:
    hh, mm = (int(x) for x in at.split(":"))
    local = datetime.fromtimestamp(now_ts, zone(tz)) if tz else datetime.fromtimestamp(now_ts)
    return (local.replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(days=days)).timestamp()


def due_at(at: str, last: float | None, now_ts: float, tz: str | None = None) -> bool:
    """True if the daily ``at`` time (HH:MM, in ``tz``, else host-local) has passed today and has
    not fired since."""
    today = _slot(at, now_ts, tz)
    return now_ts >= today and (last is None or last < today)


def daily(at: str, last: float | None, now_ts: float, tz: str | None = None, missed: str | None = None) -> str | None:
    """What a daily ``at`` does now: ``fire``; ``skip`` (its time passed more than ``LATE_S`` ago,
    unfired, and ``missed`` is ``skip``: recorded as done, not run); or None (not due)."""
    if not due_at(at, last, now_ts, tz):
        return None
    return "skip" if missed == "skip" and now_ts - _slot(at, now_ts, tz) > LATE_S else "fire"


def next_at(at: str, last: float | None, now_ts: float, tz: str | None = None) -> float:
    """When the daily ``at`` fires next: today's time if it is still to come (or due unfired), else
    tomorrow's."""
    today = _slot(at, now_ts, tz)
    return today if now_ts < today or last is None or last < today else _slot(at, now_ts, tz, days=1)
