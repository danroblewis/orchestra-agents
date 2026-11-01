"""What the live scenario (A3, tests/live/transcripts/2026-11-01/) showed the design tools must make
the Supervisor look up before it acts: the store's series before a measurement (list_metrics), the
event types machines speak before naming one (read_events, capped and summarised), the model's
flows before a rate (a stock named as a flow refused), the option an instrument records chosen at
once, and the tools a session holds named in its prompt. Each runs on Orchestra's declared layer
(design_fixtures)."""

import json

import pytest
from design_fixtures import WHY, call, layer, model_with, say

from orchestra_agents import bus_reads, design_confirm, intended_api, prompting
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

ASK = "I think we need to start tracking notes files. Create a Notes Files stock."
TURN3 = "Create a new Universal Machine called Notes Improver."
SPEC = {"invariants": ["never changes code itself; it proposes work"], "steps": ["Read gate.landed since the last run"],
        "uses": ["the ticket queue, read only"]}
EXISTING = {"label": "files git tracks", "kind": "existing_series", "series": "repo_files", "how": "repo_stats",
            "cost": "nothing new"}


def notes(project, store):
    say(store, ASK)
    return call(project, store, "declare_stock", name="Notes Files", quote="Create a Notes Files stock.", reason=WHY)


def improver(project, store, **over):
    say(store, TURN3, 3)
    args = {"name": "Notes Improver", "kind": "universal", "description": "Raises how many notes files land a day",
            "mode": "scheduled", "at": "00:00", "receives": ["gate.landed"], **SPEC, "quote": TURN3, "reason": WHY}
    return call(project, store, "declare_machine", **{**args, **over})


# ---- 1. the store before a script ----

def test_propose_measurements_waits_for_list_metrics_in_the_session(project, store):
    notes(project, store)
    store.set_agent_state("supervisor", metrics_listed=None)
    with pytest.raises(StoreError, match="waits for list_metrics.*existing_series"):
        call(project, store, "propose_measurements", element="notes_files", reason=WHY, options=[EXISTING])
    assert not (layer(project)["stocks"]["notes_files"].get("measure") or {}).get("options")
    call(project, store, "list_metrics")
    out = call(project, store, "propose_measurements", element="notes_files", reason=WHY, options=[EXISTING])
    assert [o["kind"] for o in out["options"]] == ["existing_series"]


def test_a_list_from_an_earlier_session_does_not_count(project, store):
    notes(project, store)
    store.set_session("supervisor", kind="supervisor", session_file="/s/one.jsonl")
    call(project, store, "list_metrics")
    store.set_session("supervisor", session_file="/s/two.jsonl")               # the session started again
    with pytest.raises(StoreError, match="waits for list_metrics"):
        call(project, store, "propose_measurements", element="notes_files", reason=WHY, options=[EXISTING])
    call(project, store, "list_metrics")
    call(project, store, "propose_measurements", element="notes_files", reason=WHY, options=[EXISTING])


def test_an_existing_series_that_is_a_discovered_stock_is_offered_for_a_link(project, store):
    model_with(project, {"repo_files": ("repo_files", {})})
    notes(project, store)
    out = call(project, store, "propose_measurements", element="notes_files", reason=WHY, options=[EXISTING])
    assert any("repo_files is already a discovered stock" in x and "link declared=notes_files discovered=repo_files" in x
               for x in out["said"])


# ---- 4. the option an instrument records is chosen at once (test_design_dynamics covers the write) ----

def test_add_instrument_chooses_its_option(project, store):
    notes(project, store)
    call(project, store, "propose_measurements", element="notes_files", reason=WHY, options=[
        {"label": "notes files git tracks", "kind": "script", "how": "git ls-files notes", "cost": "under a second"}])
    call(project, store, "add_instrument", element="notes_files", option="a", name="notes.files", reason=WHY,
         script="echo 'notes_files_tracked 3'\n")
    assert layer(project)["stocks"]["notes_files"]["measure"]["chosen"] == "a"


# ---- 2. event types: heard, announced, declared; read_events capped ----

def test_read_events_is_capped_newest_first_and_summarised_by_type(project, store):
    for i in range(60):
        store.events.publish("gate.landed", {"ticket": f"ORC-{i}", "log": "x" * 5000, "files": list(range(50))}, source="merge_gate")
    store.events.publish("orchestra.machine.started", {"listens_for": ["gate.rejected", "review.approved"], "mode": "continuous"},
                         source="coordinator")
    out = call(project, store, "read_events")
    assert out["count"] == 61 and out["shown"] == 20 and out["events"][0]["type"] == "orchestra.machine.started"
    assert out["events"][1]["ticket"] == "ORC-59"                                    # newest first
    landed = next(r for r in out["by_type"] if r["type"] == "gate.landed")
    assert landed["count"] == 60 and landed["sources"] == ["merge_gate"] and landed["last"]
    assert {"type": "gate.rejected", "listened_for_by": ["coordinator"]} in out["announced_not_heard"]
    assert len(json.dumps(out)) < 20_000                                             # 60 events of 5 KB each, read in 20 KB
    assert "(50 items)" in json.dumps(out["events"][1]) and "5000 chars" in out["events"][1]["log"]
    assert call(project, store, "read_events", limit=500)["shown"] == 61


