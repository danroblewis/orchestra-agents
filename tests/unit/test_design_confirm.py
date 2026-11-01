"""Confirm, discard and the owner's page requests (machine-design.md §2.4, §2.5; acceptance tests 14,
15): every settlement backed by the owner, a request backing only the call it names and only once,
the Universal | Programmatic switch end to end, a confirmed machine run from the state directory
with nothing committed, its expected effect published after the confirm (and once a waiting stock
is linked, and again when an era interrupts it), and the stand-in's declarations carried into
Orchestra's layer. Each runs on both declared layers (design_fixtures)."""

import json
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from design_fixtures import WHY, bare_request, call, changes, git, layer, model_with, request, say

from orchestra_agents import (
    design_confirm,
    intended_api,
    intended_carry,
    machine_spec,
    state_machines,
)
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


def confirmed(project, store, **over):
    improver(project, store, **over)
    effect(project, store)
    say(store, "Confirm it.", 6)
    return call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)


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
    bare = bare_request(store, "confirm", FCI)                         # on the bus as the owner, but never noted
    with pytest.raises(StoreError, match="an event on the bus alone backs nothing"):
        call(project, store, "confirm", element=FCI, request=bare, reason=WHY)
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
    assert ag.kind == "scheduled" and ag.at == "00:00" and ag.tz == "America/Los_Angeles"
    assert all_roles(project)["feature_count_improver"].tools == ["events", "escalate"]
    assert ag.wake_on == [] and ag.reads_events == ["gate.landed"]
    assert any("first run at 00:00 America/Los_Angeles" in line and "harness" in line for line in out["said"])
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


def test_the_universal_programmatic_switch_on_a_proposed_machine(project, store):
    from orchestra_agents.tooling import check_args
    improver(project, store, mode="continuous")
    rid = request(store, "switch_kind", FCI)
    flat = next(e for e in store.events.read(0, 500)[0] if e["id"] == rid)
    said = design_confirm.wake_reason(flat, project)
    assert "switch_kind with name feature_count_improver" in said and f"request {rid}" in said
    say(store, "", 6)
    args = {"name": "feature_count_improver", "request": rid, "reason": "the owner's click on the switch"}
    check_args("switch_kind", args)
    out = call(project, store, "switch_kind", **args)
    m = layer(project)["machines"]["feature_count_improver"]
    assert not out["pending"] and m["kind"] == "programmatic" and m["spec"] == SPEC          # kind alone: the spec is kept
    assert [c for c in changes(project) if c["op"] == "revise_machine"][-1]["set"] == {"kind": "programmatic"}
    assert not (project.state_dir / "drafts" / "feature_count_improver").exists()
    back = request(store, "switch_kind", FCI)
    call(project, store, "switch_kind", name="feature_count_improver", request=back, reason=WHY)
    m = layer(project)["machines"]["feature_count_improver"]
    assert m["kind"] == "universal" and m["spec"] == SPEC and (project.state_dir / "drafts" / "feature_count_improver").exists()
    with pytest.raises(StoreError, match="owner's to ask"):
        call(project, store, "switch_kind", name="feature_count_improver", reason=WHY)


def test_switching_a_confirmed_machine_to_programmatic_stops_its_agent_and_hands_the_program_on(project, store):
    from orchestra_agents.org_store import all_agents
    confirmed(project, store)
    assert "feature_count_improver" in all_agents(project)
    rid = request(store, "switch_kind", FCI)
    say(store, "", 7)
    out = call(project, store, "switch_kind", name="feature_count_improver", request=rid, reason=WHY)
    m = layer(project)["machines"]["feature_count_improver"]
    assert not out["pending"] and m["kind"] == "programmatic" and m["state"] == "declared"
    assert not (state_machines.root(project) / "feature_count_improver").exists() and "feature_count_improver" not in all_agents(project)
    asked = [e for e in store.events.read(0, 500)[0] if e["type"] == "work.requested"]
    assert asked and asked[-1]["request"] == out["request"] and "feature_count_improver" in asked[-1]["requirements"]
    assert any("agent is stopped" in line for line in out["said"]) and any(out["request"] in line for line in out["said"])


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


