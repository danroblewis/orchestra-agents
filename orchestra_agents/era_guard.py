"""The era filter the Supervisor may set: never one that begins eras faster than discovery can fit
a stock in one.

Discovery reads only the current era (Orchestra's ``sd.rediscover``), and a level live for less
than ``MIN_COVER`` of the window's data is too new to be a stock (``orchestra.sd.discover``); a net
flow needs ``2 * EVENTS_PER_PARAM`` changes (``orchestra.sd.drivers``). So an era shorter than
``minimum_s`` can never give a stock. Run 4 (2026-11-03): the Supervisor set
``eras_from = ["config.changed", "github.pr.merged"]``, a merge came every 5.2 minutes, and the
model stayed empty for 80 minutes.

An era is for a change that can alter how the system behaves (a worker kind added, a deploy, a
release that changes the code that runs), not every merge: releases are compared before and after
(``read_eras``, a prediction), never by resetting the window.

``check`` is ``set_setting``'s: for a change of ``[discovery]`` it reads how often each filter the
change adds (and, for a new window or step, each filter configured) matched on the bus, and refuses
one whose usual gap (the median of the gaps between its matches) is shorter than the minimum,
saying both numbers. Only the envelope is read (``orchestra.sd.eras.begins``)."""

from __future__ import annotations

import statistics
from itertools import pairwise

from orchestra.config import ConfigError, parse_duration

from .store import StoreError

HISTORY = 20000          # the most recent events read to see how often a filter matches
MIN_MATCHES = 3          # a filter matched fewer times than this says nothing yet about how often it comes


def minimum_s(window: float, step: float) -> float:
    """The shortest era discovery can fit a stock in: ``MIN_COVER`` of the window, and at least
    ``2 * EVENTS_PER_PARAM`` steps."""
    from orchestra.sd.discover import MIN_COVER
    from orchestra.sd.drivers import EVENTS_PER_PARAM
    return max(MIN_COVER * window, 2 * EVENTS_PER_PARAM * step)


def _minutes(s: float) -> str:
    return f"{s / 60:.1f} min" if s < 5400 else f"{s / 3600:.1f} h"


def _proposed(key: str, value, current: dict) -> dict | None:
    """``[discovery]`` as it would be after the change, or None when the change is not of it."""
    if key == "discovery":
        return dict(value or {}) if isinstance(value, dict) or value is None else None
    if not key.startswith("discovery."):
        return None
    sub = key.split(".", 1)[1]
    out = dict(current)
    if value is None:
        out.pop(sub, None)
    else:
        out[sub] = value
    return out


def gaps(events: list[dict], entry: str) -> list[float]:
    """The seconds between consecutive events the filter matches, oldest first."""
    from orchestra.events import event_time
    from orchestra.sd.eras import begins
    ts = sorted(t for ev in events if begins(ev, [entry]) and (t := event_time(ev)) is not None)
    return [b - a for a, b in pairwise(ts) if b > a]


def check(project, store, key: str, value) -> str | None:
    """Refuse (``StoreError``) a ``[discovery]`` change that makes eras come faster than discovery's
    minimum; else None, or a caution for a filter not seen often enough to tell."""
    current = dict((project.orchestra.config.raw or {}).get("discovery") or {})
    after = _proposed(key, value, current)
    if after is None:
        return None
    from orchestra.sd.eras import DEFAULT_FROM
    try:
        window = parse_duration(after.get("window"), 3600.0)
        step = parse_duration(after.get("step"), 30.0)
    except ConfigError as e:
        raise StoreError(f"[discovery] {e}") from e
    least = minimum_s(window, step)
    was = list(current.get("eras_from", DEFAULT_FROM))
    now = after.get("eras_from", DEFAULT_FROM)
    if not isinstance(now, list):
        return None                                   # Orchestra's own check says what is wrong with it
    resized = (after.get("window"), after.get("step")) != (current.get("window"), current.get("step"))
    asked = [e for e in now if isinstance(e, str) and (resized or e not in was)]
    if not asked:
        return None
    end = store.events.end()
    events, _ = store.events.read(max(0, end - HISTORY), HISTORY)
    unseen = []
    for entry in asked:
        g = gaps(events, entry)
        if len(g) + 1 < MIN_MATCHES:
            unseen.append(entry)
            continue
        usual = statistics.median(g)
        if usual < least:
            raise StoreError(
                f"refused: eras_from {entry!r} matched {len(g) + 1} events on the bus, one every {_minutes(usual)} as a rule, "
                f"and discovery needs an era of at least {_minutes(least)} to fit a stock (Orchestra's MIN_COVER, "
                f"{_min_cover():.0%} of the {_minutes(window)} window, and at least {_min_steps()} steps of {step:g} s): every "
                "era would be too short and the model would stay empty. An era is for a change that can alter how the system "
                "behaves, not every merge: compare releases before and after (read_eras, a prediction) instead. Nothing was "
                "written")
    if unseen:
        return (f"{', '.join(unseen)} matched fewer than {MIN_MATCHES} events on the bus so far, so how often it comes is not "
                f"known: if it comes more often than every {_minutes(least)}, no era will be long enough to give a stock")
    return None


def _min_cover() -> float:
    from orchestra.sd.discover import MIN_COVER
    return MIN_COVER


def _min_steps() -> int:
    from orchestra.sd.drivers import EVENTS_PER_PARAM
    return 2 * EVENTS_PER_PARAM
