"""The design tools of the Dynamics side (machine-design.md §2.2; acceptance tests 1, 2, 4–7, 9–11):
declared stocks with their provenance, the ways to measure one and whether one needs the owner's
approval, the instrument written to the state directory with its entry in the overlay as one act,
kept or expired, and the link to a discovered stock. Each runs on Orchestra's declared layer
(design_fixtures)."""

import json
import time

import pytest
from design_fixtures import WHY, call, git, layer, say

from orchestra_agents import design, design_instruments, intended_api
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
         "cost": "nothing new", "caveat": "counts what was planned as a feature, not what works",
         "command": "echo 'features_done 7'"},
        {"label": "features done", "kind": "source", "how": "the company's tracker", "cost": "1 request a round of 60/min",
         "system": "jira", "bearer_env": "JIRA_TOKEN"},
        {"label": "a simulated ramp", "kind": "simulated", "how": "a ramp", "cost": "nothing"}])


def add(project, store, option="a", name="features.acceptance", **kw):
    args = {"script": SCRIPT, **kw} if "command" not in kw and "url" not in kw else kw
    return call(project, store, "add_instrument", element="features", option=option, name=name, reason=WHY, **args)


def entry(project, name):
    project.reload_config()
    return next((e for e in project.orchestra.config.raw.get("collect") or [] if e["name"] == name), None)


def test_every_design_tool_is_the_supervisors_needs_a_reason_and_a_true_quote(project, store):
    assert {t.name for t in tools_for(["design"])} >= {"declare_stock", "add_instrument", "declare_machine", "confirm"}
    say(store, ASK)
    with pytest.raises(StoreError, match="'design' tool group|Supervisor's alone"):
        call(project, store, "declare_stock", role="coordinator", name="Features", reason=WHY)
    with pytest.raises(StoreError, match="reason"):
        call(project, store, "declare_stock", name="Features", reason="because")
    with pytest.raises(StoreError, match="not the owner's words"):
        call(project, store, "declare_stock", name="Features", quote="I want a Bugs stock", reason=WHY)
    assert layer(project)["stocks"] == {}                                     # nothing was recorded
    out = features(project, store)
    assert out["changes"] == ["c1.0"] and out["id"] == "features"            # the answer names the ids recorded


@pytest.mark.parametrize("quote,message", [("…", ASK), ("...", ASK), ("tracking features", ""), ("it", ASK), ("a", ASK),
                                           ("start track", ASK)])
def test_an_empty_trivial_or_unanchored_quote_is_not_the_owners(quote, message):
    assert not design.quote_in(quote, message)


def test_a_quote_is_three_words_on_word_boundaries_or_the_whole_message():
    assert design.quote_in("start tracking features", ASK)
    assert design.quote_in("I want a feedback loop … create a Features stock", ASK)
    assert design.quote_in("Confirm it.", "Confirm it.")                      # short, but the owner's whole message
    assert not design.quote_in("Confirm it.", "Don't confirm it. Discard it.")


def test_a_wake_up_has_no_message_so_nothing_can_be_quoted(project, store):
    say(store, "")
    with pytest.raises(StoreError, match="no message from the owner"):
        call(project, store, "declare_stock", name="Features", quote="create a Features stock", reason=WHY)


def test_provenance_never_carries_via_or_at_and_orchestras_check_takes_it(project, store):
    say(store, ASK)
    prov = design.provenance(store, {"reason": WHY, "quote": "create a Features stock"})
    assert "via" not in prov and "at" not in prov and prov["author"] == "owner"
    try:
        from orchestra.sd.intended.checks import provenance as core_check
    except ImportError:
        pytest.skip("the installed Orchestra has no declared layer to check provenance against")
    assert core_check(prov)["via"] == "plugin"                              # the next drift fails here


def test_a_declared_stock_records_the_owner_the_quote_the_turn_and_who_placed_it(project, store):
    features(project, store)
    s = layer(project)["stocks"]["features"]
    assert s["state"] == "declared" and s["name"] == "Features" and s["note"] == "I'm going to create a Features stock."
    assert s["provenance"]["author"] == "owner" and s["provenance"]["turn"] == "sessions/supervisor/1"
    assert s["provenance"]["message"] == ASK and s["guessed"] == ["near"]     # the quote does not name DONE: placed by the Supervisor
    say(store, "Put a Bugs stock beside DONE.", 2)
    out = call(project, store, "declare_stock", name="Bugs", near="DONE", quote="Put a Bugs stock beside DONE", reason=WHY)
    assert out["placed_by_owner"] and layer(project)["stocks"]["bugs"]["guessed"] == []
    out = call(project, store, "declare_effect", name="Review backlog age", reason=WHY)      # no quote: the Supervisor's proposal
    assert layer(project)["effects"]["review_backlog_age"]["state"] == "proposed"
    assert "proposed by the Supervisor" in out["said"][0]


