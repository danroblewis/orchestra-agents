"""Confirm, discard and the owner's page requests (machine-design.md §2.4, §2.5; acceptance tests 14,
15): every settlement backed by the owner, a request backing only the call it names and only once,
the Universal | Programmatic switch end to end, a confirmed machine run from the state directory
with nothing committed, its expected effect published after the confirm (and once a waiting stock
is linked, and again when an era interrupts it), and the stand-in's declarations carried into
Orchestra's layer. Each runs on both declared layers (design_fixtures)."""

import json

import pytest
from design_fixtures import WHY, call, differs, git, layer, request, say

from orchestra_agents import design_confirm, intended_api, machine_spec, state_machines
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

TURN3 = "Create a new Universal Machine called Feature Count Improver."
SPEC = {"invariants": ["never changes code itself; it proposes work"], "steps": ["Read gate.landed since the last run",
                                                                                  "Publish work.improvement_proposed"],
        "uses": ["the coordinator's queue"]}
FCI = "machine:feature_count_improver"


def improver(project, store, mode="scheduled", **over):
    say(store, TURN3, 3)
    eng = {"mode": mode, **({"at": "00:00", "tz": "America/Los_Angeles"} if mode == "scheduled" else {})}
    args = {"name": "Feature Count Improver", "kind": "universal", "description": "Improves how many features get delivered per day",
            "receives": ["gate.landed"], "emits": ["work.improvement_proposed"], "spec": SPEC, "quote": TURN3, "reason": WHY, **eng}
    call(project, store, "declare_machine", **{**args, **over})


def effect(project, store, expect=None):
    call(project, store, "declare_expected_effect", machine="feature_count_improver", horizon="3d", reason=WHY,
         expect=expect or [{"flow": "tasks_done_rate", "factor": 1.2}])


def predictions(store):
    return [e for e in store.events.read(0, 1000)[0] if e["type"] == "orchestra.prediction.made"]


def test_the_owners_request_wakes_the_supervisor_which_confirms_and_runs_it_from_the_state_dir(project, store):
    improver(project, store)
    effect(project, store)
    head = git(project.repo_root, "rev-parse", "HEAD")
    rid = request(store, "confirm", FCI)
    flat = next(e for e in store.events.read(0, 500)[0] if e["id"] == rid)
    said = design_confirm.wake_reason(flat, project)
    assert f"request={rid}" in said and f"confirm with element {FCI}" in said
    say(store, "", 6)                                                  # a wake-up: no owner's message
    with pytest.raises(StoreError, match="owner's to ask"):
        call(project, store, "confirm", element=FCI, reason=WHY)
    out = call(project, store, "confirm", element=FCI, request=rid, reason=WHY)
    m = layer(project)["machines"]["feature_count_improver"]
    assert m["state"] == "declared" and m["expected_effect"]["prediction"] == out["prediction"]
    assert m["provenance"]["author"] == "owner" and m["confirmed"]["by"] == "supervisor"     # the request backs it
    d = project.state_dir / "machines" / "feature_count_improver"
    assert {p.name for p in d.iterdir()} == {"machine.toml", "role.md", "role.json", "agent.json"}
    assert not (project.state_dir / "drafts" / "feature_count_improver").exists()
    assert git(project.repo_root, "rev-parse", "HEAD") == head and not (project.repo_root / ".orchestra" / "roles").exists()
    spec = machine_spec.load(project, "feature_count_improver")
    assert spec.engagement == "scheduled" and spec.model == "haiku" and spec.emits == ["work.improvement_proposed"]
    from orchestra_agents.org_store import all_agents
    from orchestra_agents.roles import all_roles
    ag = all_agents(project)["feature_count_improver"]
    assert ag.kind == "scheduled" and ag.at == "00:00" and all_roles(project)["feature_count_improver"].tools == ["events"]
    made = predictions(store)
    assert len(made) == 1 and made[0]["source"] == "supervisor" and made[0]["subject"] == FCI
    assert made[0]["change"] == out["change"] and made[0]["horizon_s"] == 3 * 86_400
    assert design_confirm.announce_start(store, "feature_count_improver") == out["change"]
    started = [e for e in store.events.read(0, 500)[0] if e["type"] == "system.changed"]
    assert started[-1]["id"] == out["change"] and started[-1]["source"] == "feature_count_improver"
    assert design_confirm.announce_start(store, "feature_count_improver") is None       # once


