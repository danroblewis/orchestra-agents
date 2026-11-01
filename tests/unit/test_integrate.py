"""integrate_system and approve_integration (design_integrate.py) on Orchestra's declared layer
(design_fixtures), with the bridges' kits installed (the dev extra). The scaffolded program's own
test is run for real in tests/e2e/test_integrate_e2e.py; here it is stood in for (``passed``), so
the suite stays in process."""

import tomllib

import pytest
from design_fixtures import WHY, call, git, layer, say

from orchestra_agents import design_integrate, integrate_kits, tooling
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

ASK = "Connect our Jira so I can see issue transitions and open counts, project OPS at https://jira.acme.test"
YES = "Yes, you may read Jira, metadata only, with a read-only token in JIRA_TOKEN"


@pytest.fixture(autouse=True)
def passing(monkeypatch):
    monkeypatch.setattr(integrate_kits, "run_test", lambda target, name, **_: {
        "ran": True, "passed": True, "summary": "3 passed in 0.5s", "command": f"python -m pytest {target}/test_{name}.py -q"})


def connect(project, store, **over):
    say(store, ASK)
    args = {"system": "jira", "wants": "issue transitions and open counts", "settings": {"jql": "project = OPS"},
            "base_url": "https://jira.acme.test", "stock": "Open issues", "unit": "issues",
            "quote": "Connect our Jira so I can see issue transitions and open counts", "reason": WHY}
    return call(project, store, "integrate_system", **{**args, **over})


def overlay(project) -> dict:
    f = project.orchestra.overlay_file
    return tomllib.loads(f.read_text()) if f.exists() else {}


def machine_toml(project, name="jira_poller") -> dict:
    return tomllib.loads((project.orchestra.state_dir / "machines" / name / "machine.toml").read_text())


def test_a_jira_poller_is_scaffolded_declared_as_jira_and_waits_for_the_owner(project, store):
    got = connect(project, store)
    d = project.orchestra.state_dir / "machines" / "jira_poller"
    assert got["ok"] and got["integration"] == "jira_poller" and got["clearance"] == "needs approval"
    assert {p.name for p in d.iterdir()} >= {"machine.toml", "jira_poller.py", "test_jira_poller.py", "AGENTS.md"}
    m = machine_toml(project)
    assert m["clearance"] == "needs approval" and m["settings"]["jql"] == "project = OPS"
    assert m["settings"]["base_url"] == "https://jira.acme.test" and m["wanted"] == "issue transitions and open counts"
    jira = layer(project)["machines"]["jira"]
    assert jira["managed"] == "external" and jira["kind"] == "programmatic" and jira["aliases"] == ["bot.jira"]
    assert jira["emits"] == ["jira.issue.transitioned"] and jira["spec"]["source"] == "bot.jira"
    assert jira["provenance"]["author"] == "owner"
    assert "jira_poller" not in layer(project)["machines"]                     # the poller is a relay, never a machine
    sid = next(k for k, s in layer(project)["stocks"].items() if s["name"] == "Open issues")
    assert jira["expected_effect"]["edits"] == [{"from": None, "to": sid, "appears": True}]
    assert got["planned"] == ["[[machines]] jira_poller, restart always", "[[collect]] jira_poller, every 5m"]
    assert "machines" not in overlay(project) and "collect" not in overlay(project)   # nothing runs before the approval
    assert "environment variable" in got["ask_owner"] and "never the token" in got["ask_owner"]
    assert any("bridges also read jira" in line for line in got["said"])


def test_the_owners_approval_records_the_name_and_schedules_it(project, store):
    connect(project, store)
    head = git(project.repo_root, "rev-parse", "HEAD")
    toml_before = (project.repo_root / "orchestra.toml").read_text()
    say(store, YES, 2)
    got = call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN",
               quote="Yes, you may read Jira", reason=WHY)
    assert got["ok"] and got["clearance"] == "approved" and got["token_env"] == "JIRA_TOKEN"
    m = machine_toml(project)
    assert m["clearance"] == "approved" and m["settings"]["token_env"] == "JIRA_TOKEN" and m["data_class"] == "metadata only"
    o = overlay(project)
    entry = next(e for e in o["machines"] if e["name"] == "jira_poller")
    assert entry["restart"] == "always" and entry["command"].startswith("python ") and entry["command"].endswith("jira_poller.py")
    assert next(e for e in o["collect"] if e["name"] == "jira_poller")["command"].endswith("jira_poller.py measure")
    assert (project.repo_root / "orchestra.toml").read_text() == toml_before          # the overlay only
    assert git(project.repo_root, "rev-parse", "HEAD") == head
    assert design_integrate.integrations(store)["jira_poller"]["state"] == "approved"
    assert "JIRA_TOKEN" in " ".join(got["said"]) and got["env_set"] is False