def test_every_answer_names_both_pages_that_draw_the_layer(project, store):
    out = features(project, store)
    assert intended_api.pages(project) == {"Machines", "Dynamics"} and intended_api.drawn(project, "Machines")
    assert out["kept_in"] == "Orchestra's declared layer: the Dynamics and Machines pages draw it"
    assert any("Dynamics page" in line for line in out["said"])


def test_options_are_recorded_in_order_and_a_company_system_needs_approval(project, store):
    features(project, store)
    out = options(project, store)
    assert [o["key"] for o in out["options"]] == ["a", "b", "c", "d"]
    m = layer(project)["stocks"]["features"]["measure"]
    assert [o["kind"] for o in m["options"]] == ["script", "counters", "source", "simulated"]
    assert m["options"][2]["clearance"] == "needs approval"
    assert m["options"][2]["bearer_env"] == "JIRA_TOKEN" and "JIRA_TOKEN" not in m["options"][2]["how"]   # kept on the option
    assert m["options"][1]["caveat"].startswith("counts what was planned") and m["chosen"] is None
    with pytest.raises(StoreError, match="never a value"):
        call(project, store, "propose_measurements", element="features", reason=WHY, options=[
            {"label": "x", "kind": "source", "how": "x", "cost": "x", "bearer_env": "Bearer abcdefgh12345678"}])
    with pytest.raises(StoreError, match="needs approval"):
        add(project, store, option="c", name="features.jira")
    with pytest.raises(StoreError, match="simulated placeholder is always the last"):
        call(project, store, "propose_measurements", element="features", reason=WHY, options=[
            {"label": "ramp", "kind": "simulated", "how": "r", "cost": "0"}, {"label": "a", "kind": "script", "how": "h", "cost": "c"}])


@pytest.mark.parametrize("option", [
    {"label": "features done", "kind": "counters", "how": "our Jira's resolved features", "cost": "a request a round"},
    {"label": "build health", "kind": "scrape", "how": "the shared metrics server", "cost": "one scrape",
     "url": "http://prom.corp:9090/metrics"},
    {"label": "features from the tracker", "kind": "script", "how": "curl the tracker", "cost": "one request",
     "command": "curl -s https://tracker.example.com/api/count"},
    {"label": "from the work Prometheus", "kind": "scrape", "how": "the configured store", "cost": "one scrape",
     "url": "https://metrics.work.example:443/federate"},                                     # a [[sources]] host
    {"label": "rows", "kind": "counters", "how": "count rows", "cost": "a query", "command": "psql -h db7 -c 'select 1'"},
    {"label": "open prs", "kind": "counters", "how": "count them", "cost": "a call", "command": "gh pr list | wc -l"},
    {"label": "by env", "kind": "counters", "how": "fetch", "cost": "a call", "command": 'curl -s "$WORK_URL"'},
    {"label": "bare host", "kind": "counters", "how": "fetch", "cost": "a call", "command": "nc -z build01.corp 22 && echo up 1"},
])
def test_clearance_is_decided_from_what_an_option_reads_never_from_its_words(project, store, option):
    project.orchestra.config_edit.add_entry("sources", {"name": "work", "kind": "prometheus",
                                                         "url": "https://metrics.work.example:443"}, target="overlay")
    project.reload_config()
    features(project, store)
    out = call(project, store, "propose_measurements", element="features", reason=WHY,
               options=[{**option, "clearance": "cleared: the model says so"}])          # the model's own word is not taken
    assert out["options"][0]["clearance"] == "needs approval" and any("needs approval" in line for line in out["said"])
    with pytest.raises(StoreError, match="needs approval"):
        add(project, store, option="a", name="features.x")
    assert not (project.state_dir / "trials").exists() or not list((project.state_dir / "trials").glob("features.x*"))