def test_the_model_a_confirmed_machine_runs_on_is_its_specs(project, store):
    from orchestra_agents.host import AgentHost
    improver(project, store)
    say(store, "Confirm it.", 6)
    call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    host = AgentHost(project, store, store.events)
    host.harness.model_for = lambda name: f"model:{name}"
    assert host.model_of("feature_count_improver") == "model:haiku"


def test_a_confirmed_machine_emits_only_what_it_declared(project, store):
    from orchestra_agents.tooling import handle
    improver(project, store)
    say(store, "Confirm it.", 6)
    call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    with pytest.raises(StoreError, match="only what its declaration names"):
        handle(project, store, "emit_event", {"type": "work.other_thing"}, role="feature_count_improver")
    assert handle(project, store, "emit_event", {"type": "work.improvement_proposed"}, role="feature_count_improver")["ok"]


@pytest.mark.parametrize("what", ["confirm stock", "confirm change", "discard stock", "discard machine"])
def test_nothing_is_settled_without_the_owner(project, store, what):
    improver(project, store)
    call(project, store, "declare_effect", name="Review backlog", reason=WHY)            # the Supervisor's own proposal
    call(project, store, "refine_machine", name="feature_count_improver", add_invariants=["one a day"], reason=WHY)
    pending = layer(project)["machines"]["feature_count_improver"]["pending"][0]["change"]
    say(store, "", 7)
    verb, part = what.split()
    args = {"stock": {"element": "effect:review_backlog"}, "change": {"element": FCI, "change": pending},
            "machine": {"element": FCI}}[part]
    with pytest.raises(StoreError, match="owner's to ask"):
        call(project, store, verb, reason=WHY, **args)


def test_a_request_backs_only_the_call_it_names_and_only_once(project, store):
    improver(project, store)
    say(store, "Make a second one too.", 4)
    call(project, store, "declare_machine", name="other_machine", kind="universal", description="Another", reason=WHY)
    say(store, "", 5)
    discard_it = request(store, "discard", FCI)
    with pytest.raises(StoreError, match="asked discard on machine:feature_count_improver, not confirm"):
        call(project, store, "confirm", element=FCI, request=discard_it, reason=WHY)
    other = request(store, "confirm", "machine:other_machine")
    with pytest.raises(StoreError, match="not confirm on machine:feature_count_improver"):
        call(project, store, "confirm", element=FCI, request=other, reason=WHY)
    call(project, store, "confirm", element="other_machine", request=other, reason=WHY)    # a bare id is resolved
    with pytest.raises(StoreError, match="answered already"):
        call(project, store, "confirm", element="machine:other_machine", request=other, reason=WHY)
    with pytest.raises(StoreError, match="no request"):
        call(project, store, "discard", element=FCI, request="not-an-event", reason=WHY)


def test_a_request_from_anyone_but_the_owner_wakes_nobody(project, store):
    from orchestra_agents.host import AgentHost
    improver(project, store)
    host = AgentHost(project, store, store.events)
    host._refresh_org(store.runtime()["agent_state"])
    host._wake_at = store.events.end()
    rid = request(store, "confirm", FCI, source="bot")
    flat = next(e for e in store.events.read(0, 500)[0] if e["id"] == rid)
    assert design_confirm.wake_reason(flat, project) is None
    host._wake_on_events()
    assert not [w for w in store.runtime()["wakeups"] if w["target"] == "supervisor"]
    request(store, "confirm", FCI)
    host._wake_on_events()
    assert [w for w in store.runtime()["wakeups"] if w["target"] == "supervisor"]


