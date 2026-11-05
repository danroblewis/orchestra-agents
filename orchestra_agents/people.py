"""People relayed from another system, said one line per system (the owner, 2026-11-04: "We have a
problem now where we have way too many people names sometimes, and it's breaking various things").

The bridges' pollers publish each act as who acted, ``person.<system>.<account>``, and announce each
account once, kind ``person``, named as an account (``jira.user.<account>``, the display name a
detail; the bridges' SPEC §8): at work, hundreds of Jira people. Every
answer that listed the machines one by one listed them all. Here a system's people are grouped by
their source (``person.<system>.``; ``person.<key>``, a person the identity map lists, groups as
``mapped``) and, once a group holds more than ``SUMMARISED``, said as one line: how many, how many
were heard today, and the few heard most in the last day, by name. A smaller group stays listed one
by one, as before. A tool that lists machines takes ``people`` (the system) and ``match`` (part of an
id or a name) to name a group's people, at most ``LISTED`` of them.

Nothing here is a judgement of who matters: the order is the bus's counts, and the names are what
each person's announcement said (``name``)."""

from __future__ import annotations

import time

SUMMARISED = 10        # a group of more people than this is one line
TOP = 5                # the people named in a group's line, heard most in the last day
LISTED = 25            # at most this many people in an answer that names a group's people
MAPPED = "mapped"      # the group of person.<key>: people the project's identity map lists


def group_of(mid: str) -> str | None:
    """The group a person's source is in: its system for ``person.<system>.<account>``, ``mapped``
    for ``person.<key>``; None for anything else (a program, an agent, the owner)."""
    parts = str(mid or "").split("/")[0].split(".")
    if parts[0] != "person" or len(parts) < 2:
        return None
    return parts[1] if len(parts) >= 3 else MAPPED


def pattern(group: str) -> str:
    return "person.<key>" if group == MAPPED else f"person.{group}.<account>"


def day_start(now: float | None = None) -> float:
    t = time.localtime(now if now is not None else time.time())
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def events_today(m: dict) -> int:
    """The events the machine published in the last day, as the view counts them (``publishes[].day``)."""
    return sum(int(p.get("day") or 0) for p in m.get("publishes") or [] if isinstance(p, dict))


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    """Each group's people among ``rows`` (machines in the view's shape)."""
    out: dict[str, list[dict]] = {}
    for m in rows:
        g = group_of(m.get("id"))
        if g is not None and not m.get("intent"):         # one a declaration is heard through stays listed
            out.setdefault(g, []).append(m)
    return out


def split(rows: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """The rows still listed one by one, and the groups said as one line each (more than ``SUMMARISED``)."""
    big = {g: ms for g, ms in groups(rows).items() if len(ms) > SUMMARISED}
    held = {id(m) for ms in big.values() for m in ms}
    return [m for m in rows if id(m) not in held], big


def _who(m: dict) -> str:
    name = m.get("name")
    return f"{name} ({m['id']})" if name and name != m["id"] else str(m["id"])


def busiest(ms: list[dict]) -> list[dict]:
    return sorted(ms, key=lambda m: (-events_today(m), -(m.get("last_seen") or 0), str(m.get("id"))))


def summary(group: str, ms: list[dict], now: float | None = None, tool: str = "list_machines") -> dict:
    """One group said as one line, with what it is made of."""
    start = day_start(now)
    active = sum(1 for m in ms if (m.get("last_seen") or 0) >= start)
    top = busiest(ms)[:TOP]
    who = ", ".join(f"{_who(m)}, {events_today(m)} events" for m in top)
    whose = "the identity map's people" if group == MAPPED else f"{group}'s people, relayed by its poller"
    line = (f"{len(ms)} people: {whose} ({pattern(group)}); {active} heard today; most heard in the last day: {who}; "
            f"{tool} with people=\"{group}\" (and match=\"<part of a name>\") names them")
    return {"group": group, "pattern": pattern(group), "people": len(ms), "heard_today": active,
            "top": [{"id": m["id"], "name": m.get("name"), "events_last_day": events_today(m)} for m in top], "line": line}


def summaries(big: dict[str, list[dict]], now: float | None = None, tool: str = "list_machines") -> list[dict]:
    return [summary(g, ms, now, tool) for g, ms in sorted(big.items(), key=lambda kv: (-len(kv[1]), kv[0]))]


def pick(rows: list[dict], group: str, match: str | None = None, limit: int = LISTED) -> tuple[list[dict], int]:
    """A group's people (``group``: a system, or ``mapped``; ``*`` for every person), those whose id or
    name holds ``match`` (any case), busiest first, at most ``limit``; and how many matched."""
    q = (match or "").casefold()
    got = [m for m in rows if (group == "*" and group_of(m.get("id")) is not None) or group_of(m.get("id")) == group]
    if q:
        got = [m for m in got if q in str(m.get("id")).casefold() or q in str(m.get("name") or "").casefold()]
    got = busiest(got)
    return got[:limit], len(got)


def compact(ids: list[str]) -> list[str]:
    """A list of sources with each big group of people as one entry (``person.jira.<account>: 600 people``),
    in the place of its first."""
    counts: dict[str, int] = {}
    for s in ids:
        if (g := group_of(s)) is not None:
            counts[g] = counts.get(g, 0) + 1
    out, said = [], set()
    for s in ids:
        g = group_of(s)
        if g is None or counts[g] <= SUMMARISED:
            out.append(s)
        elif g not in said:
            said.add(g)
            out.append(f"{pattern(g)}: {counts[g]} people")
    return out


def named(ids: list[str], shown: int = 20) -> str:
    """Sources said in a line: big groups of people counted (``compact``), at most ``shown`` entries."""
    xs = compact(ids)
    return ", ".join(xs[:shown]) + (f", and {len(xs) - shown} more" if len(xs) > shown else "")
