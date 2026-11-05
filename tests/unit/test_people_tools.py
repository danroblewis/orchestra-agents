"""``propose_people`` (the bridges' SPEC §8): the Supervisor's dry listing of candidate identity-map
entries from the accounts the bus announced, and the join, which is the owner's quoted yes in both
``ask`` modes, exact candidates included; the map is written in the state directory, never the
repository."""

import pytest
from design_fixtures import call, say

from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]
pytest.importorskip("orchestra_bridges.people_table")


def announce(store, source, **data):
    store.events.publish("orchestra.machine.started", {"mode": "ad hoc", "kind": "person", "listens_for": [],
                                                       "package": "bridges", **data}, source=source)


@pytest.fixture
def accounts(store):
    announce(store, "person.jira.alice", name="jira.user.alice", display_name="Alice Example")
    announce(store, "person.slack.u012ab3cd", name="slack.user.u012ab3cd", display_name="Alice Example")   # exact
    announce(store, "person.jira.bob.b", name="jira.user.bob.b", display_name="Bob Builder")
    announce(store, "person.github.bob-b", name="github.user.bob-b", display_name="Builder, Bob")         # weak
    return store


def test_the_dry_listing_names_each_candidate_with_its_evidence_and_writes_nothing(project, accounts):
    out = call(project, accounts, "propose_people")
    assert out["dry_run"] and [c["strength"] for c in out["candidates"]] == ["exact", "weak"]
    exact, weak = out["candidates"]
    assert {a["source"] for a in exact["accounts"]} == {"person.jira.alice", "person.slack.u012ab3cd"}
    assert exact["confidence"] == 0.9 and exact["evidence"] and weak["confidence"] < exact["confidence"]
    assert exact["id"] in out["lines"][0] and "owner's yes" in out["ask"] and "exact ones included" in out["ask"]
    assert out["map"].endswith("bridges/people.toml") and str(project.repo_root) not in out["map"]
    assert not (project.orchestra.state_dir / "bridges" / "people.toml").exists()


def test_a_join_is_the_owners_yes_even_for_an_exact_candidate_under_ask_outside(project, accounts):
    exact = call(project, accounts, "propose_people")["candidates"][0]["id"]
    say(accounts, "What does the table say?")
    with pytest.raises(StoreError, match="owner's yes"):
        call(project, accounts, "propose_people", join=[exact], reason="the same display name on two systems")
    assert not (project.orchestra.state_dir / "bridges" / "people.toml").exists()


def test_the_owners_yes_writes_the_exact_ones_and_a_weak_one_only_when_named(project, accounts):
    exact, weak = (c["id"] for c in call(project, accounts, "propose_people")["candidates"])
    say(accounts, "Yes, join all of these exact ones please.")
    with pytest.raises(StoreError, match="do not name"):
        call(project, accounts, "propose_people", join=[exact, weak], quote="Yes, join all of these exact ones please.",
             reason="the owner said yes to the exact matches")
    out = call(project, accounts, "propose_people", join=[exact], quote="Yes, join all of these exact ones please.",
               reason="the owner said yes to the exact matches")
    assert out["keys"] == ["alice_example"]
    text = (project.orchestra.state_dir / "bridges" / "people.toml").read_text()
    assert '[people.alice_example]' in text and 'jira = "alice"' in text and 'slack = "u012ab3cd"' in text
    assert [c["id"] for c in call(project, accounts, "propose_people")["candidates"]] == [weak]   # joined: gone
    say(accounts, "Yes, Bob Builder on GitHub is the same person.", 2)
    out = call(project, accounts, "propose_people", join=[weak], quote="Yes, Bob Builder on GitHub is the same person.",
               reason="the owner named the weak match")
    assert out["keys"] == ["bob_builder"]