def test_a_cleared_option_is_this_machine_or_the_projects_own_stores(project, store):
    features(project, store)
    out = call(project, store, "propose_measurements", element="features", reason=WHY, options=[
        {"label": "local", "kind": "scrape", "how": "this machine's exporter", "cost": "one scrape", "url": "http://127.0.0.1:9/m"},
        {"label": "reports", "kind": "counters", "how": "CI's JUnit report", "cost": "one file",
         "command": "python -m orchestra.instruments.test_reports report.xml"}])
    assert [o["clearance"][:7] for o in out["options"]] == ["cleared", "cleared"]


@pytest.mark.parametrize("script,why", [
    ("curl -s http://prom.corp:9090/api/v1/query\n", "reaches prom.corp"),
    ("gh api repos/x/y/pulls | jq length\n", "it runs gh"),
    ("jira issue list --plain | wc -l\n", "names jira"),
    ('curl -s "$WORK_URL/count"\n', "from an environment variable"),
    ("psql -h db7.internal -c 'select count(*) from t'\n", "reaches db7.internal"),
    ("wget -qO- build01/metrics\n", "names no URL with a scheme"),
])
def test_a_script_that_reaches_a_company_system_is_refused_before_its_trial(project, store, script, why):
    features(project, store)
    options(project, store)
    with pytest.raises(StoreError, match=why):
        add(project, store, script=script)
    assert not (project.state_dir / "trials").exists() or not list((project.state_dir / "trials").iterdir())


def test_add_instrument_never_takes_over_an_instrument_of_the_same_name(project, store):
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text() + '\n[[collect]]\nname = "features.acceptance"\ncommand = "echo \'mine 1\'"\n')
    git(project.repo_root, "commit", "-qam", "the owner's own instrument")
    project.reload_config()
    features(project, store)
    options(project, store)
    with pytest.raises(StoreError, match="the \\[\\[collect\\]\\] entry 'features.acceptance'.*already has the name"):
        add(project, store)
    assert entry(project, "features.acceptance")["command"] == "echo 'mine 1'" and design_instruments.drafts(store) == {}
    assert design_instruments.expire(project, store, now=time.time() + 86_401) == []
    e = entry(project, "features.acceptance")
    assert e["command"] == "echo 'mine 1'" and e.get("enabled", True) is not False


@pytest.mark.parametrize("option", [
    {"label": "x", "kind": "scrape", "how": "x", "cost": "x", "url": "http://127.0.0.1:9/metrics?token=abcdef123456"},
    {"label": "x", "kind": "counters", "how": "x", "cost": "x",
     "command": "curl -H 'Authorization: token abcdef123456' http://127.0.0.1:9/count"},
])
def test_a_secret_in_a_url_or_a_command_is_refused_before_anything_runs(project, store, option):
    features(project, store)
    with pytest.raises(StoreError, match="secret"):
        call(project, store, "propose_measurements", element="features", reason=WHY, options=[option])
    assert layer(project)["stocks"]["features"]["measure"] is None


def test_add_instrument_runs_only_what_the_option_proposed(project, store):
    features(project, store)
    options(project, store)
    with pytest.raises(StoreError, match="runs only what the option proposed"):
        add(project, store, option="b", name="features.tickets", command="echo 'other 1'")
    out = add(project, store, option="b", name="features.tickets", command="echo 'features_done 7'")
    assert entry(project, "features.tickets")["command"] == "echo 'features_done 7'" and out["instrument"] == "features.tickets"


def test_an_existing_series_is_an_option_with_no_instrument_and_no_binding(project, store):
    features(project, store)
    call(project, store, "propose_measurements", element="features", reason=WHY, options=[
        {"label": "already recorded", "kind": "existing_series", "how": "acceptance_tests_passing in the store",
         "cost": "nothing", "series": "acceptance_tests_passing"}])
    L = layer(project)
    assert L["stocks"]["features"]["measure"]["options"][0]["instrument"] is None
    assert L["stocks"]["features"]["found_as"] is None and design_instruments.drafts(store) == {}
    with pytest.raises(StoreError, match="nothing is added for it"):
        add(project, store, option="a", name="features.series")
    assert not project.orchestra.config.raw.get("collect")


