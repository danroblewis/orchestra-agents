"""What happens to a designed machine and its instruments after the first confirm: a kind switch
or an engagement change confirmed as a pending change, spec edits that settle together, a
prediction renewed after one interruption or two, a machine managed elsewhere or a program, the
records' reads, and a draft instrument kept, expired, half-written or never drafted. Each runs on
Orchestra's declared layer (design_fixtures)."""

import time
from datetime import UTC, datetime

import pytest
from design_fixtures import WHY, call, layer, model_with, say

from orchestra_agents import (
    design_confirm,
    design_instruments,
    design_machines,
    machine_spec,
    state_machines,
)
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore, StoreError

pytest_plugins = ["design_fixtures"]

TURN3 = "Create a new Universal Machine called Feature Count Improver."
SPEC = {"invariants": ["never changes code itself; it proposes work"], "steps": ["Read gate.landed since the last run",
                                                                                  "Publish work.improvement_proposed"],
        "uses": ["the coordinator's queue"]}
FCI = "machine:feature_count_improver"
MID = "feature_count_improver"
SCRIPT = "echo 'acceptance_tests_passing 41'\n"


def improver(project, store, mode="scheduled", **over):
    say(store, TURN3, 3)
    eng = {"mode": mode, **({"at": "00:00", "tz": "America/Los_Angeles"} if mode == "scheduled" else {})}
    args = {"name": "Feature Count Improver", "kind": "universal", "description": "Improves how many features get delivered per day",
            "receives": ["gate.landed"], "emits": ["work.improvement_proposed"], "quote": TURN3, "reason": WHY, **SPEC, **eng}
    call(project, store, "declare_machine", **{**args, **over})


def confirm(project, store, n=6, **kw):
    say(store, "Confirm it.", n)
    return call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY, **kw)


def predictions(store):
    return [e for e in store.events.read(0, 2000)[0] if e["type"] == "orchestra.prediction.made"]


def features(project, store):
    say(store, "I'm going to create a Features stock.", 1)
    call(project, store, "declare_stock", name="Features", description="what works for a user, end to end",
         quote="create a Features stock", reason=WHY)
    call(project, store, "propose_measurements", element="features", reason=WHY, options=[
        {"label": "acceptance tests passing on main", "kind": "script", "how": "CI's JUnit report", "cost": "a file a round"},
        {"label": "a simulated ramp", "kind": "simulated", "how": "a ramp", "cost": "nothing"}])


def add(project, store, option="a", name="features.acceptance", **kw):
    return call(project, store, "add_instrument", element="features", option=option, name=name,
                **({"script": SCRIPT} if not {"command", "script"} & set(kw) else {}), reason=WHY, **kw)


def entry(project, name):
    project.reload_config()
    return next((e for e in project.orchestra.config.raw.get("collect") or [] if e.get("name") == name), None)



def test_a_declared_stock_keeps_what_the_owner_says_it_means(project, store):
    features(project, store)
    assert layer(project)["stocks"]["features"]["description"] == "what works for a user, end to end"


def test_declare_machine_takes_the_kinds_fields_flat_and_the_older_nested_spec(project, store):
    improver(project, store)
    assert layer(project)["machines"][MID]["spec"] == SPEC
    say(store, "And a second one.", 4)
    call(project, store, "declare_machine", name="second_one", kind="universal", description="Another", spec=SPEC,
         steps=["Only this step"], reason=WHY)
    assert layer(project)["machines"]["second_one"]["spec"] == {**SPEC, "steps": ["Only this step"]}   # the flat one wins


