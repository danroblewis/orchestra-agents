"""The design tools of the Machines side (machine-design.md §2.3–§2.5, §3; acceptance tests 12–17):
a machine declared with every field of its kind and its guesses, refined edit by edit with what each
replaced, its expected effect published only at confirm with the change its first turn announces,
the owner's page request, and the refinement passes. The declared layer is the stand-in."""

import time
from pathlib import Path

import pytest
from design_fixtures import WHY, call, changes, layer, say

from orchestra_agents import design_confirm, design_passes, intended_api, prompting
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

TURN3 = "Create a new Universal Machine called Feature Count Improver."
TURN4 = ("I think the Feature Count Improver would watch the ticket queue for DONE and REJECTED tickets, and the Merge Gate, "
         "then maybe communicate those ideas to the Coordinator.")
TURN5 = ("I see this has a lot of high-throughput event triggers, maybe we should make this a scheduled task for 12am every "
         "day.")
STEPS = ["Read session.settled and task.reported since the last run", "Compare Features with acceptance tests passing",
         "Find what slowed delivery", "Write one improvement", "Publish work.improvement_proposed for the Coordinator"]
SPEC = {"invariants": ["never changes code itself; it proposes work", "one proposal a run at most",
                       "reads only what it is given"], "steps": STEPS, "uses": ["the coordinator's queue"]}


def improver(project, store, **over):
    say(store, TURN3, 3)
    args = {"name": "Feature Count Improver", "kind": "universal", "description": "Improves how many features get delivered per day",
            "tags": ["features", "feedback loop"],
            "engagement": {"mode": "scheduled", "at": "09:00", "tz": "America/Los_Angeles", "missed": "skip"},
            "receives": ["session.settled", "task.reported"], "emits": ["work.improvement_proposed"],
            "reads": ["features", "acceptance_tests_passing"], "spec": SPEC,
            "guesses": ["engagement", "receives[0]", "receives[1]"],
            "quote": "Create a new Universal Machine called Feature Count Improver.", "reason": WHY}
    return call(project, store, "declare_machine", **{**args, **over})


def test_a_title_becomes_a_valid_name_and_every_field_is_recorded_with_its_guesses(project, store):
    out = improver(project, store)
    assert out["machine"] == "feature_count_improver"
    m = layer(project)["machines"]["feature_count_improver"]
    assert m["state"] == "proposed" and m["provenance"]["author"] == "owner" and m["kind"] == "universal" and m["managed"] == "orchestra"
    assert m["engagement"] == {"mode": "scheduled", "at": "09:00", "tz": "America/Los_Angeles", "missed": "skip"}
    assert m["receives"] == ["session.settled", "task.reported"] and m["emits"] == ["work.improvement_proposed"]
    assert m["spec"] == SPEC and m["guessed"] == ["engagement", "receives[0]", "receives[1]"]
    assert any("turn(s) a day" in line and "costs nothing" in line for line in out["said"])        # the cost line
    assert any("no listener yet for work.improvement_proposed" in line for line in out["said"])
    draft = Path(out["draft"])
    assert (draft / "machine.toml").read_text().count('model = "haiku"') == 1 and "Never" in (draft / "role.md").read_text()


def test_a_universal_declaration_says_nothing_in_ai_terms(project, store):
    with pytest.raises(StoreError, match="not described in AI terms.*haiku"):
        improver(project, store, spec={**SPEC, "steps": ["Ask haiku to think"]})
    with pytest.raises(StoreError, match="AI terms.*create_ticket"):
        improver(project, store, description="Calls create_ticket every day")
    with pytest.raises(StoreError, match="spec has invariants, steps, uses; not command"):
        improver(project, store, spec={**SPEC, "command": "python x.py"})
    assert layer(project)["machines"] == {}


