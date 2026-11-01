"""The design tools of the Machines side (machine-design.md §2.3, §2.5, §3; acceptance tests 12, 13,
14, 16, 17): a machine declared with every field of its kind and its guesses, refined change by
change with what each replaced, its expected effect and its horizon, the refinement passes, and
the tools' schemas. Each runs on Orchestra's declared layer (design_fixtures)."""

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from design_fixtures import WHY, call, changes, layer, model_with, say

from orchestra_agents import (
    design_machines,
    design_passes,
    intended_api,
    prompting,
    state_machines,
    supervisor_spec,
)
from orchestra_agents.harness.tools_design import DESIGN_TOOLS
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
            "tags": ["features", "feedback loop"], "mode": "scheduled", "at": "09:00", "tz": "America/Los_Angeles", "missed": "skip",
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
    assert any("about 1 turn a day on the pi harness" in line and "it never runs" in line for line in out["said"])  # the cost
    assert any("no listener yet for work.improvement_proposed" in line for line in out["said"])


def test_the_draft_is_in_the_format_a_machine_spec_is_read_in(project, store):
    from orchestra_agents.machine_spec import parse
    out = improver(project, store)
    from pathlib import Path
    files = {p.name: p.read_text() for p in Path(out["draft"]).iterdir()}
    spec = parse("feature_count_improver", files)
    assert spec.engagement == "scheduled" and spec.model == "haiku" and spec.emits == ["work.improvement_proposed"]
    assert "Never" in files["role.md"] and '"at": "09:00"' in files["agent.json"]


def test_a_universal_declaration_says_nothing_in_ai_terms(project, store):
    with pytest.raises(StoreError, match="not described in AI terms.*haiku"):
        improver(project, store, spec={**SPEC, "steps": ["Ask haiku to think"]})
    with pytest.raises(StoreError, match="AI terms.*create_ticket"):
        improver(project, store, description="Calls create_ticket every day")
    with pytest.raises(StoreError, match="spec has invariants, steps, uses; not command"):
        improver(project, store, spec={**SPEC, "command": "python x.py"})
    assert layer(project)["machines"] == {}


def test_programmatic_fields_follow_the_mode_and_an_external_one_has_its_source(project, store):
    say(store, "Add two machines: a nightly Jenkins job, and a task feed.", 1)
    call(project, store, "declare_machine", name="nightly_build", kind="programmatic", description="Builds main every night",
         managed="external", mode="scheduled", at="02:00", emits=["build.finished"],
         spec={"trigger": "https://jenkins.example/job/nightly/build", "exit": "0 when green", "source": "bot.jenkins"},
         reason=WHY)
    call(project, store, "declare_machine", name="task_feed", kind="programmatic", description="Serves tasks",
         mode="continuous", spec={"command": "python feed.py", "protocol": "http", "receive": "a poll: getTasks() every 10 s"},
         reason=WHY)
    L = layer(project)["machines"]
    assert L["nightly_build"]["managed"] == "external" and L["nightly_build"]["spec"]["source"] == "bot.jenkins"
    assert L["task_feed"]["spec"]["protocol"] == "http" and L["task_feed"]["state"] == "proposed"
    with pytest.raises(StoreError, match="protocol is one of"):
        call(project, store, "declare_machine", name="bad_feed", kind="programmatic", description="x",
             mode="continuous", spec={"protocol": "soap"}, reason=WHY)
    with pytest.raises(StoreError, match="trigger.*external"):
        call(project, store, "declare_machine", name="bad_job", kind="programmatic", description="x",
             mode="ad hoc", spec={"trigger": "https://x"}, reason=WHY)
    with pytest.raises(StoreError, match="bus source"):
        call(project, store, "declare_machine", name="!!!", title="!!!", kind="programmatic", description="x", reason=WHY)