def test_a_quoted_kind_switch_on_a_confirmed_machine_waits_and_once_confirmed_stops_the_agent(project, store):
    improver(project, store)
    confirm(project, store)
    assert (state_machines.root(project) / MID).exists()
    store.add_wakeup(MID, "something for it to do")
    say(store, "Make the improver a program instead.", 7)
    out = call(project, store, "switch_kind", name=MID, quote="Make the improver a program instead.", reason=WHY)
    assert out["pending"] and (state_machines.root(project) / MID).exists()                    # nothing stops before the confirm
    change = layer(project)["machines"][MID]["pending"][0]["change"]
    say(store, "Yes, confirm that change.", 8)
    out = call(project, store, "confirm", element=FCI, change=change, quote="Yes, confirm that change.", reason=WHY)
    assert layer(project)["machines"][MID]["kind"] == "programmatic"
    assert not (state_machines.root(project) / MID).exists() and not store.drain_wakeups(MID)
    asked = [e for e in store.events.read(0, 2000)[0] if e["type"] == "work.requested"]
    assert asked and out["request"] == asked[-1]["request"] and MID in asked[-1]["requirements"]
    assert any("agent is stopped" in line and "1 queued wake-up dropped" in line for line in out["said"])
    assert any("handed to the Coordinator" in line for line in out["said"])


def test_confirming_an_engagement_change_counts_the_schedule_from_the_confirm(project, store, monkeypatch):
    from orchestra_agents.host import AgentHost
    improver(project, store, mode="continuous")
    confirm(project, store)
    say(store, "Make it a scheduled task at midnight.", 7)
    out = call(project, store, "refine_machine", name=MID, mode="scheduled", at="00:00", tz="America/Los_Angeles",
               quote="Make it a scheduled task at midnight.", reason=WHY)
    assert out["pending"]
    change = layer(project)["machines"][MID]["pending"][0]["change"]
    later = time.time() + 1.5 * 86_400                                      # the owner confirms it a day and a half later
    monkeypatch.setattr(time, "time", lambda: later)
    say(store, "Confirm the schedule change.", 8)
    out = call(project, store, "confirm", element=FCI, change=change, quote="Confirm the schedule change.", reason=WHY)
    assert store.agent_state(MID)["last_at_fire"] == pytest.approx(later)
    assert any("its engagement is now scheduled: first run at 00:00 America/Los_Angeles" in line for line in out["said"])
    host = AgentHost(project, store, store.events)
    host._refresh_org(store.runtime()["agent_state"])
    host._schedule_wakeups(store.runtime()["agent_state"])
    assert not [w for w in store.runtime()["wakeups"] if w["target"] == MID]          # it waits for the next 00:00


def test_the_spec_edits_of_one_call_on_a_confirmed_machine_are_one_change(project, store):
    improver(project, store)
    confirm(project, store)
    say(store, "Add that it runs once a day, and that it may read the ticket queue.", 7)
    out = call(project, store, "refine_machine", name=MID, add_invariants=["runs once a day"],
               add_uses=["the ticket queue, read only"], quote="it runs once a day, and that it may read the ticket queue",
               reason=WHY)
    pending = layer(project)["machines"][MID]["pending"]
    assert len(pending) == 1 and len(out["changes"]) == 1
    assert any(f"the spec edits are one change, {pending[0]['change']}" in line for line in out["said"])
    say(store, "Confirm it, yes.", 8)
    call(project, store, "confirm", element=FCI, change=pending[0]["change"], quote="Confirm it, yes.", reason=WHY)
    spec = layer(project)["machines"][MID]["spec"]
    assert "runs once a day" in spec["invariants"] and "the ticket queue, read only" in spec["uses"]


def test_a_confirmed_machines_role_names_the_series_its_reads_are_recorded_as(project, store):
    features(project, store)
    add(project, store)
    improver(project, store, reads=["features"])
    confirm(project, store)
    role = (state_machines.root(project) / MID / "role.md").read_text()
    assert "- Features: read_series acceptance_tests_passing (instrument features.acceptance); not linked in the model yet" in role
    say(store, "Yes, that's Features.", 9)
    out = call(project, store, "link", declared="features", discovered="acceptance_tests_passing",
               quote="Yes, that's Features.", reason=WHY)
    role = (state_machines.root(project) / MID / "role.md").read_text()
    assert "- Features: read_series acceptance_tests_passing\n" in role and "not linked" not in role
    assert any(MID in line and "written again" in line for line in out["said"])


