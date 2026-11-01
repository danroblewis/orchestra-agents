"""The design tools of the Dynamics side (machine-design.md §2.2; acceptance tests 1, 2, 4–7, 9–11):
declared stocks with their provenance, the ways to measure one, the instrument written to the state
directory with its entry in the overlay as one act, kept or expired, and the link to a discovered
stock. The declared layer is the stand-in (design_fixtures)."""

import json
import time

import pytest
from design_fixtures import WHY, call, git, layer, say

from orchestra_agents import design_instruments, intended_api
from orchestra_agents.harness.tools import tools_for
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

ASK = "I want a feedback loop. I think we need to start tracking features. I'm going to create a Features stock."
SCRIPT = "echo 'acceptance_tests_passing 41'\n"


def features(project, store, **extra):
    say(store, ASK)
    return call(project, store, "declare_stock", name="Features", unit="features", near="DONE",
                quote="I'm going to create a Features stock.", reason=WHY, **extra)


def options(project, store):
    return call(project, store, "propose_measurements", element="features", reason=WHY, options=[
        {"label": "acceptance tests passing on main", "kind": "script", "how": "CI's JUnit report for main",
         "cost": "under a second a round; reads one file"},
        {"label": "tickets labelled feature reaching DONE", "kind": "counters", "how": "the task system's counters by label",
         "cost": "nothing new", "caveat": "counts what was planned as a feature, not what works"},
        {"label": "Jira features done", "kind": "source", "how": "the company's Jira", "cost": "1 request a round of 60/min",
         "system": "jira", "bearer_env": "JIRA_TOKEN"},
        {"label": "a simulated ramp", "kind": "simulated", "how": "a ramp", "cost": "nothing"}])


def test_every_design_tool_is_the_supervisors_needs_a_reason_and_a_true_quote(project, store):
    assert {t.name for t in tools_for(["design"])} >= {"declare_stock", "add_instrument", "declare_machine", "confirm"}
    say(store, ASK)
    with pytest.raises(StoreError, match="'design' tool group|Supervisor's alone"):
        call(project, store, "declare_stock", role="coordinator", name="Features", reason=WHY)
    with pytest.raises(StoreError, match="reason"):
        call(project, store, "declare_stock", name="Features", reason="because")
    with pytest.raises(StoreError, match="not in the owner's message"):
        call(project, store, "declare_stock", name="Features", quote="I want a Bugs stock", reason=WHY)
    assert layer(project)["stocks"] == {}                                     # nothing was recorded
    out = features(project, store)
    assert out["changes"] == ["c1.0"] and out["id"] == "features"            # the answer names the ids recorded
    assert any("Expected group" in line for line in out["said"])


def test_a_declared_stock_records_the_owner_the_quote_the_turn_and_who_placed_it(project, store):
    features(project, store)
    s = layer(project)["stocks"]["features"]
    assert s["state"] == "declared" and s["name"] == "Features" and s["note"] == "I'm going to create a Features stock."
    assert s["provenance"]["author"] == "owner" and s["provenance"]["turn"] == "sessions/supervisor/1"
    assert s["provenance"]["message"] == ASK and s["guessed"] == ["near"]     # the quote does not name DONE: placed by the Supervisor
    say(store, "Put a Bugs stock beside DONE.", 2)
    out = call(project, store, "declare_stock", name="Bugs", near="DONE", quote="beside DONE", reason=WHY)
    assert out["placed_by_owner"] and layer(project)["stocks"]["bugs"]["guessed"] == []
    out = call(project, store, "declare_effect", name="Review backlog age", reason=WHY)      # no quote: the Supervisor's proposal
    assert layer(project)["effects"]["review_backlog_age"]["state"] == "proposed"
    assert "proposed by the Supervisor" in out["said"][0]