def test_approval_is_the_owners_and_names_a_variable_never_a_token(project, store):
    connect(project, store)
    say(store, YES, 2)
    with pytest.raises(StoreError, match="missing quote"):
        call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN", quote="", reason=WHY)
    with pytest.raises(StoreError, match="the owner's alone"):                     # past the schema too
        tooling.handle(project, store, "approve_integration", {"name": "jira_poller", "token_env": "JIRA_TOKEN",
                                                               "reason": WHY}, role="supervisor")
    with pytest.raises(StoreError, match="NAME of an environment variable"):
        call(project, store, "approve_integration", name="jira_poller", token_env="abc-123 not a name",
             quote="Yes, you may read Jira", reason=WHY)
    with pytest.raises(StoreError, match="no integration"):
        call(project, store, "approve_integration", name="slack_poller", token_env="JIRA_TOKEN",
             quote="Yes, you may read Jira", reason=WHY)
    assert machine_toml(project)["clearance"] == "needs approval"


def test_a_template_address_is_asked_for_before_approval(project, store):
    got = connect(project, store, base_url=None)
    assert "base URL" in got["ask_owner"]
    say(store, YES, 2)
    with pytest.raises(StoreError, match="still the template's"):
        call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN",
             quote="Yes, you may read Jira", reason=WHY)
    call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN", base_url="https://jira.acme.test",
         quote="Yes, you may read Jira", reason=WHY)
    assert machine_toml(project)["settings"]["base_url"] == "https://jira.acme.test"


def test_a_token_in_the_settings_is_refused(project, store):
    with pytest.raises(StoreError, match="the token's variable and the clearance are the owner's"):
        connect(project, store, settings={"token": "abc"})
    with pytest.raises(StoreError, match="the token's variable"):
        connect(project, store, settings={"token_env": "JIRA_TOKEN"})
    assert not (project.orchestra.state_dir / "machines" / "jira_poller").exists()


def test_without_the_kits_it_says_so_and_proposes_the_no_kit_route(project, store, monkeypatch):
    monkeypatch.setattr(integrate_kits, "installed", lambda: False)
    got = connect(project, store)
    assert got["ok"] and got["integrated"] is False
    said = " ".join(got["said"])
    assert "not installed" in said and "minikit.py" in said and "hand_to_coordinator" in said and "INTEGRATING.md" in said
    assert not (project.orchestra.state_dir / "machines").exists() and "jira" not in layer(project)["machines"]


def test_a_failed_test_declares_and_plans_nothing(project, store, monkeypatch):
    monkeypatch.setattr(integrate_kits, "run_test", lambda target, name, **_: {
        "ran": True, "passed": False, "summary": "1 failed", "command": "pytest", "tail": ["E assert"]})
    got = connect(project, store)
    assert got["integrated"] is False and "jira" not in layer(project)["machines"]
    say(store, YES, 2)
    with pytest.raises(StoreError, match="test failed"):
        call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN",
             quote="Yes, you may read Jira", reason=WHY)


def test_a_prometheus_store_is_a_source_written_only_on_approval(project, store):
    say(store, "Connect Chronosphere so I can see request rates by service")
    got = call(project, store, "integrate_system", system="chronosphere", wants="request rates by service",
               base_url="https://acme-corp.chronosphere.io/data/metrics",
               settings={"match": ['{__name__="http_requests_total"}'], "prefix": "chrono", "rate": 2.0},
               quote="Connect Chronosphere so I can see request rates by service", reason=WHY)
    assert got["planned"] == ["[[sources]] chronosphere"] and "sources" not in overlay(project)
    assert "chronosphere" not in layer(project)["machines"]                  # a store has no item events, no machine
    say(store, "Yes, read Chronosphere, metadata only, token in CHRONO_TOKEN", 2)
    call(project, store, "approve_integration", name="chronosphere", token_env="CHRONO_TOKEN",
         quote="Yes, read Chronosphere", reason=WHY)
    src = overlay(project)["sources"][0]
    assert src["auth"] == {"bearer_env": "CHRONO_TOKEN"} and src["prefix"] == "chrono"
    assert src["url"] == "https://acme-corp.chronosphere.io/data/metrics"