def test_a_machine_with_no_reads_reads_no_stock(project, store):
    improver(project, store)
    confirm(project, store)
    assert "Read no stock or effect." in (state_machines.root(project) / MID / "role.md").read_text()



def _era(project, store):
    from orchestra.events import Events as CoreEvents
    from orchestra.sd import eras
    from orchestra.sd.store import ModelStore
    store.events.publish("system.changed", {"note": "a config change"}, source="supervisor")
    every, _ = eras.sync(ModelStore.for_state(project.orchestra.state_dir), CoreEvents.for_project(project.orchestra),
                         project.orchestra.config.eras_from)
    return every[-1]


def _judged(pid, change, era):
    return {"type": "orchestra.prediction.judged", "verdict": "interrupted", "causationid": pid, "correlationid": change,
            "interrupted_by": era.id}


def test_two_interruptions_leave_what_is_left_of_the_horizon_counted_from_each_start(project, store):
    improver(project, store)
    call(project, store, "declare_expected_effect", machine=MID, horizon="7d", reason=WHY,
         expect=[{"kind": "factor", "flow": "tasks_done_rate", "factor": 1.2}])
    out = confirm(project, store)
    design_confirm.announce_start(store, MID)
    era = _era(project, store)
    design_confirm._starts(store, lambda d: d[MID].update(announced=time.time() - 86_400))      # interrupted a day in
    design_confirm.renew(project, store, _judged(out["prediction"], out["change"], era))
    first = predictions(store)[-1]
    assert first["horizon_s"] == pytest.approx(6 * 86_400, abs=60)
    design_confirm._starts(store, lambda d: d[MID]["published"][-1].update(at=time.time() - 86_400))   # and a day later again
    said = design_confirm.renew(project, store, _judged(first["id"], first["change"], era))
    assert predictions(store)[-1]["horizon_s"] == pytest.approx(5 * 86_400, abs=60) and "5 days" in said
    rec = store.find_prediction(out["change"])[1]
    assert rec["judged"] == "interrupted" and rec["renewed_as"] == first["id"]


def test_two_entries_about_one_change_are_both_renewed(project, store):
    model_with(project, {"s7": ("acceptance_tests_passing", {"instrument": "features.acceptance"}),
                         "s9": ("coordinator_queue", {})})
    say(store, "I'm going to create a Features stock.", 1)
    call(project, store, "declare_stock", name="Features", quote="create a Features stock", reason=WHY)
    improver(project, store)
    call(project, store, "declare_expected_effect", machine=MID, horizon="7d", reason=WHY,
         expect=[{"kind": "above", "stock": "features", "value": 5}, {"kind": "appears", "to": "coordinator_queue"}])
    out = confirm(project, store)
    say(store, "Yes, that's Features.", 7)
    call(project, store, "link", declared="features", discovered="acceptance_tests_passing", quote="Yes, that's Features.",
         reason=WHY)
    made = predictions(store)
    assert len(made) == 2 and made[0]["change"] == made[1]["change"] == out["change"]
    era = _era(project, store)
    for p in made:
        assert "recorded again" in design_confirm.renew(project, store, _judged(p["id"], p["change"], era))
    again = predictions(store)[2:]
    assert sorted(str(p["expect"]) for p in again) == sorted(str(p["expect"]) for p in made)


def test_a_machine_managed_elsewhere_publishes_no_change_at_confirm(project, store):
    improver(project, store, managed="external", mode="continuous")
    call(project, store, "declare_expected_effect", machine=MID, horizon="7d", reason=WHY,
         expect=[{"kind": "factor", "flow": "tasks_done_rate", "factor": 1.2}])
    out = confirm(project, store)
    assert out["prediction"] is None and not predictions(store)
    assert not [e for e in store.events.read(0, 500)[0] if e["type"] == "system.changed"]
    assert any("kept as an expectation" in line for line in out["said"])


