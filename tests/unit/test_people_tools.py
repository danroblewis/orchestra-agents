"""``propose_people`` (the bridges' SPEC §8): the Supervisor's dry listing of candidate identity-map
entries from the accounts the bus announced, and the join: under ``ask = "outside"`` an exact
candidate is the Supervisor's to join on its reason (``joined_by: supervisor``, its evidence kept),
undone by ``unjoin_people``; a weak one, and every one under ``always``, the owner's quoted yes;
the map is written in the state directory, never the repository."""

import pytest
from conftest import ask_always
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
    assert exact["id"] in out["lines"][0] and "Join the exact candidates yourself" in out["ask"]
    assert "weak one to the owner" in out["ask"]
    assert out["map"].endswith("bridges/people.toml") and str(project.repo_root) not in out["map"]
    assert not (project.orchestra.state_dir / "bridges" / "people.toml").exists()


def test_under_ask_always_a_join_is_the_owners_yes_even_for_an_exact_candidate(project, accounts):
    ask_always(project)
    out = call(project, accounts, "propose_people")
    assert "owner's yes, exact ones included" in out["ask"]
    say(accounts, "What does the table say?")
    with pytest.raises(StoreError, match="owner's yes"):
        call(project, accounts, "propose_people", join=[out["candidates"][0]["id"]], reason="the same display name on two systems")
    assert not (project.orchestra.state_dir / "bridges" / "people.toml").exists()


def test_under_ask_outside_the_supervisor_joins_an_exact_candidate_and_unjoins_it(project, accounts):
    exact, _weak = call(project, accounts, "propose_people")["candidates"]
    say(accounts, "What does the table say?")
    out = call(project, accounts, "propose_people", join=[exact["id"]], reason="the same display name on two systems")
    assert out["keys"] == ["alice_example"] and out["joined"][0]["joined_by"] == "supervisor"
    assert out["joined"][0]["evidence"] and "unjoin_people undoes it" in out["line"] and "Tell the owner" in out["line"]
    path = project.orchestra.state_dir / "bridges" / "people.toml"
    assert '[people.alice_example]' in path.read_text() and 'slack = "u012ab3cd"' in path.read_text()
    rec = accounts.agent_state("supervisor")["people_joins"]["alice_example"]
    assert rec["joined_by"] == "supervisor" and rec["confidence"] == 0.9 and rec["evidence"] == exact["evidence"]
    assert rec["reason"] == "the same display name on two systems" and rec["candidate"] == exact["id"]
    # undone: the accounts are accounts again, and the Supervisor does not join them again on its own reason
    out = call(project, accounts, "unjoin_people", key="alice_example", reason="the owner said these are two people")
    assert out["unjoined"] == "alice_example" and "alice_example" not in path.read_text()
    assert "alice_example" not in (accounts.agent_state("supervisor").get("people_joins") or {})
    again = call(project, accounts, "propose_people")["candidates"]
    assert exact["id"] in [c["id"] for c in again]
    with pytest.raises(StoreError, match="unjoined before"):
        call(project, accounts, "propose_people", join=[exact["id"]], reason="the same display name on two systems")
    assert not path.read_text().count("[people.")


def test_under_ask_outside_a_weak_candidate_is_still_the_owners(project, accounts):
    exact, weak = (c["id"] for c in call(project, accounts, "propose_people")["candidates"])
    say(accounts, "What does the table say?")
    with pytest.raises(StoreError, match="weak"):
        call(project, accounts, "propose_people", join=[exact, weak], reason="they look like the same people")
    assert not (project.orchestra.state_dir / "bridges" / "people.toml").exists()      # all or nothing


def test_an_owners_join_is_unjoined_only_on_their_words(project, accounts):
    exact = call(project, accounts, "propose_people")["candidates"][0]["id"]
    say(accounts, "Yes, join all of these exact ones please.")
    call(project, accounts, "propose_people", join=[exact], quote="Yes, join all of these exact ones please.",
         reason="the owner said yes to the exact matches")
    assert accounts.agent_state("supervisor")["people_joins"]["alice_example"]["joined_by"] == "owner"
    with pytest.raises(StoreError, match="only on the owner's quoted words"):
        call(project, accounts, "unjoin_people", key="alice_example", reason="it looked wrong to me today")
    say(accounts, "Please unjoin alice_example, they are two people.", 2)
    out = call(project, accounts, "unjoin_people", key="alice_example", reason="the owner asked to unjoin it",
               quote="Please unjoin alice_example, they are two people.")
    assert out["unjoined"] == "alice_example"


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
