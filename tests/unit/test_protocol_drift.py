"""The task system's event names and ticket states are copied by hand into protocol.py (nothing
is imported from it). When orchestra-tasking is installed, its source is read to check the copy:
every event name here is in its source; every dotted name in an ``emit(`` call there (every
string literal of the call, so a conditional like ``emit("x.paused" if p else "x.resumed")`` is
read whole) is a name here; and every state here is one of its ``State`` members."""

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
    STATES,
    TASK_EVENTS,
    WORKING,
)


def emitted_names(source: str) -> set[str]:
    """Every dotted string literal inside each ``emit(...)`` call, to the call's closing paren."""
    out: set[str] = set()
    for m in re.finditer(r"\bemit\(", source):
        depth, i = 0, m.end() - 1
        while i < len(source):
            depth += {"(": 1, ")": -1}.get(source[i], 0)
            if depth == 0:
                break
            i += 1
        out |= set(re.findall(r'"([a-z_]+\.[a-z_]+)"', source[m.end():i]))
    return out


def test_emitted_names_are_read_whole():
    assert emitted_names('x.emit("a.paused" if p else "a.resumed", by=f(y), note="n")\nemit("b.one")') == {"a.paused", "a.resumed", "b.one"}


def test_the_copied_event_names_and_states_match_the_task_systems_source():
    tasking = pytest.importorskip("tasking")
    source = "".join(p.read_text(encoding="utf-8") for p in sorted(Path(tasking.__file__).parent.rglob("*.py")))
    for name in TASK_EVENTS:
        assert f'"{name}"' in source, f"{name} is in protocol.TASK_EVENTS but not in orchestra-tasking's source"
    emitted = emitted_names(source)
    assert "pickup.resumed" in emitted and "pipeline.resumed" in emitted             # the conditional emits are read
    known = set(TASK_EVENTS) | {ASSIGNED, ACCEPTED, WORKING, REPORTED, RELEASED, ATTENTION, DONE, REFUSED, QUERY} | set(REQUESTS.values())
    assert emitted <= known, f"orchestra-tasking emits {sorted(emitted - known)}, which protocol.py does not list"
    for state in STATES:
        assert f"State.{state}" in source or f'"{state}"' in source, f"{state} is in protocol.STATES but not a State in orchestra-tasking"
    members = re.findall(r"^\s+([A-Z_]+) = \"[A-Z_]+\"$", source[source.index("class State"):], re.MULTILINE)[:len(STATES) + 1]
    assert set(members) == set(STATES)                                                 # and no eleventh state