def test_a_programs_waiting_edit_names_the_change_its_program_announces(project, store):
    model_with(project, {"s7": ("acceptance_tests_passing", {})})
    say(store, "I'm going to create a Features stock.", 1)
    call(project, store, "declare_stock", name="Features", quote="create a Features stock", reason=WHY)
    improver(project, store, kind="programmatic", mode="continuous")
    call(project, store, "declare_expected_effect", machine=MID, horizon="7d", reason=WHY,
         expect=[{"kind": "above", "stock": "features", "value": 5}])
    out = confirm(project, store)
    assert out["prediction"] is None
    say(store, "Yes, that's Features.", 7)
    linked = call(project, store, "link", declared="features", discovered="acceptance_tests_passing",
                  quote="Yes, that's Features.", reason=WHY)
    assert any(f"announce_change with change {out['change']} when its program runs" in line for line in linked["said"])



def test_the_hosts_expiry_removes_and_announces_with_a_store_that_has_no_source(project, store):
    features(project, store)
    add(project, store)
    script = project.state_dir / "instruments" / "features.acceptance.sh"
    assert script.exists() and entry(project, "features.acceptance")
    host_store = AgentStore(project, Events.for_project(project))           # as the host builds it: no source of its own
    before = len([e for e in store.events.read(0, 2000)[0] if e["type"] == "instruments.changed"])
    said = []
    gone = design_instruments.expire(project, host_store, now=time.time() + 86_401, log=said.append)
    assert gone == ["features.acceptance"] and not said
    assert entry(project, "features.acceptance") is None and not script.exists()
    assert "features.acceptance" not in design_instruments.drafts(host_store)
    opt = layer(project)["stocks"]["features"]["measure"]["options"][0]
    assert opt["instrument"] is None and opt["provenance"].startswith("removed")
    after = [e for e in store.events.read(0, 2000)[0] if e["type"] == "instruments.changed"]
    assert len(after) == before + 1 and after[-1]["source"] == "supervisor"


def test_the_owners_expect_mark_keeps_a_draft_at_its_expiry(project, store):
    features(project, store)
    add(project, store)
    say(store, "Yes, track acceptance tests passing.", 4)
    call(project, store, "mark_expected", part="stock", key="acceptance_tests_passing",
         quote="Yes, track acceptance tests passing.", reason=WHY)
    said = []
    assert design_instruments.expire(project, store, now=time.time() + 25 * 3600, log=said.append) == []
    e = entry(project, "features.acceptance")
    assert e is not None and "draft" not in e and "expires" not in e
    rec = design_instruments.drafts(store)["features.acceptance"]
    assert rec["kept"] and rec["kept_by"] == "the owner's expect mark on acceptance_tests_passing"
    assert layer(project)["stocks"]["features"]["measure"]["chosen"] == "a"
    assert any("kept" in line and "expect mark" in line for line in said)


def test_a_simulated_placeholder_is_never_marked_draft(project, store):
    features(project, store)
    out = add(project, store, option="b", name="features.sim", script="echo 'features_simulated 3'\n")
    e = entry(project, "features.sim")
    assert e["simulated"] is True and "draft" not in e and "expires" not in e and not out["draft"]
    assert design_instruments.drafts(store)["features.sim"]["until"]                 # still removed unless kept
    assert design_instruments.SIMULATED in out["said"]


def test_an_add_whose_option_cannot_be_recorded_says_the_instrument_records(project, store, monkeypatch):
    features(project, store)

    def refuse(*a, **k):
        raise StoreError("Orchestra refused op 0: for the test (HTTP 422); nothing was applied")
    monkeypatch.setattr(design_instruments, "_set_option", refuse)
    out = add(project, store)
    assert entry(project, "features.acceptance") is not None and "features.acceptance" in design_instruments.drafts(store)
    assert any("option a was not updated" in line for line in out["said"])
    assert not any("nothing was applied" in line for line in out["said"])