def test_programmatic_fields_follow_the_mode_and_an_external_one_has_its_source(project, store):
    say(store, "a nightly Jenkins job", 1)
    call(project, store, "declare_machine", name="nightly_build", kind="programmatic", description="Builds main every night",
         managed="external", engagement={"mode": "scheduled", "at": "02:00"}, emits=["build.finished"],
         spec={"trigger": "https://jenkins.example/job/nightly/build", "exit": "0 when green", "source": "bot.jenkins"},
         reason=WHY)
    call(project, store, "declare_machine", name="task_feed", kind="programmatic", description="Serves tasks",
         engagement={"mode": "continuous"}, spec={"command": "python feed.py", "protocol": "http",
                                                  "receive": "a poll: getTasks() every 10 s"}, reason=WHY)
    L = layer(project)["machines"]
    assert L["nightly_build"]["managed"] == "external" and L["nightly_build"]["spec"]["source"] == "bot.jenkins"
    assert L["task_feed"]["spec"]["protocol"] == "http" and L["task_feed"]["state"] == "proposed"
    with pytest.raises(StoreError, match="protocol is one of"):
        call(project, store, "declare_machine", name="bad_feed", kind="programmatic", description="x",
             engagement={"mode": "continuous"}, spec={"protocol": "soap"}, reason=WHY)
    with pytest.raises(StoreError, match="trigger.*external"):
        call(project, store, "declare_machine", name="bad_job", kind="programmatic", description="x",
             engagement={"mode": "ad hoc"}, spec={"trigger": "https://x"}, reason=WHY)
    with pytest.raises(StoreError, match="bus source"):
        call(project, store, "declare_machine", name="!!!", title="!!!", kind="programmatic", description="x", reason=WHY)


def test_six_edits_record_six_changes_with_previous_values_and_a_failing_edit_applies_none(project, store):
    improver(project, store)
    say(store, TURN4, 4)
    steps = ["Read changes.requested, gate.rejected and gate.landed since the last run"] + STEPS[1:]
    edits = [{"op": "add_receive", "type": "changes.requested"}, {"op": "add_receive", "type": "gate.rejected"},
             {"op": "add_receive", "type": "gate.landed"}, {"op": "remove_receive", "type": "session.settled"},
             {"op": "remove_receive", "type": "task.reported"}, {"op": "rewrite_steps", "steps": steps}]
    with pytest.raises(StoreError, match="edit 2.*no edit was applied"):
        call(project, store, "refine_machine", name="feature_count_improver", reason=WHY, quote="watch the ticket queue",
             edits=[edits[0], {"op": "remove_receive", "type": "nope.never"}])
    assert layer(project)["machines"]["feature_count_improver"]["receives"] == ["session.settled", "task.reported"]
    out = call(project, store, "refine_machine", name="feature_count_improver", edits=edits, reason=WHY,
               quote="watch the ticket queue for DONE and REJECTED tickets, and the Merge Gate")
    assert len(out["changes"]) == 6 and not out["pending"]
    L = layer(project)
    m = L["machines"]["feature_count_improver"]
    assert m["receives"] == ["changes.requested", "gate.rejected", "gate.landed"] and m["spec"]["steps"] == steps
    revs = [c for c in changes(project) if c["op"] == "revise_machine"]
    assert len(revs) == 6 and revs[3]["remove"] == {"receives": ["session.settled"]}
    assert m["latest"]["before"] == {"steps": STEPS}                 # the latest change, with what it replaced
    assert m["guessed"] == ["engagement"]                          # the receives are the owner's now
    say(store, TURN5, 5)
    call(project, store, "refine_machine", name="feature_count_improver", reason=WHY, quote="a scheduled task for 12am every day",
         edits=[{"op": "set_engagement", "mode": "scheduled", "at": "00:00", "tz": "America/Los_Angeles"}])
    m = layer(project)["machines"]["feature_count_improver"]
    assert m["engagement"]["at"] == "00:00" and m["latest"]["before"]["engagement"]["at"] == "09:00"


def test_an_unquoted_refinement_or_one_on_a_confirmed_machine_is_a_proposal(project, store):
    improver(project, store)
    out = call(project, store, "refine_machine", name="feature_count_improver", reason=WHY,
               edits=[{"op": "add_invariant", "text": "never more than one proposal a day"}])
    m = layer(project)["machines"]["feature_count_improver"]
    assert out["pending"] and len(m["pending"]) == 1 and len(m["spec"]["invariants"]) == 3