def test_an_announced_type_is_known_and_an_invented_one_is_named_in_the_answer(project, store):
    store.events.publish("gate.landed", {}, source="merge_gate")
    store.events.publish("orchestra.machine.started", {"listens_for": ["gate.rejected", "changes.requested"]}, source="coordinator")
    known = bus_reads.known_types(project, store)
    assert known["gate.rejected"] == "announced by coordinator" and known["gate.landed"] == "heard from merge_gate"
    out = improver(project, store, emits=["work.proposed"])
    assert any(x.startswith("emits: work.proposed is not heard") for x in out["said"])
    assert not any(x.startswith("receives:") for x in out["said"])
    say(store, "watch the merge gate and the REJECTED tickets please", 4)
    out = call(project, store, "refine_machine", name="notes_improver", quote="watch the merge gate and the REJECTED tickets",
               add_receives=["gate.rejected", "changes.requested", "ticket.rejected"], reason=WHY)
    assert layer(project)["machines"]["notes_improver"]["receives"] == ["gate.landed", "gate.rejected", "changes.requested",
                                                                        "ticket.rejected"]
    [named] = [x for x in out["said"] if x.startswith("receives:")]
    assert named.startswith("receives: ticket.rejected is not heard") and "gate.rejected" not in named


def test_a_declared_machines_emits_are_known_to_the_others_and_receives_left_empty_are_named(project, store):
    out = improver(project, store, receives=[], emits=["work.improvement_proposed"])
    assert any(x.startswith("receives is empty") for x in out["said"])
    assert bus_reads.known_types(project, store)["work.improvement_proposed"] == "emitted by the declared machine notes_improver"
    assert "work.improvement_proposed" not in bus_reads.known_types(project, store, but="notes_improver")


# ---- 3. rates are flows ----

def flows_model(project):
    model_with(project, {"repo_commits": ("repo_commits", {}), "repo_files": ("repo_files", {})},
               [("commits_landed", None, "repo_commits"), ("files_added", None, "repo_files")])


def test_a_stock_named_as_a_flow_is_refused_with_the_models_flows(project, store):
    flows_model(project)
    improver(project, store)
    with pytest.raises(StoreError, match=r"repo_commits is a stock \(a level\), not a flow.*commits_landed \(outside -> "
                                         r"repo_commits\); files_added"):
        call(project, store, "declare_expected_effect", machine="notes_improver", horizon="3d", reason=WHY,
             expect=[{"kind": "factor", "flow": "repo_commits", "factor": 1.2}])
    notes(project, store)
    with pytest.raises(StoreError, match="notes_files is a stock"):
        call(project, store, "declare_expected_effect", machine="notes_improver", horizon="3d", reason=WHY,
             expect=[{"kind": "factor", "flow": "notes_files", "factor": 1.2}])
    with pytest.raises(StoreError, match=r"repo_commit_count is not a flow of the model.*commits_landed"):   # an input, as live
        call(project, store, "declare_expected_effect", machine="notes_improver", horizon="3d", reason=WHY,
             expect=[{"kind": "factor", "flow": "repo_commit_count", "factor": 1.2}])
    call(project, store, "declare_expected_effect", machine="notes_improver", horizon="3d", reason=WHY,
         expect=[{"kind": "factor", "flow": "commits_landed", "factor": 1.2}, {"kind": "rises", "stock": "repo_files"}])


def test_confirm_refuses_an_expected_effect_that_names_a_stock_as_a_flow(project, store):
    flows_model(project)
    improver(project, store)
    intended_api.layer(project).apply([{"op": "set_expected_effect", "machine": "notes_improver",
                                        "edits": [{"flow": "repo_commits", "factor": 1.2}], "horizon_s": 7 * 86400.0}],
                                      {"author": "supervisor", "source": "supervisor", "reason": WHY})
    say(store, "Confirm it.", 6)
    with pytest.raises(StoreError, match="repo_commits is a stock.*commits_landed"):
        call(project, store, "confirm", element="machine:notes_improver", quote="Confirm it.", reason=WHY)
    assert layer(project)["machines"]["notes_improver"]["state"] == "proposed"
    assert design_confirm.starts(store) == {}


# ---- 5. the tools a session holds ----

def test_the_prompt_names_the_tools_the_session_holds_and_no_other():
    text = prompting.tools_held(["design", "observe", "model", "events"])
    assert "`list_metrics`" in text and "`link`" in text and "`declare_machine`" in text and "`read_events`" in text
    assert "add_duty" not in text.split("Your tools in this session")[1].split(".")[0]
    assert prompting.tools_held([]) == ""


def test_the_design_prompt_says_store_first_receives_flows_and_the_owners_zone():
    from orchestra_agents.prompting import PROMPTS_DIR
    text = " ".join((PROMPTS_DIR / "design.md").read_text().split())
    assert "An existing series always beats a new script" in text and "`propose_measurements` is refused until" in text
    assert "Events go in `receives`." in text and "Never put them only in `steps` or `uses`" in text
    assert "An announced type counts as heard" in text
    assert "`link` the declared stock to it in the same turn" in text and "never the stock's own key" in text
    assert "Never write UTC or the host's zone as theirs" in text and "ask in that reply which zone" in text
    assert "Your tools are the ones this session lists" in text
