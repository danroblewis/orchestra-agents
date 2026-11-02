"""What the owner asked for holds in the tools, not only in the prompt (goal task L2, the failures
seen on Haiku): a machine declared only when the turn's message asks for one, and no more than it
asks for; an existing series that is a superset or a proxy linked with its caveat before any new
instrument; a schedule that keeps every receive."""

import pytest
from design_fixtures import WHY, call, journal, layer, model_with, say

from orchestra_agents import design_asked, intended_api
from orchestra_agents.harness.tools_design import DESIGN_TOOLS
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

TURN1 = ("I want a feedback loop where once per day an agent runs and tries to improve how many notes files get added "
         "per day. I think we need to start tracking notes files. Create a Notes Files stock.")
TURN3 = "Create a new Universal Machine called Notes Improver."
TURN5 = "This has a lot of high-throughput event triggers, make it a scheduled task for 12am every day."
SPEC = {"invariants": ["never changes code itself"], "steps": ["Read gate.landed", "Publish one idea"], "uses": ["the queue"]}


def machine(project, store, name, **over):
    args = {"name": name, "kind": "universal", "description": "Improves how many notes files are added a day",
            "mode": "continuous", "receives": ["session.settled"], "reason": WHY, **SPEC}
    return call(project, store, "declare_machine", **{**args, **over})


@pytest.mark.parametrize(("message", "n"), [
    (TURN1, 0), (TURN3, 1), ("Make an agent that triages the issues.", 1), ("Add two machines: a builder and a tester.", 2),
    ("Create agents named Triage, Release Notes and Pager.", 3), ("Make a second one too.", 1), ("And another agent.", 1),
    ("Confirm it.", 0), ("I think the Notes Improver would watch the ticket queue and the merge gate.", 0)])
def test_how_many_machines_a_message_asks_for(message, n):
    assert design_asked.asked_for(message) == n


def test_a_machine_the_message_did_not_ask_for_is_refused(project, store):
    say(store, TURN1, 1)
    with pytest.raises(StoreError, match="asks for no machine"):
        machine(project, store, "notes_files_improver")
    assert layer(project)["machines"] == {}


def test_one_machine_named_is_one_declared_and_a_second_is_refused(project, store):
    say(store, TURN3, 3)
    machine(project, store, "notes_improver", quote=TURN3)
    with pytest.raises(StoreError, match=r"asks for 1 machine \(Notes Improver\), and the session's earlier messages ask for none that is not declared yet; notes_improver is declared already"):
        machine(project, store, "notes_improver_helper")
    say(store, "Add two machines: a builder and a tester.", 4)              # the next turn asks for two
    machine(project, store, "builder")
    machine(project, store, "tester")
    assert set(layer(project)["machines"]) == {"notes_improver", "builder", "tester"}


def test_a_wake_up_and_an_external_machine_have_no_limit(project, store):
    say(store, TURN3, 3)
    machine(project, store, "notes_improver", quote=TURN3)
    call(project, store, "declare_machine", name="jira_feed", kind="programmatic", description="Jira's transitions",
         managed="external", mode="continuous", emits=["issue.moved"], source="bot.jira", reason=WHY)
    store.set_agent_state("supervisor", turn={"n": 4, "id": "sessions/supervisor/4", "message": None, "at": 1.0})
    machine(project, store, "proposed_by_a_pass")
    assert {"jira_feed", "proposed_by_a_pass"} <= set(layer(project)["machines"])


def test_scheduling_never_takes_out_receives(project, store):
    say(store, TURN3, 3)
    machine(project, store, "notes_improver", quote=TURN3, receives=["session.settled", "task.reported"])
    say(store, TURN5, 5)
    with pytest.raises(StoreError, match="never takes out receives"):
        call(project, store, "refine_machine", name="notes_improver", quote="make it a scheduled task for 12am every day",
             mode="scheduled", at="00:00", remove_receives=["session.settled", "task.reported"], reason=WHY)
    m = layer(project)["machines"]["notes_improver"]
    assert m["receives"] == ["session.settled", "task.reported"] and m["engagement"]["mode"] == "continuous"
    call(project, store, "refine_machine", name="notes_improver", quote="make it a scheduled task for 12am every day",
         mode="scheduled", at="00:00", reason=WHY)
    m = layer(project)["machines"]["notes_improver"]
    assert m["receives"] == ["session.settled", "task.reported"] and m["engagement"]["at"] == "00:00"


