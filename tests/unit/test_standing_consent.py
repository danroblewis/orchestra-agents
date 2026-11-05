"""The owner's standing consent (Orchestra's rule 9 as amended 2026-11-04, ``[intended] settle_own``
in the private overlay; ``ask.settles_own``): under ``ask = "outside"`` the Supervisor confirms the
machines it designs itself, says so, and the owner can discard them; without the consent, or under
``always``, the confirm stays the owner's."""

import pytest
from conftest import ask_always
from design_fixtures import call, layer, request, say

from orchestra_agents import ask
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

WHY = "the triage machine the owner's queue needs, designed from what the bus shows"
FCI = "machine:feature_count_improver"
TURN = "Create a new Universal Machine called Feature Count Improver."


def consent(project, on=True):
    f = project.orchestra.overlay_file
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('[intended]\nsettle_own = ["supervisor"]\n' if on else "# none\n")
    project.reload_config()


def designed(project, store):
    """A machine the Supervisor designs on its own reason (no quote): its proposal, with an expected effect."""
    say(store, TURN, 3)
    out = call(project, store, "declare_machine", name="Feature Count Improver", kind="universal",
               description="Improves how many features get delivered per day", receives=["gate.landed"],
               emits=["work.improvement_proposed"], invariants=["never changes code itself"],
               steps=["Read gate.landed since the last run", "Publish work.improvement_proposed"], uses=["the queue"],
               mode="continuous", reason=WHY)
    call(project, store, "declare_expected_effect", machine="feature_count_improver", horizon="3d", reason=WHY,
         expect=[{"flow": "tasks_done_rate", "factor": 1.2}])
    return out


def test_with_the_consent_the_design_flow_confirms_its_own_machine_and_says_so(project, store):
    consent(project)
    where = ask.settles_own(project)
    assert where and where.startswith("[intended] settle_own in ")
    out = designed(project, store)
    assert any("standing consent is on" in x and "yourself" in x for x in out["said"])    # the design flow's next step
    done = call(project, store, "confirm", element=FCI, reason=WHY)
    m = layer(project)["machines"]["feature_count_improver"]
    assert m["state"] == "declared" and m["confirmed"]["by"] == "supervisor" and m["confirmed"]["reason"] == WHY
    assert m["confirmed"]["standing_consent"] == where
    said = " ".join(done["said"])
    assert "standing consent" in said and "can discard it" in said and "never theirs" in said
    assert (project.state_dir / "machines" / "feature_count_improver" / "machine.toml").exists()   # it runs
    # the owner discards it on the Machines page: removed, and retired here
    rid = request(store, "discard", FCI)
    out = call(project, store, "discard", element=FCI, request=rid, reason="the owner clicked discard on the page")
    assert "feature_count_improver" not in layer(project)["machines"]
    assert "the owner undid it" in out["said"][0]
    assert not (project.state_dir / "machines" / "feature_count_improver").exists()


def test_without_the_consent_the_confirm_stays_the_owners(project, store):
    out = designed(project, store)
    assert not any("standing consent" in x for x in out["said"])
    with pytest.raises(StoreError, match="owner's to ask"):
        call(project, store, "confirm", element=FCI, reason=WHY)
    assert layer(project)["machines"]["feature_count_improver"]["state"] == "proposed"


def test_under_ask_always_the_consent_is_not_used(project, store):
    consent(project)
    ask_always(project)
    assert ask.settles_own(project) is None
    designed(project, store)
    with pytest.raises(StoreError, match="owner's to ask"):
        call(project, store, "confirm", element=FCI, reason=WHY)


def test_the_consent_never_reaches_the_owners_machine(project, store):
    consent(project)
    say(store, "Create a new Universal Machine called Feature Count Improver.", 3)
    call(project, store, "declare_machine", name="Feature Count Improver", kind="universal",
         description="Improves how many features get delivered per day", receives=["gate.landed"], mode="continuous",
         invariants=["never changes code itself"], steps=["Read gate.landed"], uses=["the queue"],
         quote="Create a new Universal Machine called Feature Count Improver.", reason=WHY)
    with pytest.raises(StoreError, match="confirmed by the owner"):           # the owner's proposal: Orchestra refuses
        call(project, store, "confirm", element=FCI, reason=WHY)
