"""What the owner asked for holds in the tools, not only in the prompt (goal task L2, the failures
seen on Haiku): a machine declared only when the turn's message asks for one, and no more than it
asks for; an existing series that is a superset or a proxy linked with its caveat before any new
instrument; a schedule that keeps every receive."""

import pytest
from design_fixtures import WHY, call, journal, layer, model_with, say

from orchestra_agents import design_asked
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
    with pytest.raises(StoreError, match=r"asks for 1 machine \(Notes Improver\), and notes_improver is declared already"):
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
