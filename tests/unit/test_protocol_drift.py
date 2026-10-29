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
    APPROVED,
    ASSIGNED,
    ATTENTION,
    CHANGES_REQUESTED,
    DONE,
    PLANNER_ROLES,
    QUERY,
    REFUSED,
    RELEASED,
    REPORTED,
    REQUESTS,
    REVIEW_REQUESTED,
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
    for name in (*TASK_EVENTS, ASSIGNED, ACCEPTED, WORKING, REPORTED, RELEASED, REVIEW_REQUESTED, CHANGES_REQUESTED, APPROVED):
        assert f'"{name}"' in source, f"{name} is in protocol.py but not in orchestra-tasking's source"
    emitted = emitted_names(source)
    assert "pickup.resumed" in emitted and "pipeline.resumed" in emitted             # the conditional emits are read
    known = set(TASK_EVENTS) | {ASSIGNED, ACCEPTED, WORKING, REPORTED, RELEASED, REVIEW_REQUESTED, CHANGES_REQUESTED, APPROVED,
                                ATTENTION, DONE, REFUSED, QUERY} | set(REQUESTS.values())
    assert emitted <= known, f"orchestra-tasking emits {sorted(emitted - known)}, which protocol.py does not list"
    for state in STATES:
        assert f"State.{state}" in source or f'"{state}"' in source, f"{state} is in protocol.STATES but not a State in orchestra-tasking"
    body = source[source.index("class State"):]
    body = body[:body.index("\n\n\n")]                                              # the class alone
    members = re.findall(r"^\s+([A-Z_]+) = \"[A-Z_]+\"\s*(?:#.*)?$", body, re.MULTILINE)   # a member may carry a comment
    assert set(members) == set(STATES)                                                 # and no other state


def test_the_planner_role_names_match_the_task_systems():
    tasking = pytest.importorskip("tasking")
    source = (Path(tasking.__file__).parent / "events.py").read_text(encoding="utf-8")
    for role in PLANNER_ROLES:
        assert f'"{role}"' in source, f"{role} is in protocol.PLANNER_ROLES but not a name in tasking/events.py"


def test_the_claim_rule_matches_the_task_systems():
    """Who holds a turn is decided the same way on both sides: the first valid claim on the bus."""
    tasking = pytest.importorskip("tasking.assignments")
    from orchestra_agents.protocol import holder
    offer = {"assignment": "a", "to": "worker/1"}
    cases = [[{"type": ACCEPTED, "assignment": "a", "source": "worker/2"}, {"type": ACCEPTED, "assignment": "a", "source": "worker/1"}],
             [{"type": ACCEPTED, "assignment": "a", "source": "worker/1", "instance": "worker/9"}],
             [{"type": ACCEPTED, "assignment": "b", "source": "worker/1"}, {"type": ACCEPTED, "assignment": "a", "source": "worker/1"}]]
    for evs in cases:
        for o in (offer, {"assignment": "a"}):
            assert holder(evs, o) == tasking.holder(evs, o), (evs, o)