def test_options_are_recorded_in_order_and_a_company_system_needs_approval(project, store):
    features(project, store)
    out = options(project, store)
    assert [o["key"] for o in out["options"]] == ["a", "b", "c", "d"]
    m = layer(project)["stocks"]["features"]["measure"]
    assert [o["kind"] for o in m["options"]] == ["script", "counters", "source", "simulated"]
    assert m["options"][2]["clearance"] == "needs approval" and "environment variable JIRA_TOKEN" in m["options"][2]["how"]
    assert m["options"][1]["caveat"].startswith("counts what was planned") and m["chosen"] is None
    with pytest.raises(StoreError, match="never a value"):
        call(project, store, "propose_measurements", element="features", reason=WHY, options=[
            {"label": "x", "kind": "source", "how": "x", "cost": "x", "bearer_env": "Bearer abcdefgh12345678"}])
    with pytest.raises(StoreError, match="needs approval|not cleared"):
        call(project, store, "add_instrument", element="features", option="c", name="features.jira", kind="scrape",
             url="http://127.0.0.1:1/metrics", reason=WHY)
    with pytest.raises(StoreError, match="simulated placeholder is always the last"):
        call(project, store, "propose_measurements", element="features", reason=WHY, options=[
            {"label": "ramp", "kind": "simulated", "how": "r", "cost": "0"}, {"label": "a", "kind": "script", "how": "h", "cost": "c"}])


def test_an_existing_series_is_an_option_with_no_instrument_and_no_binding(project, store):
    features(project, store)
    call(project, store, "propose_measurements", element="features", reason=WHY, options=[
        {"label": "already recorded", "kind": "existing_series", "how": "acceptance_tests_passing in the store",
         "cost": "nothing", "series": "acceptance_tests_passing"}])
    L = layer(project)
    assert L["stocks"]["features"]["measure"]["options"][0]["instrument"] is None
    assert L["stocks"]["features"]["found_as"] is None and design_instruments.drafts(store) == {}
    assert not project.orchestra.config.raw.get("collect")


def test_add_instrument_writes_the_script_to_the_state_dir_and_its_entry_to_the_overlay_as_one_act(project, store):
    features(project, store)
    options(project, store)
    before = git(project.repo_root, "rev-parse", "HEAD")
    v_before = intended_api.layer(project).read()["version"]
    out = call(project, store, "add_instrument", element="features", option="a", name="features.acceptance", kind="collect",
               script=SCRIPT, every="15m", reason=WHY)
    path = project.state_dir / "instruments" / "features.acceptance.sh"
    assert out["script"] == str(path) and path.read_text().endswith(SCRIPT)
    assert out["trial"] == [{"name": "acceptance_tests_passing", "labels": {}, "value": 41.0}]
    entry = next(e for e in project.orchestra.config.raw["collect"] if e["name"] == "features.acceptance")
    assert entry["command"] == str(path) and entry["every"] == "15m" and "simulated" not in entry
    assert git(project.repo_root, "rev-parse", "HEAD") == before and "features.acceptance" not in (project.repo_root / "orchestra.toml").read_text()
    opt = layer(project)["stocks"]["features"]["measure"]
    assert opt["chosen"] == "a" and opt["options"][0]["instrument"] == "features.acceptance"
    assert "recording · draft" in opt["options"][0]["provenance"] and str(path) in opt["options"][0]["provenance"]   # test 6
    d = design_instruments.drafts(store)["features.acceptance"]
    assert not d["kept"] and d["until"] - d["at"] == pytest.approx(86_400)
    # test 4: choosing an option changes no other element and no other instrument
    L = layer(project)
    assert set(L["stocks"]) == {"features"} and intended_api.layer(project).read()["version"] == v_before + 1
    assert [e["name"] for e in project.orchestra.config.raw.get("collect") or []] == ["features.acceptance"]
    events = [e for e in store.events.read(0, 500)[0] if e["type"] == "instruments.changed"]
    assert events and events[-1]["target"] == "overlay"


def test_add_instrument_refuses_a_long_script_a_secret_and_a_failing_trial_writing_nothing(project, store):
    features(project, store)
    options(project, store)
    kw = {"element": "features", "option": "a", "name": "features.acceptance", "kind": "collect", "reason": WHY}
    with pytest.raises(StoreError, match="101 lines"):
        call(project, store, "add_instrument", script="echo 'x 1'\n" * 101, **kw)
    with pytest.raises(StoreError, match="secret"):
        call(project, store, "add_instrument", script="curl -H 'Authorization: Bearer abcdef0123456789' x\necho 'x 1'\n", **kw)
    with pytest.raises(StoreError, match="trial round failed"):
        call(project, store, "add_instrument", script="echo 'not a measurement'; exit 3\n", **kw)
    assert not (project.state_dir / "instruments").exists() or not list((project.state_dir / "instruments").iterdir())
    assert not project.orchestra.config.raw.get("collect")
    assert layer(project)["stocks"]["features"]["measure"]["chosen"] is None