def test_with_the_workflows_plugin_it_is_the_floors_every_item(project, store, monkeypatch):
    monkeypatch.setattr(integrate_kits, "workflows_installed", lambda: True)
    got = connect(project, store)
    assert got["planned"][0].startswith("[workflows.every.jira_poller] every 5m")
    say(store, YES, 2)
    call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN",
         quote="Yes, you may read Jira", reason=WHY)
    o = overlay(project)
    assert o["workflows"]["every"]["jira_poller"] == {"every": "5m", "role": "jira_poller", "brief": "poll Jira once"}
    assert "machines" not in o and o["collect"][0]["name"] == "jira_poller"
    import json
    body = json.loads((project.orchestra.state_dir / "machines" / "jira_poller" / "machine.json").read_text())
    assert body["kind"] == "programmatic" and body["spec"]["command"].endswith("--once")


def test_integrating_is_the_supervisors_alone(project, store):
    say(store, ASK)
    with pytest.raises(StoreError, match="needs the .design. tool group"):
        call(project, store, "integrate_system", role="worker", system="jira", wants="issue transitions", reason=WHY)


def test_a_name_taken_is_refused_before_anything_is_written(project, store):
    connect(project, store)
    with pytest.raises(StoreError, match="already has the name"):
        connect(project, store)
    with pytest.raises(StoreError, match="never a bridge's own name"):
        connect(project, store, name="jira")


def test_the_test_runs_without_the_environments_credentials(monkeypatch):
    monkeypatch.setenv("JIRA_TOKEN", "not-a-real-token")
    monkeypatch.setenv("GH_API_KEY", "x")
    monkeypatch.setenv("ORCHESTRA_EVENTS_TOKEN", "x")
    env = integrate_kits.test_env()
    assert "JIRA_TOKEN" not in env and "GH_API_KEY" not in env and "ORCHESTRA_EVENTS_TOKEN" not in env and "PATH" in env


def test_the_bridges_own_reader_is_its_section_written_on_approval(project, store):
    say(store, ASK)
    got = call(project, store, "integrate_system", system="jira", strategy="bridge", wants="issue transitions and open counts",
               base_url="https://jira.acme.test", settings={"jql": "project = OPS", "every": "5m"},
               quote="Connect our Jira so I can see issue transitions", reason=WHY)
    assert got["planned"] == ["[bridges.jira]"] and "bridges" not in overlay(project)
    say(store, YES, 2)
    call(project, store, "approve_integration", name="jira", token_env="JIRA_API_TOKEN", quote="Yes, you may read Jira",
         reason=WHY)
    assert overlay(project)["bridges"]["jira"] == {"api": "https://jira.acme.test", "jql": "project = OPS", "every": "5m"}


def test_a_failure_after_the_scaffold_leaves_nothing_behind(project, store, monkeypatch):
    def broken(*a, **k):
        raise StoreError("the declared layer refused it")
    monkeypatch.setattr(design_integrate, "_declare", broken)
    with pytest.raises(StoreError, match="refused it"):
        connect(project, store)
    assert not (project.orchestra.state_dir / "machines" / "jira_poller").exists()
    assert "jira_poller" not in design_integrate.integrations(store)


def test_the_scope_the_template_guesses_is_asked_for(project, store):
    got = connect(project, store, settings={})
    assert "which Jira projects" in got["ask_owner"]
    say(store, YES, 2)
    with pytest.raises(StoreError, match="scope"):
        call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN",
             quote="Yes, you may read Jira", reason=WHY)
    call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN", settings={"jql": "project = OPS"},
         quote="Yes, you may read Jira", reason=WHY)
    assert machine_toml(project)["settings"]["jql"] == "project = OPS"
