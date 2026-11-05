"""Hundreds of people as machines (the owner, 2026-11-04: "way too many people names sometimes, and it's
breaking various things"): a relayed system's poller publishes each act as ``person.<system>.<account>``
and announces each person once with their display name, kind ``person``. ``crowd`` puts that on the bus:
``n`` Jira people, each announced and acting ``acts`` times (the first ``busy`` of them more), beside a few
programs, as the bridges' poller would (``notes``: the poller's notes, on each person it announces)."""

from __future__ import annotations

import time

PROGRAMS = ("slack_notify", "review_nudger", "build_watch")


def person(i: int) -> str:
    return f"person.jira.acct{i:04d}"


def display(i: int) -> str:
    return f"Person Number{i:04d}"


def crowd(store, n: int = 600, busy: int = 5, acts: int = 1, now: float | None = None,
          notes: dict | None = None) -> list[str]:
    """Publish ``n`` Jira people and the programs on ``store``'s bus; the people's sources."""
    now = now or time.time()
    events = store.events
    for p in PROGRAMS:
        events.publish("orchestra.machine.started", {"mode": "continuous", "kind": "program", "listens_for": [],
                                                     "package": "demo"}, source=p)
        events.publish(f"{p}.ran", {"ok": True}, source=p)
    out = []
    for i in range(n):
        src = person(i)
        out.append(src)
        at = now - 900 + i
        events.publish("orchestra.machine.started", {"mode": "ad hoc", "kind": "person", "listens_for": [],
                                                     "package": "bridges", "name": display(i), **(notes or {})},
                       source=src, time=at)
        for k in range(acts + (20 - i if i < busy else 0)):
            events.publish("jira.issue.updated", {"key": f"SCRUM-{i}", "to": "In Progress"}, source=src,
                           subject=f"SCRUM-{i}", time=now - 900 + i + k)
    return out
