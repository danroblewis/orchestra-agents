"""The task system's event names are copied by hand into protocol.py (nothing is imported from
it). When orchestra-tasking is installed, its source is read to check the copy: every name here
is in its source, and everything it emits is a name here."""

import re
from pathlib import Path

import pytest

from orchestra_agents.protocol import (
    ACCEPTED,
    ASSIGNED,
    ATTENTION,
    DONE,
    QUERY,
    REFUSED,
    RELEASED,
    REPORTED,
    REQUESTS,
    TASK_EVENTS,
    WORKING,
)


def test_the_copied_event_names_match_the_task_systems_source():
    tasking = pytest.importorskip("tasking")
    source = "".join(p.read_text(encoding="utf-8") for p in sorted(Path(tasking.__file__).parent.rglob("*.py")))
    for name in TASK_EVENTS:
        assert f'"{name}"' in source, f"{name} is in protocol.TASK_EVENTS but not in orchestra-tasking's source"
    emitted = set(re.findall(r'emit\("([a-z_.]+)"', source))
    known = set(TASK_EVENTS) | {ASSIGNED, ACCEPTED, WORKING, REPORTED, RELEASED, ATTENTION, DONE, REFUSED, QUERY} | set(REQUESTS.values())
    assert emitted <= known, f"orchestra-tasking emits {sorted(emitted - known)}, which protocol.py does not list"