def test_six_changes_record_six_with_previous_values_and_a_failing_one_applies_none(project, store):
    improver(project, store)
    say(store, TURN4, 4)
    steps = ["Read changes.requested, gate.rejected and gate.landed since the last run"] + STEPS[1:]
    quote = "watch the ticket queue for DONE and REJECTED tickets, and the Merge Gate"
    with pytest.raises(StoreError, match="has no nope.never"):
        call(project, store, "refine_machine", name="feature_count_improver", reason=WHY, quote=quote,
             add_receives=["changes.requested"], remove_receives=["nope.never"])
    assert layer(project)["machines"]["feature_count_improver"]["receives"] == ["session.settled", "task.reported"]
    out = call(project, store, "refine_machine", name="feature_count_improver", reason=WHY, quote=quote,
               add_receives=["changes.requested", "gate.rejected", "gate.landed"],
               remove_receives=["session.settled", "task.reported"], steps=steps)
    assert len(out["changes"]) == 6 and not out["pending"]
    m = layer(project)["machines"]["feature_count_improver"]
    assert m["receives"] == ["changes.requested", "gate.rejected", "gate.landed"] and m["spec"]["steps"] == steps
    revs = [c for c in changes(project) if c["op"] == "revise_machine"]
    assert len(revs) == 6 and revs[3]["remove"] == {"receives": ["session.settled"]}
    assert m["latest"]["before"] == {"steps": STEPS}                 # the latest change, with what it replaced
    assert m["guessed"] == ["engagement"]                          # the receives are the owner's now
    say(store, TURN5, 5)
    call(project, store, "refine_machine", name="feature_count_improver", reason=WHY, quote="a scheduled task for 12am every day",
         mode="scheduled", at="00:00", tz="America/Los_Angeles")
    m = layer(project)["machines"]["feature_count_improver"]
    assert m["engagement"]["at"] == "00:00" and m["latest"]["before"]["engagement"]["at"] == "09:00"


def test_an_unquoted_refinement_is_a_proposal(project, store):
    improver(project, store)
    out = call(project, store, "refine_machine", name="feature_count_improver", reason=WHY,
               add_invariants=["never more than one proposal a day"])
    m = layer(project)["machines"]["feature_count_improver"]
    assert out["pending"] and len(m["pending"]) == 1 and len(m["spec"]["invariants"]) == 3


def test_the_expected_effect_waits_for_a_series_and_is_a_draft_until_confirm(project, store):
    model_with(project, {"s3": ("coordinator_queue", {})})
    say(store, "I'm going to create a Features stock.", 1)
    call(project, store, "declare_stock", name="Features", quote="create a Features stock", reason=WHY)
    improver(project, store)
    with pytest.raises(StoreError, match="kind rises needs stock"):
        call(project, store, "declare_expected_effect", machine="feature_count_improver", expect=[{"kind": "rises"}],
             horizon="3d", reason=WHY)
    with pytest.raises(StoreError, match="kind factor needs flow and factor"):
        call(project, store, "declare_expected_effect", machine="feature_count_improver", expect=[{"kind": "factor", "flow": "x"}],
             horizon="3d", reason=WHY)
    out = call(project, store, "declare_expected_effect", machine="feature_count_improver", horizon="1d", reason=WHY, expect=[
        {"kind": "factor", "flow": "tasks_done_rate", "factor": 1.2}, {"kind": "factor", "flow": "gate_rejected_rate", "factor": 0.8},
        {"kind": "appears", "to": "coordinator_queue"}, {"kind": "rises", "stock": "features"},
        {"kind": "appears", "to": "features"}])
    assert any("raised to 3 days" in line for line in out["said"])
    assert any("Features is not linked to a discovered stock yet: published once you say which one it is (link)" in line
               for line in out["said"])
    eff = layer(project)["machines"]["feature_count_improver"]["expected_effect"]
    assert eff["horizon_s"] == 3 * 86_400 and len(eff["edits"]) == 5
    got = [design_machines.publishable(project, layer(project), "feature_count_improver", e) for e in eff["edits"]]
    assert [p is not None for p, _ in got] == [True, True, True, False, False]
    assert got[2][0] == {"from": None, "to": "s3", "appears": True} and got[3][1] == "waits for a series"
    made = [e for e in store.events.read(0, 500)[0] if e["type"] == "orchestra.prediction.made"]
    assert made == []                                                  # a draft: nothing published before confirm