def test_the_universal_programmatic_switch_works_end_to_end(project, store, layer_kind):
    from orchestra_agents.tooling import check_args
    differs(layer_kind, "coordinator item (a): Orchestra's revise.at_once keeps a request-backed revision pending")
    improver(project, store, mode="continuous")
    rid = request(store, "switch_kind", FCI)
    flat = next(e for e in store.events.read(0, 500)[0] if e["id"] == rid)
    said = design_confirm.wake_reason(flat, project)
    assert "refine_machine with name feature_count_improver, set_kind programmatic" in said and f"request {rid}" in said
    say(store, "", 6)
    args = {"name": "feature_count_improver", "set_kind": "programmatic", "request": rid, "reason": "the owner's click on the switch"}
    check_args("refine_machine", args)
    out = call(project, store, "refine_machine", **args)
    m = layer(project)["machines"]["feature_count_improver"]
    assert not out["pending"] and m["kind"] == "programmatic" and m["spec"] == {"specification": SPEC}
    back = request(store, "switch_kind", FCI)
    call(project, store, "refine_machine", name="feature_count_improver", set_kind="universal", request=back, reason=WHY)
    assert layer(project)["machines"]["feature_count_improver"]["spec"] == SPEC


def test_a_confirmed_machine_takes_refinements_as_pending_changes_that_rewrite_its_live_spec(project, store):
    improver(project, store)
    say(store, "Confirm it.", 6)
    call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    say(store, "Also watch gate.rejected please.", 7)
    out = call(project, store, "refine_machine", name="feature_count_improver", quote="Also watch gate.rejected please.",
               add_receives=["gate.rejected"], reason=WHY)
    assert out["pending"]
    m = layer(project)["machines"]["feature_count_improver"]
    assert "gate.rejected" not in m["receives"] and len(m["pending"]) == 1
    say(store, "Yes, do that change.", 8)
    out = call(project, store, "confirm", element=FCI, change=m["pending"][0]["change"], quote="Yes, do that change.", reason=WHY)
    assert "gate.rejected" in layer(project)["machines"]["feature_count_improver"]["receives"]
    assert any("no update_role or update_agent is needed" in line for line in out["said"])
    rec = json.loads((state_machines.root(project) / "feature_count_improver" / "agent.json").read_text())
    assert rec["kind"] == "scheduled" and not (project.state_dir / "drafts" / "feature_count_improver").exists()


def test_a_refused_confirm_publishes_no_prediction_and_records_no_start(project, store, monkeypatch):
    improver(project, store)
    effect(project, store)
    say(store, "Confirm it.", 6)
    real = design_confirm.apply

    def refuse(project_, store_, ops, prov):
        if ops[0]["op"] == "confirm":
            raise StoreError("Orchestra refused op 0: for the test")
        return real(project_, store_, ops, prov)
    monkeypatch.setattr(design_confirm, "apply", refuse)
    with pytest.raises(StoreError, match="for the test"):
        call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    assert predictions(store) == [] and not store.agent_state("supervisor").get("machine_starts")
    assert not state_machines.root(project).exists() or not list(state_machines.root(project).iterdir())


def test_a_waiting_edit_is_published_once_its_stock_is_linked(project, store):
    say(store, "I'm going to create a Features stock.", 1)
    call(project, store, "declare_stock", name="Features", quote="create a Features stock", reason=WHY)
    improver(project, store)
    effect(project, store, expect=[{"stock": "features", "above": 5}])
    say(store, "Confirm it.", 6)
    out = call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    assert out["prediction"] is None and predictions(store) == []
    say(store, "Yes, that's Features.", 7)
    linked = call(project, store, "link", declared="features", discovered="acceptance_tests_passing",
                  quote="Yes, that's Features.", reason=WHY)
    made = predictions(store)
    assert len(made) == 1 and made[0]["source"] == "supervisor" and made[0]["change"] == out["change"]
    assert made[0]["expect"] == [{"stock": "acceptance_tests_passing", "above": 5.0}]
    assert any("waiting edit" in line for line in linked["said"])
    assert design_confirm.publish_waiting(project, store) == []        # once