def notes_files(project, store):
    say(store, TURN1, 1)
    call(project, store, "declare_stock", name="Notes Files", quote="Create a Notes Files stock.", reason=WHY)
    call(project, store, "propose_measurements", element="notes_files", reason=WHY, options=[
        {"label": "files git tracks", "kind": "existing_series", "series": "repo_files", "how": "repo_stats",
         "cost": "nothing new", "caveat": "every tracked file"},
        {"label": "notes files git tracks", "kind": "script", "how": "git ls-files notes | wc -l", "cost": "under a second"}])


def test_a_superset_series_is_linked_with_its_caveat_before_any_new_instrument(project, store):
    notes_files(project, store)
    with pytest.raises(StoreError, match="superset.*link it.*rejects that series"):
        call(project, store, "add_instrument", element="notes_files", option="b", name="notes_files.git",
             script="echo \"notes_files $(git ls-files notes | wc -l)\"", reason=WHY)
    out = call(project, store, "link", declared="notes_files", discovered="repo_files", reason=WHY,
               caveat="it counts every tracked file, not only notes files")
    s = layer(project)["stocks"]["notes_files"]
    assert s["found_as"] == "repo_files" and s["measure"]["chosen"] == "a"
    assert s["measure"]["options"][0]["caveat"] == "it counts every tracked file, not only notes files"
    entry = next(e for e in journal(project) if any(op.get("op") == "link" for op in e.get("sent") or e["ops"]))
    assert "Caveat: it counts every tracked file" in str(entry)                  # with the link's reason
    assert any("caveat recorded" in line and "only if they reject" in line for line in out["said"])


def test_the_owner_rejecting_the_series_lets_a_new_instrument_be_added(project, store):
    notes_files(project, store)
    say(store, "No, repo_files counts everything; count only the notes files.", 2)
    out = call(project, store, "add_instrument", element="notes_files", option="b", name="notes_files.git",
               script="echo 'notes_files 3'", quote="count only the notes files", reason=WHY)
    assert out["trial"] == [{"name": "notes_files", "labels": {}, "value": 3.0}]


def test_the_schemas_say_it():
    tools = {t.name: t for t in DESIGN_TOOLS}
    assert "caveat" in tools["link"].properties
    assert "superset" in tools["propose_measurements"].description and "rejects" in tools["propose_measurements"].description
    assert "scheduling keeps every receive" in tools["refine_machine"].properties["remove_receives"]["description"]
    assert "no more than it asks for" in tools["declare_machine"].description


def test_option_a_a_discovered_stock_is_linked_by_propose_measurements_with_its_caveat(project, store):
    model_with(project, {"repo_files": ("repo_files", {})})
    say(store, TURN1, 1)
    call(project, store, "declare_stock", name="Notes Files", quote="Create a Notes Files stock.", reason=WHY)
    out = call(project, store, "propose_measurements", element="notes_files", reason=WHY, options=[
        {"label": "files git tracks", "kind": "existing_series", "series": "repo_files", "how": "repo_stats",
         "cost": "nothing new", "caveat": "it counts every tracked file"},
        {"label": "notes files git tracks", "kind": "script", "how": "git ls-files notes | wc -l", "cost": "under a second"}])
    s = layer(project)["stocks"]["notes_files"]
    assert s["found_as"] == "repo_files" and s["measure"]["chosen"] == "a"
    assert any("linked to repo_files" in x and "caveat: it counts every tracked file" in x and "unlink=true" in x
               for x in out["said"])
    entry = next(e for e in journal(project) if any(op.get("op") == "link" for op in e.get("sent") or e["ops"]))
    assert "Caveat: it counts every tracked file" in str(entry)
    with pytest.raises(StoreError, match="the owner's to ask"):                  # the model never declines it
        call(project, store, "link", declared="notes_files", unlink=True, reason=WHY)
    say(store, "No, unlink repo_files, it counts everything.", 2)
    call(project, store, "link", declared="notes_files", unlink=True, quote="unlink repo_files, it counts everything", reason=WHY)
    s = layer(project)["stocks"]["notes_files"]
    assert not s["found_as"] and s["measure"]["chosen"] is None
    call(project, store, "propose_measurements", element="notes_files", reason=WHY, options=[
        {"label": "files git tracks", "kind": "existing_series", "series": "repo_files", "how": "repo_stats", "cost": "nothing"}])
    assert not layer(project)["stocks"]["notes_files"]["found_as"]              # declined once: not linked again