def test_a_linked_end_is_published_as_the_id_of_the_stock_it_was_linked_to(project, store):
    model_with(project, {"s1": ("acceptance_tests_passing", {"instrument": "features.acceptance", "suite": "acceptance"})})
    say(store, "I'm going to create a Features stock.", 1)
    call(project, store, "declare_stock", name="Features", quote="create a Features stock", reason=WHY)
    edit = {"from": None, "to": "features", "appears": True}
    assert design_machines.publishable(project, layer(project), "m", edit) == (None, "waits for a series")
    say(store, "Yes, that's Features.", 2)
    call(project, store, "link", declared="features", discovered='acceptance_tests_passing{suite="acceptance"}',
         quote="Yes, that's Features.", reason=WHY)
    assert design_machines.publishable(project, layer(project), "m", edit)[0] == {"from": None, "to": "s1", "appears": True}


def test_the_horizon_is_three_runs_by_the_schedule_or_by_the_rate_of_what_wakes_it(project, store):
    improver(project, store)
    m = layer(project)["machines"]["feature_count_improver"]
    assert design_machines.min_horizon(project, store, m)[0] == 3 * 86_400        # once a day at 09:00
    for _ in range(6):                                                              # six a day: three runs take half a day
        store.events.publish("session.settled", {}, source="worker/1")
    busy = {**m, "engagement": {"mode": "continuous"}}
    h, why = design_machines.min_horizon(project, store, busy)               # a bus seconds old: six scaled to a day
    per_day = design_machines.runs_a_day(store, busy)[0]
    assert per_day > 6 and h == pytest.approx(max(3 * 86_400 / per_day, intended_api.min_era_s(project.orchestra.config)))
    assert "6 such events in the last" in why and "a day" in why
    quiet = {**busy, "receives": ["nothing.ever"]}
    assert design_machines.min_horizon(project, store, quiet)[1].startswith("what discovery needs")


def test_a_refinement_round_proposes_its_findings_once_and_declares_nothing(project, store):
    say(store, "Create a machine called Improver.", 1)
    call(project, store, "declare_machine", name="improver", kind="universal", description="Improves delivery",
         mode="continuous", receives=["session.settled"], emits=["work.improvement_proposed"], reason=WHY)
    call(project, store, "declare_expected_effect", machine="improver", horizon="1d", reason=WHY,
         expect=[{"flow": "done_rate", "factor": 1.1}])
    call(project, store, "declare_event", type="work.improvement_proposed", purpose="an idea that would improve delivery", reason=WHY)
    call(project, store, "declare_event", type="work.idea_raised", purpose="an idea that would improve delivery", reason=WHY)
    call(project, store, "declare_event", type="work.flagged", purpose="a ticket is late or a test is flaky", reason=WHY)
    got = intended_api.layer(project).read()
    L = {**got["layer"], "version": got["version"]}
    now = time.time()
    busy = [{"type": "session.settled", "ts": datetime.fromtimestamp(now - 60, UTC).isoformat()}] * 250
    heard = [{"type": "orchestra.machine.started", "listens_for": ["session.settled"]}]
    r = design_passes.round_of(L, busy + heard, {}, passes=3, now=now)
    assert sorted((f["n"], f["check"]) for f in r["findings"]) == [
        (1, "emitted, never heard"), (3, "one purpose, two names"), (4, "one name, several purposes"),
        (7, "engagement against load")]
    assert r["passes"] == 1                                            # no neighbour to follow: one pass
    assert design_passes.round_of(L, busy + heard, {}, passes=3, already={f["key"] for f in r["findings"]}, now=now)["findings"] == []
    assert intended_api.layer(project).read()["version"] == L["version"]      # it declared nothing
    supervisor_spec.ensure(project)                                     # a spec of its own, with no refine_map in it
    assert supervisor_spec.load(project, "supervisor").duty("refine_map") is None
    first = design_passes.run(project, store, busy + heard)            # built in: it runs all the same
    assert len(first["findings"]) == 4 and design_passes.run(project, store, busy + heard) is None   # unchanged map: no round
    assert "propose" in design_passes.reason(first, "[Review round]") and "heuristic" in design_passes.reason(first, "x")