def test_a_waiting_edit_is_published_once_its_stock_is_linked_with_the_eras_ids(project, store):
    model_with(project, {"s7": ("acceptance_tests_passing", {"instrument": "features.acceptance"}),
                         "s9": ("coordinator_queue", {})})                      # keys and ids differ
    say(store, "I'm going to create a Features stock.", 1)
    call(project, store, "declare_stock", name="Features", quote="create a Features stock", reason=WHY)
    improver(project, store)
    effect(project, store, expect=[{"kind": "above", "stock": "features", "value": 5},
                                   {"kind": "appears", "to": "coordinator_queue"}])
    say(store, "Confirm it.", 6)
    out = call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    made = predictions(store)
    assert len(made) == 1 and made[0]["expect"] == [{"from": None, "to": "s9", "appears": True}]       # the id, not the key
    assert any("Features is not linked to a discovered stock yet" in line for line in out["said"])
    say(store, "Yes, that's Features.", 7)
    linked = call(project, store, "link", declared="features", discovered="acceptance_tests_passing",
                  quote="Yes, that's Features.", reason=WHY)
    made = predictions(store)
    assert len(made) == 2 and made[1]["source"] == "supervisor" and made[1]["change"] == out["change"]
    assert made[1]["expect"] == [{"stock": "s7", "above": 5.0}]
    assert any("waiting edit" in line for line in linked["said"])
    assert design_confirm.publish_waiting(project, store) == []        # once
    assert design_confirm.starts(store)["feature_count_improver"]["waiting"] == 0


def test_an_edit_into_a_key_the_model_does_not_have_waits_and_says_why(project, store, monkeypatch):
    improver(project, store)
    out = call(project, store, "declare_expected_effect", machine="feature_count_improver", horizon="3d", reason=WHY,
               expect=[{"kind": "appears", "to": "coordinator_queue"}])
    assert any("coordinator_queue is not in the current model" in line for line in out["said"])
    design = intended_api.layer(project).design()
    assert [i["why"] for i in design["open"] if i["what"] == "unpublishable draft"] == ["not in the model"]
    say(store, "Confirm it.", 6)
    assert call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)["prediction"] is None
    calls, real = [], intended_api.current
    monkeypatch.setattr(intended_api, "current", lambda p: calls.append(1) or real(p))
    assert design_confirm.publish_waiting(project, store) == [] and calls              # one edit waits: it looks
    design_confirm._starts(store, lambda d: d["feature_count_improver"].update(waiting=0))
    calls.clear()
    assert design_confirm.publish_waiting(project, store) == [] and not calls           # none waits: nothing is read