def test_the_expected_effect_waits_for_a_series_and_is_published_only_at_confirm(project, store):
    say(store, "I'm going to create a Features stock.", 1)
    call(project, store, "declare_stock", name="Features", quote="create a Features stock", reason=WHY)
    improver(project, store)
    with pytest.raises(StoreError, match="expected edit is"):
        call(project, store, "declare_expected_effect", machine="feature_count_improver", expect=[{"stock": "features"}],
             horizon="3d", reason=WHY)
    out = call(project, store, "declare_expected_effect", machine="feature_count_improver", horizon="1d", reason=WHY, expect=[
        {"flow": "tasks_done_rate", "factor": 1.2}, {"flow": "gate_rejected_rate", "factor": 0.8},
        {"from": None, "to": "coordinator_queue", "appears": True}, {"stock": "features", "rises": True}])
    assert any("raised to 3 days" in line for line in out["said"]) and any("waits for a series" in line for line in out["said"])
    eff = layer(project)["machines"]["feature_count_improver"]["expected_effect"]
    assert eff["horizon_s"] == 3 * 86_400 and len(eff["edits"]) == 4
    from orchestra_agents.design_machines import publishable
    assert [publishable(layer(project), e) is not None for e in eff["edits"]] == [True, True, True, False]
    made = [e for e in store.events.read(0, 500)[0] if e["type"] == "orchestra.prediction.made"]
    assert made == []                                                  # a draft: nothing published before confirm


def test_the_owners_request_wakes_the_supervisor_which_confirms_and_the_first_turn_announces_the_change(project, store):
    improver(project, store)
    call(project, store, "declare_expected_effect", machine="feature_count_improver", horizon="3d", reason=WHY,
         expect=[{"flow": "tasks_done_rate", "factor": 1.2}])
    ev = store.events.publish("orchestra.intent.requested", {"control": "confirm", "part": "machine", "id": "feature_count_improver",
                                                             "change": None, "from": "Machines page"},
                              source="owner", subject="machine:feature_count_improver")
    flat = next(e for e in store.events.read(0, 500)[0] if e["id"] == ev["id"])
    said = design_confirm.wake_reason(flat)
    assert f"request={ev['id']}" in said and "confirm with element machine:feature_count_improver" in said
    say(store, "", 6)                                                  # a wake-up: no owner's message
    with pytest.raises(StoreError, match="owner's"):
        call(project, store, "confirm", element="machine:feature_count_improver", reason=WHY)
    out = call(project, store, "confirm", element="machine:feature_count_improver", request=ev["id"], reason=WHY)
    m = layer(project)["machines"]["feature_count_improver"]
    assert m["state"] == "declared" and m["expected_effect"]["prediction"] == out["prediction"]
    assert m["provenance"]["author"] == "owner" and m["confirmed"]["by"] == "supervisor"     # the request backs it
    spec = project.state_dir / "machines" / "feature_count_improver"
    assert (spec / "role.md").exists() and not (project.state_dir / "drafts" / "feature_count_improver").exists()
    made = [e for e in store.events.read(0, 500)[0] if e["type"] == "orchestra.prediction.made"]
    assert len(made) == 1 and made[0]["source"] == "supervisor" and made[0]["subject"] == "machine:feature_count_improver"
    assert made[0]["change"] == out["change"] and made[0]["horizon_s"] == 3 * 86_400
    assert design_confirm.announce_start(store, "feature_count_improver") == out["change"]
    started = [e for e in store.events.read(0, 500)[0] if e["type"] == "system.changed"]
    assert started[-1]["id"] == out["change"] and started[-1]["source"] == "feature_count_improver"
    assert design_confirm.announce_start(store, "feature_count_improver") is None       # once
    with pytest.raises(StoreError, match="no request"):
        call(project, store, "discard", element="machine:feature_count_improver", request="not-an-event", reason=WHY)