@pytest.mark.parametrize(("message", "heard"), [
    ("Confirm it. I expect notes files to rise, and the commit rate to go up 1.2 times.",
     ["expect notes files to rise, and the commit rate to go up 1.2 times"]),
    ("Confirm it, I expect fewer rejections.", ["expect fewer rejections"]),
    ("Confirm it; it should make landings 20% faster.", ["20% faster"]),
    ("Confirm it.", []), ("Confirm the Notes Files stock.", [])])
def test_the_expectations_a_message_states(message, heard):
    assert design_asked.expectations_in(message) == heard


def test_confirm_refuses_a_machine_whose_stated_expectation_is_unrecorded(project, store):
    model_with(project, {"notes_files_s": ("repo_files", {})})
    say(store, TURN3, 3)
    machine(project, store, "notes_improver", quote=TURN3)
    confirm = "Confirm it. I expect notes files to rise, and the commit rate to go up 1.2 times."
    say(store, confirm, 6)
    with pytest.raises(StoreError, match=r"states what they expect \(expect notes files to rise.*1.2 times\).*Nothing was confirmed"):
        call(project, store, "confirm", element="machine:notes_improver", quote="I expect notes files to rise", reason=WHY)
    assert layer(project)["machines"]["notes_improver"]["state"] == "proposed"
    call(project, store, "declare_expected_effect", machine="notes_improver", horizon="3d", reason=WHY,
         expect=[{"kind": "rises", "stock": "notes_files_s"}])
    call(project, store, "confirm", element="machine:notes_improver", quote="I expect notes files to rise", reason=WHY)
    assert layer(project)["machines"]["notes_improver"]["state"] == "declared"


def test_confirm_with_no_expectation_stated_is_not_held(project, store):
    say(store, TURN3, 3)
    machine(project, store, "notes_improver", quote=TURN3)
    say(store, "Confirm it.", 6)
    call(project, store, "confirm", element="machine:notes_improver", quote="Confirm it.", reason=WHY)
    assert layer(project)["machines"]["notes_improver"]["state"] == "declared"


ASKED = ("Create a machine called Review Nudger: when a pull request has waited too long for its review or for SAIL, it "
         "reminds the reviewer. Make it a scheduled task for 9am every day. Then confirm it. I expect SAILED per day ×1.2.")
CLOCK = "This machine's clock."


def test_a_machine_asked_for_in_an_earlier_turn_is_still_asked_for(project, store):
    """T2 s10: the Supervisor asked a question first; the owner's answer names no machine, and the
    machine their earlier message asked for is declared in that turn, quoting the earlier message."""
    say(store, ASKED.replace("Review Nudger", "Notes Improver"), 1)
    say(store, CLOCK, 2)
    out = machine(project, store, "notes_improver", quote="Create a machine called Notes Improver", mode="scheduled",
                  at="09:00")
    assert out.get("machine") == "notes_improver", out
    prov = layer(project)["machines"]["notes_improver"]["provenance"]
    assert prov["author"] == "owner" and prov["quote"] == "Create a machine called Notes Improver"
    assert prov["message"].startswith("Create a machine called Notes Improver") and prov["turn"] == "sessions/supervisor/2"
    with pytest.raises(StoreError, match="ask for none that is not declared yet") as e:     # owed once, declared once
        machine(project, store, "notes_improver_helper")
    assert "restate" not in str(e.value) and "never ask them to repeat it" in str(e.value)


