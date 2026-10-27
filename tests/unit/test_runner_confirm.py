"""An instance starts a session only once it has read on the bus that its claim of the offer was
the first valid one (the bus's order decides a race; the task system says nothing to a loser): a
claim of the same offer by another instance before it, a release of it, or a claim never seen
holds nothing, so one turn never gets two sessions and a loser spends nothing."""

from types import SimpleNamespace

from orchestra_agents import runner
from orchestra_agents.protocol import ACCEPTED, RELEASED
from orchestra_agents.runner import AgentRunner

OFFER = {"type": "task.assigned", "id": "o1", "assignment": "a2", "ticket": "T-1", "role": "worker", "lease_s": 0.2}


class Bus:
    def __init__(self, evs):
        self.evs = evs

    def end(self):
        return 0

    def after(self, offset):
        return self.evs[offset:], len(self.evs)


def claim(source, aid="a2", id_=None, **extra):
    return {"type": ACCEPTED, "id": id_ or f"c-{source}-{aid}", "assignment": aid, "ticket": "T-1", "role": "worker", "source": source, **extra}


def held(evs, monkeypatch, offer=OFFER, me="worker/1"):
    monkeypatch.setattr(runner, "CONFIRM_POLL_S", 0.01)
    r = AgentRunner(SimpleNamespace(events=Bus([offer, *evs])))
    return r._held(offer, me, {"id": f"c-{me}-a2"}, 0)


def test_its_own_claim_first_holds_the_turn(monkeypatch):
    assert held([claim("worker/1"), claim("worker/2")], monkeypatch) is True


def test_another_instances_claim_before_it_wins_the_race(monkeypatch):
    assert held([claim("worker/2"), claim("worker/1")], monkeypatch) is False


def test_a_claim_of_another_offer_of_the_same_ticket_does_not_count(monkeypatch):
    assert held([claim("worker/2", aid="a1"), claim("worker/1")], monkeypatch) is True


def test_an_invalid_claim_before_it_does_not_count(monkeypatch):
    """A claim whose instance is not its source, or of an offer addressed to another instance, holds nothing."""
    assert held([claim("worker/2", instance="worker/9"), claim("worker/1")], monkeypatch) is True
    addressed = {**OFFER, "to": "worker/1"}
    assert held([claim("worker/2"), claim("worker/1")], monkeypatch, offer=addressed) is True


def test_a_release_of_the_assignment_before_its_claim_is_read_holds_nothing(monkeypatch):
    released = {"type": RELEASED, "id": "r", "assignment": "a2", "ticket": "T-1", "reason": "the operator stopped it"}
    assert held([released, claim("worker/1")], monkeypatch) is False


def test_a_claim_never_seen_on_the_bus_holds_nothing(monkeypatch):
    assert held([claim("worker/2")], monkeypatch) is False