def test_repo_hands_the_script_to_the_coordinator_and_writes_nothing(project, store):
    features(project, store)
    options(project, store)
    out = call(project, store, "add_instrument", element="features", option="a", name="features.acceptance", kind="collect",
               script=SCRIPT, where="repo", reason=WHY)
    assert out["request"].startswith("r-") and not out["written"]
    assert not project.orchestra.config.raw.get("collect")
    req = [e for e in store.events.read(0, 500)[0] if e["type"] == "work.requested"]
    assert req and "machines/features.acceptance.sh" in req[-1]["requirements"]


def test_a_kept_draft_stays_an_unkept_one_expires_with_its_script_and_entry(project, store):
    features(project, store)
    options(project, store)
    call(project, store, "add_instrument", element="features", option="a", name="features.acceptance", kind="collect",
         script=SCRIPT, every="15m", reason=WHY)
    call(project, store, "add_instrument", element="features", option="b", name="features.tickets", kind="collect",
         command="echo 'features_done 7'", reason=WHY)
    say(store, "Keep the acceptance tests one.", 2)
    call(project, store, "keep_instrument", name="features.acceptance", quote="Keep the acceptance tests one.", reason=WHY)
    gone = design_instruments.expire(project, store, now=time.time() + 86_401)
    assert gone == ["features.tickets"]
    project.reload_config()
    names = [e["name"] for e in project.orchestra.config.raw.get("collect") or []]
    assert names == ["features.acceptance"] and (project.state_dir / "instruments" / "features.acceptance.sh").exists()
    kept = layer(project)["stocks"]["features"]["measure"]["options"][0]
    assert kept["provenance"].endswith("kept")


def test_a_simulated_placeholder_is_flagged_and_removed_once_a_real_option_records(project, store):
    features(project, store)
    options(project, store)
    call(project, store, "add_instrument", element="features", option="d", name="features.sim", kind="collect",
         script="echo 'features_simulated 3'\n", reason=WHY)
    entry = next(e for e in project.orchestra.config.raw["collect"] if e["name"] == "features.sim")
    assert entry["simulated"] is True
    call(project, store, "add_instrument", element="features", option="a", name="features.acceptance", kind="collect",
         script=SCRIPT, reason=WHY)
    project.reload_config()
    assert [e["name"] for e in project.orchestra.config.raw["collect"]] == ["features.acceptance"]
    assert not (project.state_dir / "instruments" / "features.sim.sh").exists()
    assert "features.sim" not in design_instruments.drafts(store)


def test_no_credential_value_appears_in_an_answer_or_a_file(project, store):
    features(project, store)
    out = options(project, store)
    text = json.dumps(out) + json.dumps(layer(project))
    assert "JIRA_TOKEN" in text and "Bearer " not in text


def test_link_records_a_manual_binding_and_the_discovered_stock_keeps_its_name(project, store):
    features(project, store)
    say(store, "Yes, that's Features.", 2)
    out = call(project, store, "link", declared="features", discovered="acceptance_tests_passing",
               quote="Yes, that's Features.", reason=WHY)
    s = layer(project)["stocks"]["features"]
    assert s["found_as"] == "acceptance_tests_passing" and s["name"] == "Features"
    assert "origin declared" in out["said"][0] and "keeps its name" in out["said"][0]
    call(project, store, "mark_expected", part="stock", key="acceptance_tests_passing", reason=WHY)
    call(project, store, "ignore", part="flow", key="repo_files->repo_commits", reason=WHY)
    L = layer(project)
    assert "acceptance_tests_passing" in L["marks"]["expected"] and "repo_files->repo_commits" in L["marks"]["ignored"]
    call(project, store, "set_target", element="features", op="above", value=5, over="1d", reason=WHY)
    assert layer(project)["stocks"]["features"]["target"] == {"op": "above", "value": 5, "over_s": 86_400}


def test_read_design_is_open_to_any_observer_and_lists_what_was_declared(project, store):
    features(project, store)
    out = call(project, store, "read_design", role="supervisor")
    assert out["version"] == 1 and out["elements"][0]["id"] == "features" and out["kept_in"]