def test_a_quote_from_no_message_of_the_session_is_refused(project, store):
    say(store, TURN3, 3)
    say(store, CLOCK, 4)
    with pytest.raises(StoreError, match="any of their messages in this session"):
        machine(project, store, "notes_improver", quote="Create a new Universal Machine called Pager")
    machine(project, store, "notes_improver", quote="Create a new Universal Machine called Notes Improver")


def test_a_reset_session_owes_nothing(project, store):
    say(store, TURN3, 3)
    store.set_agent_state("supervisor", said=None, session_machines=None)       # residents.py, on reset
    say(store, CLOCK, 4)
    with pytest.raises(StoreError, match="asks for no machine"):
        machine(project, store, "notes_improver")


def test_the_owners_name_for_a_machine_a_plugin_proposed_refines_that_one(project, store):
    """The Workflows plugin proposed review_nudger (its workflow's program runs it): the owner's
    "a machine called Review Nudger" is that one, refined and confirmed, never a second beside it."""
    intended_api.layer(project).apply(
        [{"op": "propose_machine", "id": "review_nudger", "title": "review_nudger", "kind": "programmatic",
          "description": "Named by workflow review_nudger: the program review_nudger.", "receives": ["offer.made"],
          "engagement": {"mode": "ad hoc"}}],
        {"author": "workflows", "source": "workflow review_nudger", "reason": "the definition of workflow review_nudger"})
    say(store, ASKED, 1)
    with pytest.raises(StoreError) as e:
        machine(project, store, "review_nudger_daily", title="Review Nudger", quote="Create a machine called Review Nudger")
    said = str(e.value)
    assert "review_nudger" in said and "workflow review_nudger" in said and "the program review_nudger" in said
    assert "refine_machine name review_nudger" in said and "restate" not in said
    assert set(layer(project)["machines"]) == {"review_nudger"}
    call(project, store, "refine_machine", name="review_nudger", quote="Make it a scheduled task for 9am every day",
         mode="scheduled", at="09:00", reason=WHY)
    assert layer(project)["machines"]["review_nudger"]["engagement"]["at"] == "09:00"


def test_the_owners_factor_is_recorded_as_a_waiting_draft_with_no_model(project, store):
    """T2 s10: "SAILED per day ×1.2" with no model saved yet: recorded on the flow the owner named,
    as a draft that waits for discovery, never "can't be recorded"; confirm is then not held."""
    say(store, ASKED.replace("Review Nudger", "Notes Improver"), 1)
    machine(project, store, "notes_improver", quote="Create a machine called Notes Improver")
    out = call(project, store, "declare_expected_effect", machine="notes_improver", horizon="3d", reason=WHY,
               quote="I expect SAILED per day ×1.2", expect=[{"kind": "factor", "flow": "SAILED per day", "factor": 1.2}])
    said = " ".join(out["said"])
    assert "a draft" in said and "waiting for discovery" in said and "there is no model yet" in said
    assert "SAILED per day ×1.2" in said and "can't" not in said and "cannot" not in said
    effect = layer(project)["machines"]["notes_improver"]["expected_effect"]
    assert effect["edits"] == [{"flow": "SAILED per day", "factor": 1.2}]
    design_asked.check_expectation_recorded(store, layer(project)["machines"]["notes_improver"])


NUDGER_WORDS = ("Create a machine called Review Nudger: when a pull request has waited too long for its review or for SAIL, "
                "it reminds the reviewer. It should listen for the GitHub pull request events and the SAIL check results "
                "on the bus. Make it a scheduled task for 9am every day.")


def plugin_nudger(project):
    intended_api.layer(project).apply(
        [{"op": "propose_machine", "id": "review_nudger", "title": "review_nudger", "kind": "programmatic",
          "description": "Named by workflow review_nudger: the program review_nudger.", "receives": ["offer.made"],
          "engagement": {"mode": "ad hoc"}}],
        {"author": "workflows", "source": "workflow review_nudger", "reason": "the definition of workflow review_nudger"})