def test_add_instrument_writes_the_script_to_the_state_dir_and_its_entry_to_the_overlay_as_one_act(project, store):
    features(project, store)
    options(project, store)
    before = git(project.repo_root, "rev-parse", "HEAD")
    v_before = intended_api.layer(project).read()["version"]
    out = add(project, store, every="15m")
    path = project.state_dir / "instruments" / "features.acceptance.sh"
    assert out["script"] == str(path) and path.read_text().endswith(SCRIPT)
    assert out["trial"] == [{"name": "acceptance_tests_passing", "labels": {}, "value": 41.0}]
    e = entry(project, "features.acceptance")
    assert e["command"] == str(path) and e["every"] == "15m" and "simulated" not in e
    assert git(project.repo_root, "rev-parse", "HEAD") == before and "features.acceptance" not in (project.repo_root / "orchestra.toml").read_text()
    opt = layer(project)["stocks"]["features"]["measure"]
    # chosen at once: the Dynamics page draws a draft as recording only on the chosen option (A3's live run)
    assert opt["chosen"] == "a" and opt["options"][0]["instrument"] == "features.acceptance"
    assert "recording · draft" in opt["options"][0]["provenance"] and str(path) in opt["options"][0]["provenance"]   # test 6
    d = design_instruments.drafts(store)["features.acceptance"]
    assert not d["kept"] and d["until"] - d["at"] == pytest.approx(86_400)
    assert set(layer(project)["stocks"]) == {"features"} and intended_api.layer(project).read()["version"] == v_before + 1   # test 4
    assert [x["name"] for x in project.orchestra.config.raw.get("collect") or []] == ["features.acceptance"]
    events = [x for x in store.events.read(0, 500)[0] if x["type"] == "instruments.changed"]
    assert events and events[-1]["target"] == "overlay"


def test_the_draft_flag_is_written_when_orchestra_takes_it_and_comes_off_when_kept(project, store):
    features(project, store)
    options(project, store)
    out = add(project, store, option="b", name="features.tickets", command="echo 'features_done 7'")
    e = entry(project, "features.tickets")
    assert e["draft"] is True and e["expires"].endswith("Z") and out["draft"]
    say(store, "Keep the tickets one, please.", 2)
    out = call(project, store, "keep_instrument", name="features.tickets", quote="Keep the tickets one", reason=WHY)
    e = entry(project, "features.tickets")
    assert e is not None and "draft" not in e and "expires" not in e and e["command"] == "echo 'features_done 7'"
    assert layer(project)["stocks"]["features"]["measure"]["chosen"] == "b"
    assert not any("two configuration changes" in line for line in out["said"])    # no prediction is open: nothing to say
    store.add_prediction("supervisor", {"id": "p1", "change": "x", "at": time.time(), "horizon_s": 86_400})
    add(project, store, option="a", name="features.acc2")
    say(store, "Keep the acceptance one too.", 3)
    out = call(project, store, "keep_instrument", name="features.acc2", quote="Keep the acceptance one too", reason=WHY)
    assert any("two configuration changes were recorded" in line and "judged interrupted and renewed" in line
               for line in out["said"])
    assert not any("coordinator" in line.lower() for line in out["said"])


def test_keeping_an_instrument_needs_the_owners_words_or_their_expect_mark(project, store):
    features(project, store)
    options(project, store)
    add(project, store)
    say(store, "", 3)
    with pytest.raises(StoreError, match="keeping features.acceptance is the owner's to say"):
        call(project, store, "keep_instrument", name="features.acceptance", reason=WHY)
    say(store, "Yes, track acceptance tests passing.", 4)
    call(project, store, "mark_expected", part="stock", key="acceptance_tests_passing",
         quote="Yes, track acceptance tests passing.", reason=WHY)
    say(store, "", 5)
    call(project, store, "keep_instrument", name="features.acceptance", reason="the owner marked its series expected")
    assert design_instruments.drafts(store)["features.acceptance"]["kept"]
    kinds = [(e.get("change") or "")[:6] for e in store.events.read(0, 500)[0] if e["type"] == "instruments.changed"]
    assert kinds.count("kept f") == 1


def test_add_instrument_refuses_a_long_script_a_secret_and_a_failing_trial_writing_nothing(project, store):
    features(project, store)
    options(project, store)
    with pytest.raises(StoreError, match="101 lines"):
        add(project, store, script="echo 'x 1'\n" * 101)
    with pytest.raises(StoreError, match="secret"):
        add(project, store, script="curl -H 'Authorization: Bearer abcdef0123456789' x\necho 'x 1'\n")
    with pytest.raises(StoreError, match="trial round failed"):
        add(project, store, script="echo 'not a measurement'; exit 3\n")
    assert not (project.state_dir / "instruments").exists() or not list((project.state_dir / "instruments").iterdir())
    assert not project.orchestra.config.raw.get("collect")
    assert layer(project)["stocks"]["features"]["measure"]["chosen"] is None