def test_an_interrupted_prediction_is_renewed_for_the_new_era(project, store):
    from orchestra.events import Events as CoreEvents
    from orchestra.sd import eras
    from orchestra.sd.store import ModelStore
    improver(project, store)
    call(project, store, "declare_expected_effect", machine="feature_count_improver", horizon="7d", reason=WHY,
         expect=[{"kind": "factor", "flow": "tasks_done_rate", "factor": 1.2}])
    say(store, "Confirm it.", 6)
    out = call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    design_confirm.announce_start(store, "feature_count_improver")
    store.events.publish("system.changed", {"note": "a config change"}, source="supervisor")
    models = ModelStore.for_state(project.orchestra.state_dir)
    every, _ = eras.sync(models, CoreEvents.for_project(project.orchestra), project.orchestra.config.eras_from)
    later = every[-1]
    design_confirm._starts(store, lambda d: d["feature_count_improver"].update(announced=time.time() - 86_400))   # a day in
    said = design_confirm.renew(project, store, {"type": "orchestra.prediction.judged", "verdict": "interrupted",
                                                 "correlationid": out["change"], "interrupted_by": later.id})
    made = predictions(store)
    assert len(made) == 2 and made[-1]["change"] == later.cause and "recorded again" in said
    assert made[-1]["horizon_s"] == pytest.approx(6 * 86_400, abs=60) and "what is left of its horizon: 6 days" in said
    rec = design_confirm.starts(store)["feature_count_improver"]
    design_confirm._starts(store, lambda d: d["feature_count_improver"].update(announced=time.time() - 6.5 * 86_400))
    said = design_confirm.renew(project, store, {"type": "orchestra.prediction.judged", "verdict": "interrupted",
                                                 "correlationid": later.cause, "interrupted_by": later.id})
    assert predictions(store)[-1]["horizon_s"] == pytest.approx(3 * 86_400) and "raised to what one needs" in said   # 3 runs
    assert rec["published"]
    assert layer(project)["machines"]["feature_count_improver"]["expected_effect"]["prediction"] == predictions(store)[-1]["id"]


@pytest.mark.parametrize("verdict", ["passed", "no effect"])
def test_a_verdict_on_a_designed_machines_prediction_wakes_the_supervisor(project, store, verdict):
    from orchestra_agents.host import AgentHost
    out = confirmed(project, store)
    host = AgentHost(project, store, store.events)
    host._refresh_org(store.runtime()["agent_state"])
    host.verdicts.see({"type": "orchestra.prediction.judged", "verdict": verdict, "correlationid": out["change"]})
    woke = [w["reason"] for w in store.runtime()["wakeups"] if w["target"] == "supervisor"]
    assert woke and f"judged: {verdict}" in woke[-1] and FCI in woke[-1]


def test_a_daily_machine_confirmed_after_its_time_waits_for_its_next_time(project, store, monkeypatch):
    from orchestra_agents.host import AgentHost
    zone = ZoneInfo("America/Los_Angeles")
    past = datetime.now(zone).replace(hour=0, minute=30, second=0, microsecond=0).timestamp()      # 00:30, after 00:00
    monkeypatch.setattr(time, "time", lambda: past)
    improver(project, store, missed="skip")
    say(store, "Confirm it.", 6)
    out = call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    assert any("first run at 00:00 America/Los_Angeles, tomorrow" in line for line in out["said"])
    host = AgentHost(project, store, store.events)
    host._refresh_org(store.runtime()["agent_state"])
    nxt = (datetime.fromtimestamp(past, zone) + timedelta(days=1)).replace(hour=0, minute=0).timestamp()   # 00:00 there
    for t in (past + 60, nxt - 60):                                    # all day, until just before the next 00:00 there
        monkeypatch.setattr(time, "time", lambda t=t: t)
        host._schedule_wakeups(store.runtime()["agent_state"])
        assert not [w for w in store.runtime()["wakeups"] if w["target"] == "feature_count_improver"]
    monkeypatch.setattr(time, "time", lambda: nxt + 30)
    host._schedule_wakeups(store.runtime()["agent_state"])
    assert [w for w in store.runtime()["wakeups"] if w["target"] == "feature_count_improver"]


def test_a_daily_run_missed_while_the_host_was_down_is_skipped(project, store, monkeypatch):
    from orchestra_agents.host import AgentHost
    zone = ZoneInfo("America/Los_Angeles")
    midnight = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    monkeypatch.setattr(time, "time", lambda: midnight - 3600)
    improver(project, store, missed="skip")
    say(store, "Confirm it.", 6)
    call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    host = AgentHost(project, store, store.events)
    host._refresh_org(store.runtime()["agent_state"])
    monkeypatch.setattr(time, "time", lambda: midnight + 3 * 3600)    # the host was down over 00:00
    host._schedule_wakeups(store.runtime()["agent_state"])
    assert not [w for w in store.runtime()["wakeups"] if w["target"] == "feature_count_improver"]
    tomorrow = (datetime.fromtimestamp(midnight, zone) + timedelta(days=1)).replace(hour=0).timestamp()
    assert store.agent_state("feature_count_improver")["next_scheduled_at"] == tomorrow