def test_later_passes_follow_the_neighbours_and_stop_at_design_passes(project, store):
    say(store, "Create machines named A Maker, B Relay, C End and D Tail.", 1)
    for name, receives, emits in (("a_maker", [], ["work.a"]), ("b_relay", ["work.a"], ["work.b"]), ("c_end", ["work.b"], ["work.c"]),
                                  ("d_tail", ["work.c"], [])):
        call(project, store, "declare_machine", name=name, kind="universal", description="x", mode="ad hoc", receives=receives,
             emits=emits, reason=WHY)
    got = intended_api.layer(project).read()
    L = {**got["layer"], "version": got["version"]}
    r = design_passes.round_of(L, [], {}, passes=3, focus={"a_maker"})
    six = [f["key"] for f in r["findings"] if f["n"] == 6]
    assert six == ["neighbour:b_relay:a_maker", "neighbour:a_maker:b_relay", "neighbour:c_end:b_relay",
                   "neighbour:b_relay:c_end", "neighbour:d_tail:c_end"]
    assert r["passes"] == 3                                            # stopped at design_passes, d_tail not followed
    assert design_passes.round_of(L, [], {}, passes=1, focus={"a_maker"})["passes"] == 1


def test_loose_ends_include_a_target_with_no_series(project, store):
    say(store, "Features should stay above five.", 1)
    call(project, store, "declare_stock", name="Features", quote="Features should stay above five.", reason=WHY)
    call(project, store, "set_target", element="features", compare="above", value=5, over="1d",
         quote="Features should stay above five.", reason=WHY)
    got = intended_api.layer(project).read()
    keys = {f["key"] for f in design_passes.round_of({**got["layer"], "version": got["version"]}, [], {}, 3)["findings"]}
    assert {"unmeasured:features", "target_unmeasured:features"} <= keys
    expected = {**got["layer"], "marks": {"expected": {"tests_passing": {}}, "ignored": {}}}
    keys = {f["key"] for f in design_passes.round_of(expected, [], {}, 3, absent=[{"x"}, {"y"}, {"z"}])["findings"]}
    assert "absent:tests_passing" in keys


def test_an_expected_flow_and_a_labelled_stock_that_are_present_are_no_loose_end(project, store):
    for _ in range(3):
        model_with(project, {"a": ("tests_passing", {"suite": "e2e", "instrument": "x"}), "b": ("tests_run", {})},
                   flows=[("f", "b", "a")])
    absent = design_passes._absent(project)
    expected = {"marks": {"expected": {'tests_passing{suite="e2e"}': {}, 'tests_run -> tests_passing{suite="e2e"}': {}},
                          "ignored": {}}}
    assert 'tests_run -> tests_passing{suite="e2e"}' in absent[-1]
    keys = {f["key"] for f in design_passes.round_of(expected, [], {}, 3, absent=absent)["findings"]}
    assert not {k for k in keys if k.startswith("absent:")}


def test_refine_machine_edits_the_lists_and_only_text_fields_by_set_field(project, store):
    improver(project, store)
    say(store, TURN4, 4)
    q = "watch the ticket queue for DONE and REJECTED tickets"
    call(project, store, "refine_machine", name="feature_count_improver", quote=q, reason=WHY,
         add_uses=["the ticket queue, read only"], remove_uses=["the coordinator's queue"], add_tags=["tickets"],
         remove_tags=["feedback loop"], set_field="title", value="Feature Improver")
    m = layer(project)["machines"]["feature_count_improver"]
    assert m["spec"]["uses"] == ["the ticket queue, read only"] and m["tags"] == ["features", "tickets"]
    assert m["title"] == "Feature Improver"
    for field in ("managed", "tags", "spec.steps"):
        with pytest.raises(StoreError, match="set_field sets a text field"):
            call(project, store, "refine_machine", name="feature_count_improver", quote=q, reason=WHY, set_field=field, value="x")