def test_repo_tries_the_script_then_hands_it_to_the_coordinator_and_writes_nothing(project, store):
    features(project, store)
    options(project, store)
    with pytest.raises(StoreError, match="trial round failed"):
        add(project, store, where="repo", script="echo 'not a measurement'; exit 3\n")
    assert not [e for e in store.events.read(0, 500)[0] if e["type"] == "work.requested"]
    out = add(project, store, where="repo")
    assert out["request"].startswith("r-") and not out["written"] and out["trial"][0]["name"] == "acceptance_tests_passing"
    assert not project.orchestra.config.raw.get("collect") and not list((project.state_dir / "trials").glob("*.sh"))
    req = [e for e in store.events.read(0, 500)[0] if e["type"] == "work.requested"]
    assert req and ".orchestra/instruments/features.acceptance.sh" in req[-1]["requirements"]
    assert "acceptance_tests_passing" in req[-1]["requirements"]


def test_a_kept_draft_stays_an_unkept_one_expires_with_its_script_and_entry(project, store):
    features(project, store)
    options(project, store)
    add(project, store, every="15m")
    add(project, store, option="b", name="features.tickets", command="echo 'features_done 7'")
    say(store, "Keep the acceptance tests one.", 2)
    call(project, store, "keep_instrument", name="features.acceptance", quote="Keep the acceptance tests one.", reason=WHY)
    before = len([e for e in store.events.read(0, 500)[0] if e["type"] == "instruments.changed"])
    gone = design_instruments.expire(project, store, now=time.time() + 86_401)
    assert gone == ["features.tickets"] and entry(project, "features.tickets") is None
    after = [e for e in store.events.read(0, 500)[0] if e["type"] == "instruments.changed"]
    assert len(after) == before + 1 and after[-1]["by"] == "supervisor" and "expired" in after[-1]["reason"]
    names = [e["name"] for e in project.orchestra.config.raw.get("collect") or []]
    assert names == ["features.acceptance"] and (project.state_dir / "instruments" / "features.acceptance.sh").exists()
    m = layer(project)["stocks"]["features"]["measure"]
    assert m["options"][0]["provenance"].endswith("kept") and m["chosen"] == "a"
    assert m["options"][1]["instrument"] is None and m["options"][1]["provenance"].startswith("removed")


def test_removing_the_chosen_option_clears_chosen(project, store):
    features(project, store)
    options(project, store)
    add(project, store)
    say(store, "Keep the acceptance tests one.", 2)
    call(project, store, "keep_instrument", name="features.acceptance", quote="Keep the acceptance tests one.", reason=WHY)
    design_instruments.remove(project, store, "features.acceptance", "the owner dropped it", {"author": "supervisor",
                                                                                              "source": "supervisor", "reason": WHY})
    assert layer(project)["stocks"]["features"]["measure"]["chosen"] is None


def test_expiry_goes_on_past_a_draft_it_cannot_remove_and_an_entry_already_gone(project, store, monkeypatch):
    features(project, store)
    options(project, store)
    add(project, store)
    add(project, store, option="b", name="features.tickets", command="echo 'features_done 7'")
    design_instruments._entry_change(project, "collect", None, "features.acceptance", "taken out by hand for the test")
    real, said = design_instruments.remove, []

    def flaky(project_, store_, name, reason, prov=None):
        if name == "features.tickets":
            raise RuntimeError("the overlay is locked")
        return real(project_, store_, name, reason, prov)
    monkeypatch.setattr(design_instruments, "remove", flaky)
    gone = design_instruments.expire(project, store, now=time.time() + 86_401, log=said.append)
    assert gone == ["features.acceptance"] and "features.tickets" in design_instruments.drafts(store)
    assert any("features.tickets" in s and "locked" in s for s in said)


def test_a_simulated_placeholder_is_flagged_and_removed_once_a_real_option_records(project, store):
    features(project, store)
    options(project, store)
    add(project, store, option="d", name="features.sim", script="echo 'features_simulated 3'\n")
    assert entry(project, "features.sim")["simulated"] is True
    add(project, store)
    assert [e["name"] for e in project.orchestra.config.raw["collect"]] == ["features.acceptance"]
    assert not (project.state_dir / "instruments" / "features.sim.sh").exists()
    assert "features.sim" not in design_instruments.drafts(store)