def test_a_confirmed_machine_takes_refinements_as_pending_changes_the_owner_answers(project, store):
    improver(project, store)
    say(store, "Confirm it.", 6)
    call(project, store, "confirm", element="machine:feature_count_improver", quote="Confirm it.", reason=WHY)
    say(store, "Also watch gate.landed.", 7)
    out = call(project, store, "refine_machine", name="feature_count_improver", quote="Also watch gate.landed.", reason=WHY,
               edits=[{"op": "add_receive", "type": "gate.landed"}])
    assert out["pending"]
    m = layer(project)["machines"]["feature_count_improver"]
    assert "gate.landed" not in m["receives"] and len(m["pending"]) == 1
    say(store, "Yes.", 8)
    call(project, store, "confirm", element="machine:feature_count_improver", change=m["pending"][0]["change"], quote="Yes.",
         reason=WHY)
    assert "gate.landed" in layer(project)["machines"]["feature_count_improver"]["receives"]


def test_discard_leaves_no_file_but_the_history(project, store):
    improver(project, store)
    say(store, "No, drop it.", 6)
    call(project, store, "discard", element="machine:feature_count_improver", quote="drop it", reason=WHY)
    assert "feature_count_improver" not in layer(project)["machines"]
    assert not (project.state_dir / "drafts" / "feature_count_improver").exists()
    assert not (project.state_dir / "machines" / "feature_count_improver").exists()
    assert any(c["op"] == "propose_machine" for c in changes(project))


def test_a_refinement_round_proposes_three_findings_once_and_declares_nothing(project, store):
    say(store, "map", 1)
    call(project, store, "declare_machine", name="improver", kind="universal", description="Improves delivery",
         engagement={"mode": "continuous"}, receives=["session.settled"], emits=["work.improvement_proposed"], spec={}, reason=WHY)
    call(project, store, "declare_expected_effect", machine="improver", horizon="1d", reason=WHY,
         expect=[{"flow": "done_rate", "factor": 1.1}])
    call(project, store, "declare_event", type="work.improvement_proposed", purpose="an idea that would improve delivery", reason=WHY)
    call(project, store, "declare_event", type="work.idea_raised", purpose="an idea that would improve delivery", reason=WHY)
    call(project, store, "declare_event", type="work.flagged", purpose="a ticket is late or a test is flaky", reason=WHY)
    got = intended_api.layer(project).read()
    L = {**got["layer"], "version": got["version"]}
    now = time.time()
    from datetime import UTC, datetime
    busy = [{"type": "session.settled", "ts": datetime.fromtimestamp(now - 60, UTC).isoformat()}] * 250
    heard = [{"type": "orchestra.machine.started", "listens_for": ["session.settled"]}]
    r = design_passes.round_of(L, busy + heard, {}, passes=3, now=now)
    kinds = sorted(f["check"] for f in r["findings"])
    assert kinds == ["emitted, never heard", "engagement against load", "one name, several purposes", "one purpose, two names"]
    assert r["passes"] == 2                                            # the second pass found nothing new and the round stopped
    assert design_passes.round_of(L, busy + heard, {}, passes=3, already={f["key"] for f in r["findings"]}, now=now)["findings"] == []
    assert design_passes.round_of(L, busy + heard, {}, passes=1, now=now)["passes"] == 1
    assert intended_api.layer(project).read()["version"] == L["version"]      # it declared nothing
    store.set_agent_state("supervisor", turn={})
    first = design_passes.run(project, store, busy + heard)
    assert len(first["findings"]) == 4 and design_passes.run(project, store, busy + heard) is None   # unchanged map: no round
    assert "propose" in design_passes.reason(first, "[Review round]")


def test_the_supervisors_prompt_says_never_to_ask_the_owner_to_edit_a_file(project):
    from orchestra_agents.org import built_in_agents
    text = prompting.agent_prompt(project, built_in_agents()["supervisor"])
    assert "never ask the owner to edit a file" in text.lower() and "## Designing the system" in text
    for phrase in ("edit orchestra.toml", "add this to", "paste", "put this in", "run this command"):
        assert phrase not in text.lower(), phrase