def test_a_keep_whose_option_cannot_be_marked_is_kept_and_marked_on_the_hosts_tick(project, store, monkeypatch):
    features(project, store)
    add(project, store)
    real = design_instruments._set_option

    def refuse(*a, **k):
        raise StoreError("Orchestra's endpoint did not answer; nothing was applied")
    monkeypatch.setattr(design_instruments, "_set_option", refuse)
    say(store, "Keep the acceptance tests one.", 2)
    out = call(project, store, "keep_instrument", name="features.acceptance", quote="Keep the acceptance tests one.", reason=WHY)
    rec = design_instruments.drafts(store)["features.acceptance"]
    assert rec["kept"] and rec["until"] is None and rec["chosen_pending"]
    assert any("is not marked chosen yet" in line for line in out["said"])
    assert design_instruments.expire(project, store, now=time.time() + 86_401) == []
    monkeypatch.setattr(design_instruments, "_set_option", real)
    said = design_instruments.retry_chosen(project, store)
    assert said and layer(project)["stocks"]["features"]["measure"]["chosen"] == "a"
    assert design_instruments.retry_chosen(project, store) == []


def test_add_instrument_reads_what_the_option_names_from_the_layer(project, store, monkeypatch):
    features(project, store)
    call(project, store, "propose_measurements", element="features", reason=WHY, options=[
        {"label": "the tickets counted", "kind": "script", "how": "a count", "cost": "nothing", "command": "echo 'features_done 7'"}])
    opt = layer(project)["stocks"]["features"]["measure"]["options"][0]
    assert opt["command"] == "echo 'features_done 7'"
    out = add(project, store, name="features.tickets", command="echo 'features_done 7'")
    assert entry(project, "features.tickets")["command"] == "echo 'features_done 7'" and out["instrument"] == "features.tickets"



def test_a_dummy_harness_turn_costs_nothing_and_a_model_turn_its_price(project, monkeypatch):
    monkeypatch.setattr(project.config, "harness", "dummy")
    assert design_machines.turn_usd(project) == 0.0
    monkeypatch.setattr(project.config, "harness", "claude_code")
    assert design_machines.turn_usd(project) == pytest.approx(0.03)


def test_events_from_a_short_history_are_scaled_to_a_day_and_said_so(store, monkeypatch):
    now = time.time()

    def iso(t):
        return datetime.fromtimestamp(t, UTC).isoformat()
    m = {"engagement": {"mode": "continuous"}, "receives": ["gate.landed"]}
    young = [{"type": "gate.landed", "ts": iso(now - 180 + i)} for i in range(10)]
    monkeypatch.setattr(design_machines, "_recent", lambda s, n=20_000: young)
    per_day, how = design_machines.runs_a_day(store, m, now=now)
    assert per_day == pytest.approx(10 * 86_400 / 180) and "10 such events in the last 3 min, about 4,800 a day" in how
    old = [{"type": "boot", "ts": iso(now - 3 * 86_400)}, *young]
    monkeypatch.setattr(design_machines, "_recent", lambda s, n=20_000: old)
    assert design_machines.runs_a_day(store, m, now=now) == (10.0, "woken by gate.landed: 10 such events in the last day")


def test_a_machine_answering_the_floors_offers_is_written_with_its_role_and_read_by_the_one_loader(project):
    m = {"id": "triager", "kind": "universal", "description": "Triages", "engagement": {"mode": "ad hoc"},
         "receives": ["offer.made", "offer.withdrawn"], "emits": ["triage.done"], "spec": {}}
    state_machines.write(project, m)
    spec = machine_spec.load_all(project)["triager"]
    assert spec.role == "triager" and spec.emits == ["triage.done"] and spec.root != machine_spec.ROOT
    state_machines.write(project, {**m, "id": "builder", "receives": ["gate.landed"], "spec": {"role": "build"}})
    assert machine_spec.load_all(project)["builder"].role == "build"
    state_machines.write(project, {**m, "id": "plain", "receives": ["gate.landed"]})
    assert machine_spec.load_all(project)["plain"].role is None
