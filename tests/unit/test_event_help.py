"""Every event a built-in agent wakes on is described on the Agents page (org.EVENT_HELP): its
trigger list never says an event is unknown that the agents' own floor tools listen for."""

from orchestra_agents.builtins import ROLES
from orchestra_agents.org import EVENT_HELP


def test_every_built_in_trigger_is_described():
    types = {w.split(":")[0] for r in ROLES.values() for w in r.wake_on}
    assert "attention.needed" in types
    assert not sorted(t for t in types if t not in EVENT_HELP)