def test_a_machine_managed_elsewhere_starts_nothing_here_and_is_still_predicted(project, store):
    out = confirmed(project, store, managed="external")
    assert not (state_machines.root(project) / "feature_count_improver").exists()
    assert not (project.state_dir / "drafts" / "feature_count_improver").exists()
    assert any("nothing runs here" in line for line in out["said"]) and out["prediction"]
    started = [e for e in store.events.read(0, 500)[0] if e["type"] == "system.changed"]
    assert started and started[-1]["id"] == out["change"]


def test_discard_leaves_no_file_but_the_history(project, store):
    improver(project, store)
    say(store, "No, drop it, I changed my mind.", 6)
    call(project, store, "discard", element=FCI, quote="drop it, I changed my mind", reason=WHY)
    assert "feature_count_improver" not in layer(project)["machines"]
    assert not (project.state_dir / "drafts" / "feature_count_improver").exists()
    assert not (project.state_dir / "machines" / "feature_count_improver").exists()


@pytest.mark.parametrize("by", ["request", "quote"])
def test_a_confirmed_machine_is_retired_by_the_owners_discard(project, store, by):
    from orchestra_agents.org_store import all_agents
    confirmed(project, store)
    if by == "request":
        rid = request(store, "discard", FCI)
        say(store, "", 7)
        out = call(project, store, "discard", element=FCI, request=rid, reason=WHY)
    else:
        say(store, "Retire the feature count improver.", 7)
        out = call(project, store, "discard", element=FCI, quote="Retire the feature count improver.", reason=WHY)
    assert "feature_count_improver" not in layer(project)["machines"] and "retracted" in out["said"][0]
    assert not (state_machines.root(project) / "feature_count_improver").exists() and "feature_count_improver" not in all_agents(project)
    assert any(line.startswith("retired") for line in out["said"])
    assert not any("retract it" in line for line in out["said"])


def test_a_proposal_cannot_retire_what_the_owner_declared(project, store):
    confirmed(project, store)
    say(store, "", 7)
    with pytest.raises(StoreError, match="owner's to ask"):
        call(project, store, "discard", element=FCI, reason=WHY)
    assert (state_machines.root(project) / "feature_count_improver").exists()


Q = {"author": "owner", "source": "supervisor", "quote": "create a Features stock", "reason": WHY, "turn": "sessions/supervisor/1"}


def _stand_in(project, n: int = 2):
    from orchestra_agents.intended_standin import StandIn
    standin = StandIn(project)
    standin.apply([{"op": "declare_stock", "name": "Features"}], Q)
    standin.apply([{"op": "declare_effect", "name": "Review backlog"}], {"author": "supervisor", "source": "supervisor", "reason": WHY})
    if n > 2:
        standin.apply([{"op": "declare_stock", "name": "Bugs"}], Q)


def test_the_stand_ins_declarations_are_carried_into_orchestras_layer_once(project, store, layer_kind):
    if layer_kind != "core":
        pytest.skip("carrying over needs Orchestra's layer")
    _stand_in(project)
    lay = intended_api.layer(project)
    got = lay.read()
    assert got["version"] == 2 and got["layer"]["stocks"]["features"]["provenance"]["quote"] == "create a Features stock"
    assert got["layer"]["effects"]["review_backlog"]["state"] == "proposed"
    assert not intended_api.standin_dir(project).exists() and list(project.state_dir.glob("intended.replayed.*"))
    from orchestra_agents.intended_standin import StandIn
    StandIn(project).apply([{"op": "declare_stock", "name": "Bugs"}], Q)
    with pytest.raises(StoreError, match="both Orchestra's declared layer and the agents' stand-in"):
        intended_api.layer(project)
    say(store, "Drop the old ones, keep what Orchestra has.", 9)
    out = call(project, store, "carry_over", how="abandon", quote="Drop the old ones, keep what Orchestra has.", reason=WHY)
    assert "abandoned" in out["said"][0] and list(project.state_dir.glob("intended.abandoned.*"))
    assert intended_api.layer(project).read()["version"] == 2