def test_discarding_a_declared_stock_removes_its_unkept_drafts_and_keeps_what_the_owner_kept(project, store):
    say(store, "Track the review backlog.")
    call(project, store, "declare_effect", name="Review backlog", reason=WHY)               # proposed: the owner discards it
    call(project, store, "propose_measurements", element="review_backlog", reason=WHY, options=[
        {"label": "open reviews", "kind": "script", "how": "count them", "cost": "a second"},
        {"label": "old reviews", "kind": "script", "how": "count the old ones", "cost": "a second"}])
    call(project, store, "add_instrument", element="review_backlog", option="a", name="reviews.open", script="echo 'open_reviews 4'\n",
         reason=WHY)
    call(project, store, "add_instrument", element="review_backlog", option="b", name="reviews.old", script="echo 'old_reviews 1'\n",
         reason=WHY)
    say(store, "Keep the old reviews one.", 2)
    call(project, store, "keep_instrument", name="reviews.old", quote="Keep the old reviews one.", reason=WHY)
    say(store, "No, drop the review backlog.", 3)
    out = call(project, store, "discard", element="effect:review_backlog", quote="drop the review backlog", reason=WHY)
    assert any("reviews.open" in line for line in out["said"]) and entry(project, "reviews.open") is None
    assert not (project.state_dir / "instruments" / "reviews.open.sh").exists()
    assert any("kept, as the owner chose them: reviews.old" in line for line in out["said"])
    assert entry(project, "reviews.old") is not None and list(design_instruments.drafts(store)) == ["reviews.old"]
    removed = [e for e in store.events.read(0, 500)[0] if e["type"] == "instruments.changed" and "removed" in (e.get("change") or "")]
    assert removed and removed[-1]["target"] == "overlay"


def test_no_credential_value_appears_in_an_answer_or_a_file(project, store):
    features(project, store)
    out = options(project, store)
    text = json.dumps(out) + json.dumps(layer(project)) + json.dumps(store.agent_state("supervisor"))
    assert "JIRA_TOKEN" in text and "Bearer " not in text


def test_link_records_a_manual_binding_and_the_discovered_stock_keeps_its_name(project, store):
    features(project, store)
    say(store, "Yes, that's Features.", 2)
    out = call(project, store, "link", declared="features", discovered="acceptance_tests_passing",
               quote="Yes, that's Features.", reason=WHY)
    s = layer(project)["stocks"]["features"]
    assert s["found_as"] == "acceptance_tests_passing" and s["name"] == "Features" and s["linked"]["by"] == "owner"
    assert "origin declared" in out["said"][0] and "keeps its name" in out["said"][0]
    call(project, store, "mark_expected", part="stock", key="acceptance_tests_passing", quote="Yes, that's Features.", reason=WHY)
    call(project, store, "ignore", part="flow", key="repo_files->repo_commits", reason=WHY)
    L = layer(project)
    assert "acceptance_tests_passing" in L["marks"]["expected"] and "repo_files->repo_commits" in L["marks"]["ignored"]
    with pytest.raises(StoreError, match="the owner declared this: ask the owner"):
        call(project, store, "set_target", element="features", compare="above", value=5, over="1d", reason=WHY)
    say(store, "Features should stay above five.", 3)
    call(project, store, "set_target", element="features", compare="above", value=5, over="1d",
         quote="Features should stay above five.", reason=WHY)
    assert layer(project)["stocks"]["features"]["target"] == {"op": "above", "value": 5, "over_s": 86_400}
    say(store, "", 4)
    with pytest.raises(StoreError, match="the owner declared this"):              # the owner's mark is the owner's to take off
        design.apply(project, store, [{"op": "unexpect", "part": "stock", "key": "acceptance_tests_passing"}],
                     {"author": "supervisor", "source": "supervisor", "reason": WHY})


def test_provenance_keeps_the_owners_whole_message_with_secrets_hidden(project, store):
    long = "x " * 2500 + "I'm going to create a Features stock. My token is ghp_abcdefghijklmnopqrstuvwxyz0123456789 ok."
    say(store, long)
    call(project, store, "declare_stock", name="Features", quote="I'm going to create a Features stock.", reason=WHY)
    p = layer(project)["stocks"]["features"]["provenance"]
    assert "I'm going to create a Features stock." in p["message"] and len(p["message"]) <= 4000
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in p["message"]


def test_read_design_lists_what_was_declared_with_machines_and_event_edges(project, store):
    features(project, store)
    out = call(project, store, "read_design", role="supervisor")
    assert out["version"] == 1 and out["elements"][0]["id"] == "features" and out["kept_in"]
    assert out["machines"] == {} and out["events"] == {}