def test_every_family_the_owner_names_goes_into_receives_and_the_answer_says_each_match(project, store):
    """T2b s10: "the SAIL check results on the bus" was left out of the nudger's receives (a SAILED
    label was taken to carry it) although sail.check.completed was heard from sail."""
    plugin_nudger(project)
    store.events.publish("github.pr.opened", {"pr": 18}, source="person.github.dan")
    store.events.publish("github.pr.labeled", {"pr": 18, "label": "SAILED"}, source="person.github.dan")
    store.events.publish("sail.check.completed", {"pr": 18, "conclusion": "failure"}, source="sail")
    say(store, NUDGER_WORDS, 1)
    out = call(project, store, "refine_machine", name="review_nudger", quote="listen for the GitHub pull request events",
               owner_named=[{"said": "the GitHub pull request events", "types": ["github.pr.opened", "github.pr.labeled"]},
                            {"said": "the SAIL check results on the bus"}], reason=WHY)
    said = " ".join(out["said"])
    assert ("the owner's words matched, in receives (heard on the bus): 'the GitHub pull request events' -> "
            "github.pr.opened, github.pr.labeled") in said
    assert "could not match: 'the SAIL check results on the bus' names sail.check, heard as sail.check.completed" in said
    assert set(layer(project)["machines"]["review_nudger"]["receives"]) == {"offer.made", "github.pr.opened", "github.pr.labeled"}
    out = call(project, store, "refine_machine", name="review_nudger", quote="the SAIL check results on the bus",
               owner_named=[{"said": "the SAIL check results on the bus", "types": ["sail.check.completed"]}], reason=WHY)
    assert "'the SAIL check results on the bus' -> sail.check.completed" in " ".join(out["said"])
    assert "could not match" not in " ".join(out["said"])
    assert "sail.check.completed" in layer(project)["machines"]["review_nudger"]["receives"]


def test_a_named_type_nothing_speaks_is_said_and_not_added(project, store):
    say(store, TURN3, 3)
    out = machine(project, store, "notes_improver", quote=TURN3,
                  owner_named=[{"said": "the merge gate's landings", "types": ["gate.landed_ok"]}])
    assert "'the merge gate's landings': gate.landed_ok never heard on the bus" in " ".join(out["said"])
    assert layer(project)["machines"]["notes_improver"]["receives"] == ["session.settled"]


def test_scheduling_a_machine_a_workflow_runs_says_its_run_time_is_the_workflows(project, store):
    """T2b s10: the nudger's engagement said 09:00 while its workflow started every 24h at 00:00 UTC,
    and the Supervisor told the owner it ran at 09:00."""
    plugin_nudger(project)
    say(store, NUDGER_WORDS, 1)
    out = call(project, store, "refine_machine", name="review_nudger", quote="Make it a scheduled task for 9am every day",
               mode="scheduled", at="09:00", reason=WHY)
    said = " ".join(out["said"])
    assert "review_nudger is run by workflow review_nudger" in said and "changes no run time" in said
    assert "declare that start with at and tz (declare_workflow)" in said


def test_the_design_prompt_puts_every_named_family_in_receives_and_the_clock_on_the_workflow(project):
    from orchestra_agents import prompting
    from orchestra_agents.org import built_in_agents
    text = " ".join(prompting.agent_prompt(project, built_in_agents()["supervisor"]).split())
    assert "**Every event family the owner names goes into `receives`.**" in text
    assert "give each phrase in `owner_named`" in text and "a SAILED label does not stand for `sail.check.completed`" in text
    assert "declare the workflow's `every` start with `at` (\"09:00\") and `tz`" in text
    assert "Never state a run time the declaration does not hold" in text
    schema = {t.name: t.input_schema for t in DESIGN_TOOLS}
    for name in ("declare_machine", "refine_machine"):
        named = schema[name]["properties"]["owner_named"]
        assert named["items"]["required"] == ["said"] and "the SAIL check results" in named["description"]