def test_a_carry_over_that_stopped_resumes_where_it_stopped(project, store, layer_kind, monkeypatch):
    if layer_kind != "core":
        pytest.skip("carrying over needs Orchestra's layer")
    _stand_in(project, 3)
    real, n = intended_api.Core.apply, []

    def fails_once(self, ops, prov):
        n.append(1)
        if len(n) == 2:
            raise StoreError("Orchestra refused op 0: for the test")
        return real(self, ops, prov)
    monkeypatch.setattr(intended_api.Core, "apply", fails_once)
    with pytest.raises(StoreError, match="stopped at call 2"):
        intended_api.layer(project)
    assert intended_api.Core(project).read()["version"] == 1                  # call 1 is in
    lay = intended_api.layer(project)                                         # resumes from call 2: no "both hold"
    got = lay.read()
    assert got["version"] == 3 and set(got["layer"]["stocks"]) == {"features", "bugs"}
    assert not intended_api.standin_dir(project).exists()


def test_two_first_calls_at_once_carry_over_once(project, store, layer_kind):
    if layer_kind != "core":
        pytest.skip("carrying over needs Orchestra's layer")
    _stand_in(project)
    errors = []

    def first():
        try:
            intended_api.layer(project)
        except Exception as e:  # noqa: BLE001 - collected for the assertion
            errors.append(e)
    threads = [threading.Thread(target=first) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and intended_api.Core(project).read()["version"] == 2


def test_carry_over_is_the_owners_to_answer(project, store, layer_kind):
    if layer_kind != "core":
        pytest.skip("carrying over needs Orchestra's layer")
    say(store, "", 9)
    with pytest.raises(StoreError, match="quote"):
        call(project, store, "carry_over", how="finish", reason=WHY)
    assert intended_carry.carry_over(project, intended_api.Core(project)) is None   # nothing to carry


def test_the_refinement_pass_runs_after_the_owner_confirms_whatever_the_spec(project, store):
    improver(project, store)
    say(store, "Confirm it.", 6)
    out = call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    assert any(line.startswith("the refinement pass found") for line in out["said"])  # no duty in the spec needed: no prediction
    improver(project, store, name="second_improver", title="Second")
    rid = request(store, "discard", "machine:second_improver")
    out = call(project, store, "discard", element="machine:second_improver", request=rid, reason=WHY)
    assert any(line.startswith("the refinement pass") for line in out["said"])


def test_a_confirmed_daily_machine_is_announced_scheduled_and_listed_by_agents_status(project, store, capsys):
    from orchestra_agents.cli import cmd_status
    from orchestra_agents.host import AgentHost
    from orchestra_agents.org_views import agents_status
    confirmed(project, store)
    host = AgentHost(project, store, store.events)
    host._refresh_org(store.runtime()["agent_state"])
    host._announce("feature_count_improver", "feature_count_improver")
    ann = [e for e in store.events.read(0, 500)[0] if e["type"] == "orchestra.machine.started"][-1]
    assert ann["mode"] == "scheduled" and "gate.landed" in ann["listens_for"]
    row = next(a for a in agents_status(project, store)["agents"] if a["name"] == "feature_count_improver")
    assert (row["engaged"], row["at"], row["tz"]) == ("scheduled", "00:00", "America/Los_Angeles")

    class Args:
        repo = str(project.repo_root)
    cmd_status(Args())
    out = capsys.readouterr().out
    assert "feature_count_improver" in out and "daily at 00:00 America/Los_Angeles" in out