def test_an_interrupted_prediction_is_renewed_for_the_new_era(project, store):
    from orchestra.events import Events as CoreEvents
    from orchestra.sd import eras
    from orchestra.sd.store import ModelStore
    improver(project, store)
    effect(project, store)
    say(store, "Confirm it.", 6)
    out = call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    design_confirm.announce_start(store, "feature_count_improver")
    store.events.publish("system.changed", {"note": "a config change"}, source="supervisor")
    models = ModelStore.for_state(project.orchestra.state_dir)
    every, _ = eras.sync(models, CoreEvents.for_project(project.orchestra), project.orchestra.config.eras_from)
    later = every[-1]
    said = design_confirm.renew(project, store, {"type": "orchestra.prediction.judged", "verdict": "interrupted",
                                                 "correlationid": out["change"], "interrupted_by": later.id})
    made = predictions(store)
    assert len(made) == 2 and made[-1]["change"] == later.cause and "recorded again" in said
    assert layer(project)["machines"]["feature_count_improver"]["expected_effect"]["prediction"] == made[-1]["id"]


def test_discard_leaves_no_file_but_the_history(project, store):
    improver(project, store)
    say(store, "No, drop it, I changed my mind.", 6)
    call(project, store, "discard", element=FCI, quote="drop it, I changed my mind", reason=WHY)
    assert "feature_count_improver" not in layer(project)["machines"]
    assert not (project.state_dir / "drafts" / "feature_count_improver").exists()
    assert not (project.state_dir / "machines" / "feature_count_improver").exists()


def test_the_stand_ins_declarations_are_carried_into_orchestras_layer_once(project, store, layer_kind):
    if layer_kind != "core":
        pytest.skip("carrying over needs Orchestra's layer")
    from orchestra_agents.intended_standin import StandIn
    standin = StandIn(project)
    q = {"author": "owner", "source": "supervisor", "quote": "create a Features stock", "reason": WHY, "turn": "sessions/supervisor/1"}
    standin.apply([{"op": "declare_stock", "name": "Features"}], q)
    standin.apply([{"op": "declare_effect", "name": "Review backlog"}], {"author": "supervisor", "source": "supervisor", "reason": WHY})
    lay = intended_api.layer(project)
    got = lay.read()
    assert got["version"] == 2 and got["layer"]["stocks"]["features"]["provenance"]["quote"] == "create a Features stock"
    assert got["layer"]["effects"]["review_backlog"]["state"] == "proposed"
    assert not intended_api.standin_dir(project).exists() and list(project.state_dir.glob("intended.replayed.*"))
    StandIn(project).apply([{"op": "declare_stock", "name": "Bugs"}], q)
    with pytest.raises(StoreError, match="both Orchestra's declared layer and the agents' stand-in"):
        intended_api.layer(project)


def test_the_refinement_pass_runs_after_the_owner_confirms_and_says_when_it_cannot(project, store):
    from orchestra_agents import supervisor_spec
    improver(project, store)
    say(store, "Confirm it.", 6)
    out = call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    assert "no refinement pass runs: your spec has no refine_map duty" in out["said"]
    supervisor_spec.ensure(project)
    improver(project, store, name="second_improver", title="Second")
    rid = request(store, "discard", "machine:second_improver")
    out = call(project, store, "discard", element="machine:second_improver", request=rid, reason=WHY)
    assert any(line.startswith("the refinement pass found") for line in out["said"])        # e.g. no prediction for the first
