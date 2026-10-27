"""An instance starts a session only on the task system's confirmation of its own assignment:
the same ticket moved for another assignment (an earlier offer this instance also accepted)
confirms nothing, so one ticket never gets two sessions."""

from types import SimpleNamespace

from orchestra_agents import runner
from orchestra_agents.protocol import TRANSITIONED
from orchestra_agents.runner import AgentRunner


class Bus:
    def __init__(self, evs):
        self.evs = evs

    def end(self):
        return 0

    def after(self, offset):
        return self.evs[offset:], len(self.evs)


def confirmed(evs, monkeypatch):
    monkeypatch.setattr(runner, "CONFIRM_POLL_S", 0.01)
    r = AgentRunner(SimpleNamespace(events=Bus(evs)))
    return r._confirmed({"assignment": "a2", "ticket": "T-1", "lease_s": 0.2}, "worker/1", 0)


def moved(**extra):
    return {"type": TRANSITIONED, "ticket": "T-1", "owner": "worker/1", "from": "OPEN", "to": "IN_PROGRESS", **extra}


def test_another_assignments_transition_of_the_same_ticket_confirms_nothing(monkeypatch):
    assert confirmed([moved(assignment="a1")], monkeypatch) is False


def test_the_transition_that_names_this_assignment_confirms_it(monkeypatch):
    assert confirmed([moved(assignment="a1"), moved(assignment="a2")], monkeypatch) is True


def test_a_transition_without_an_assignment_is_taken_by_ticket_and_owner(monkeypatch):
    assert confirmed([moved()], monkeypatch) is True