def test_the_improvers_role_has_the_tools_its_uses_need_and_reads_its_events_each_run(project, store):
    out = improver(project, store, spec={**SPEC, "uses": ["the ticket queue, read only", "the model"],
                                         "steps": ["1. Read gate.landed", "2) Publish work.improvement_proposed"]})
    files = state_machines.files(layer(project)["machines"]["feature_count_improver"])
    import json
    role = json.loads(files["role.json"])
    assert role["tools"] == ["events", "escalate", "tickets", "model"]
    text = files["role.md"]
    assert "1. Read gate.landed\n2. Publish work.improvement_proposed" in text
    assert "Each run, read these events since your last run (read_events): session.settled, task.reported." in text
    assert "change a ticket: you read the ticket queue only" in text
    agent = json.loads(files["agent.json"])
    assert agent["wake_on"] == [] and agent["reads_events"] == ["session.settled", "task.reported"]
    from orchestra_agents.listening import listens_for
    from orchestra_agents.org import AgentDef
    assert {"session.settled", "task.reported"} <= set(listens_for(AgentDef.from_dict(agent), "tasks"))
    assert not any("could act on nothing" in line for line in out["said"])
    out = improver(project, store, name="other_one", spec={**SPEC, "uses": ["the coffee machine"]})
    assert any("could act on nothing for 'the coffee machine'" in line for line in out["said"])


def test_a_state_directory_machines_prompt_is_the_supervisors_and_has_no_duty_tools(project, store):
    from orchestra_agents.machine_spec import load, prompt_part, render
    improver(project, store)
    say(store, "Confirm it.", 6)
    call(project, store, "confirm", element="machine:feature_count_improver", quote="Confirm it.", reason=WHY)
    spec = load(project, "feature_count_improver")
    part = prompt_part(spec)
    assert "kept in the state directory and never committed" in part and "add_duty" not in part
    assert "How you work a wake-up" not in part
    assert "never committed" in render(spec) and "Edited by its own duty tools" not in render(spec)


def test_the_supervisors_prompt_says_never_to_ask_the_owner_to_edit_a_file(project):
    from orchestra_agents.org import built_in_agents
    text = prompting.agent_prompt(project, built_in_agents()["supervisor"])
    assert "never ask the owner to edit a file" in text.lower() and "## Designing the system" in text
    assert "Your built-in duty `refine_map`" in text                  # every Supervisor's, whatever its spec says


def test_the_supervisor_knows_the_pages_states_and_answers_in_two_or_three_lines(project):
    """The pages' states as the Dynamics and Machines pages tag them, explained in the prompt, and
    the reply rule of machine-design.md §2.6: two or three lines, ids, where to look, no question
    back; each standard reply in the prompt is itself that short."""
    from orchestra_agents.org import built_in_agents
    text = prompting.agent_prompt(project, built_in_agents()["supervisor"])
    for state in ("**Ghost**", "**Expected**", "**New in vN**", "**Unconfirmed**", "**Measured, never heard**",
                  "**Asked the Supervisor · waiting**"):
        assert state in text, state
    flat = " ".join(text.split())
    assert "two or three lines" in flat and "never ask a question back when the request was clear" in flat
    design = (Path(prompting.__file__).parent / "prompts" / "design.md").read_text(encoding="utf-8")
    standard = design.split("The standard, one reply per owner's turn:")[1].split("**Where the owner looks.**")[0]
    replies = [r for r in standard.strip().split("\n- ") if r.strip()]
    assert len(replies) == 4
    for r in replies:
        assert "page" in r and "`" in r and "?" not in r and len(r) < 330, r


def test_every_design_schema_is_typed_all_the_way_down():
    def walk(where: str, s: dict):
        assert "type" in s or "enum" in s, f"{where} has no type"
        if s.get("type") == "object" and where.count(".") > 0:
            assert s.get("properties"), f"{where} is an untyped object"
            for k, v in s["properties"].items():
                walk(f"{where}.{k}", v)
        if s.get("type") == "array":
            walk(f"{where}[]", s.get("items") or {})
    for t in DESIGN_TOOLS:
        for k, v in t.properties.items():
            walk(f"{t.name}.{k}", v)
