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

REAL_RUN_TEST = integrate_kits.run_test                    # before the autouse fixture stands a passing one in
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


def scaffolded(project, name="jira_poller"):
    """Where integrate_system scaffolds by default: the owner's repository (the owner, 2026-11-01)."""
    return project.repo_root / ".orchestra" / "machines" / name


def machine_toml(project, name="jira_poller") -> dict:
    return tomllib.loads((scaffolded(project, name) / "machine.toml").read_text())


def asked(store) -> list[dict]:
    return [e for e in store.events.read(0, 500)[0] if e["type"] == "work.requested"]


def test_a_jira_poller_is_scaffolded_declared_as_jira_and_waits_for_the_owner(project, store):
    got = connect(project, store)
    d = scaffolded(project)
    assert got["dir"] == str(d) and not (project.orchestra.state_dir / "machines" / "jira_poller").exists()
    assert any(str(d) in line and "a coding tool" in line and "pull request" in line for line in got["said"])
    assert got["ok"] and got["integration"] == "jira_poller" and got["clearance"] == "needs approval"
    assert {p.name for p in d.iterdir()} >= {"machine.toml", "jira_poller.py", "test_jira_poller.py", "AGENTS.md"}
    m = machine_toml(project)
    assert m["clearance"] == "needs approval" and m["settings"]["jql"] == "project = OPS"
    assert m["settings"]["base_url"] == "https://jira.acme.test" and "wanted" not in m   # the owner's words are the record's
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
    assert not asked(store) and "request" not in got                          # the jira kit's own template: no hand-off


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
    assert not scaffolded(project).exists()


def test_without_the_kits_it_says_so_and_proposes_the_no_kit_route(project, store, monkeypatch):
    monkeypatch.setattr(integrate_kits, "installed", lambda: False)
    got = connect(project, store)
    assert got["ok"] and got["integrated"] is False
    said = " ".join(got["said"])
    assert "not installed" in said and "minikit.py" in said and "hand_to_coordinator" in said and "INTEGRATING.md" in said
    assert not (project.orchestra.state_dir / "machines").exists() and not scaffolded(project).exists()
    assert "jira" not in layer(project)["machines"]


def test_a_failed_test_is_handed_to_the_coordinator_and_declares_nothing(project, store, monkeypatch):
    monkeypatch.setattr(integrate_kits, "run_test", lambda target, name, **_: {
        "ran": True, "passed": False, "summary": "1 failed", "command": "pytest", "tail": ["E assert"]})
    got = connect(project, store)
    assert got["integrated"] is False and "jira" not in layer(project)["machines"]
    work = asked(store)
    assert len(work) == 1 and work[0]["request"] == got["request"] and work[0]["to"] == "coordinator"
    assert str(scaffolded(project)) in work[0]["requirements"] and "1 failed" in work[0]["requirements"]
    assert "pull request" in work[0]["requirements"] and "Outbox" in work[0]["requirements"]
    assert design_integrate.integrations(store)["jira_poller"]["request"] == got["request"]
    assert scaffolded(project).is_dir()                                       # kept for the worker
    say(store, YES, 2)
    with pytest.raises(StoreError, match="test failed"):
        call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN",
             quote="Yes, you may read Jira", reason=WHY)


def test_without_pytest_the_test_is_not_run_and_that_is_no_failure(project, store, monkeypatch):
    import importlib.util
    monkeypatch.setattr(integrate_kits, "run_test", REAL_RUN_TEST)
    find = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a: None if n == "pytest" else find(n, *a))
    got = connect(project, store)
    assert got["integrated"] is False and got["test"]["ran"] is False
    assert design_integrate.integrations(store)["jira_poller"]["state"] == "test not run"
    said = " ".join(got["said"])
    assert "the test is not failing, it could not run. pytest is not installed in Orchestra's Python" in said
    assert "uv tool install 'orchestra[sd,agents,bridges,dev] @ git+https://github.com/danroblewis/orchestra@main'" in said
    assert "hand_to_coordinator" not in said and "jira" not in layer(project)["machines"]
    say(store, YES, 2)
    with pytest.raises(StoreError, match="test not run: only an integration whose test passed is approved"):
        call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN", base_url="https://jira.acme.test",
             settings={"jql": "project = OPS"}, quote="Yes, you may read Jira", reason=WHY)
    monkeypatch.setattr(importlib.util, "find_spec", find)                     # pytest installed: the same call again
    monkeypatch.setattr(integrate_kits, "run_test", lambda target, name, **_: {
        "ran": True, "passed": True, "summary": "3 passed", "command": "pytest"})
    got = connect(project, store)
    assert got["integration"] == "jira_poller" and design_integrate.integrations(store)["jira_poller"]["state"] == "needs approval"


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


def test_a_poller_is_a_kept_process_and_never_a_machine_file(project, store):
    got = connect(project, store)
    assert got["planned"][0] == "[[machines]] jira_poller, restart always"
    say(store, YES, 2)
    call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN",
         quote="Yes, you may read Jira", reason=WHY)
    o = overlay(project)
    assert [e["name"] for e in o["machines"]] == ["jira_poller"] and o["collect"][0]["name"] == "jira_poller"
    assert "workflows" not in o
    assert not (scaffolded(project) / "machine.json").exists()


def test_what_fill_and_clear_write_is_read_by_the_pollers_own_reader(project, store):
    from orchestra_bridges.kits.machinefile import read
    connect(project, store)
    target = scaffolded(project)
    assert read(target / "machine.toml").extra["jql"] == "project = OPS"           # what fill wrote
    integrate_kits.clear(target, {"token_env": "JIRA_TOKEN"}, "metadata only", "https://jira.acme.test")
    m = read(target / "machine.toml")
    assert (m.clearance, m.token_env, m.data_class, m.base_url) == ("approved", "JIRA_TOKEN", "metadata only", "https://jira.acme.test")
    before = (target / "machine.toml").read_text()
    with pytest.raises(StoreError, match="the poller's own reader refuses the change .*put back as it was"):
        integrate_kits.fill(target, {"per_minute": "lots"}, None)
    assert (target / "machine.toml").read_text() == before


def test_integrating_is_the_supervisors_alone(project, store):
    say(store, ASK)
    with pytest.raises(StoreError, match="needs the .design. tool group"):
        call(project, store, "integrate_system", role="worker", system="jira", wants="issue transitions", reason=WHY)


def test_a_name_taken_is_refused_before_anything_is_written(project, store):
    connect(project, store)
    with pytest.raises(StoreError, match="already has the name"):
        connect(project, store)
    with pytest.raises(StoreError, match="never the system's own name"):
        connect(project, store, name="jira")


def test_the_test_runs_without_the_environments_credentials(monkeypatch):
    monkeypatch.setenv("JIRA_TOKEN", "not-a-real-token")
    monkeypatch.setenv("GH_API_KEY", "x")
    monkeypatch.setenv("ORCHESTRA_EVENTS_TOKEN", "x")
    env = integrate_kits.test_env()
    assert "JIRA_TOKEN" not in env and "GH_API_KEY" not in env and "ORCHESTRA_EVENTS_TOKEN" not in env and "PATH" in env


def test_the_bridges_have_no_reader_of_their_own_to_choose(project, store):
    say(store, ASK)
    with pytest.raises(StoreError, match="the bridges have no readers"):
        call(project, store, "integrate_system", system="jira", strategy="bridge", wants="issue transitions and open counts",
             base_url="https://jira.acme.test", quote="Connect our Jira so I can see issue transitions", reason=WHY)
    assert "bridges" not in overlay(project) and not scaffolded(project).exists()


def test_a_token_name_the_kits_refuse_is_refused_at_approval(project, store):
    connect(project, store)
    say(store, YES, 2)
    with pytest.raises(StoreError, match="NAME of an environment variable"):
        call(project, store, "approve_integration", name="jira_poller", token_env="_PD_TOKEN", base_url="https://jira.acme.test",
             settings={"jql": "project = OPS"}, quote="Yes, you may read Jira", reason=WHY)


def test_a_failure_after_the_scaffold_leaves_nothing_behind(project, store, monkeypatch):
    def broken(*a, **k):
        raise StoreError("the declared layer refused it")
    monkeypatch.setattr(design_integrate, "_declare", broken)
    with pytest.raises(StoreError, match="refused it"):
        connect(project, store)
    assert not scaffolded(project).exists()
    assert "jira_poller" not in design_integrate.integrations(store)


def test_a_failure_after_the_declaration_names_what_stayed(project, store, monkeypatch):
    def broken(*a, **k):
        raise StoreError("the stock was refused")
    monkeypatch.setattr(design_integrate, "_stock", broken)
    with pytest.raises(StoreError) as e:
        connect(project, store)
    said = str(e.value)
    assert said.startswith("the stock was refused. Applied before it in this call, and kept in the declared layer: machine jira")
    assert "heard as bot.jira" in said and "The poller jira_poller was not kept" in said
    ids = [c["change"] for c in layer_changes(project)]
    assert ids and all(i in said for i in ids)                                    # every change that stayed, by id
    assert layer(project)["machines"]["jira"]["aliases"] == ["bot.jira"]
    assert not scaffolded(project).exists()
    assert "jira_poller" not in design_integrate.integrations(store)


def layer_changes(project) -> list[dict]:
    return [c for e in project.orchestra.intended.journal() for c in e["changes"]]


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


def test_the_state_directory_only_when_the_owner_keeps_the_repository_clean(project, store):
    got = connect(project, store, root="state")
    d = project.orchestra.state_dir / "machines" / "jira_poller"
    assert got["dir"] == str(d) and (d / "machine.toml").is_file() and not scaffolded(project).exists()
    assert any("outside the repository" in line for line in got["said"])
    assert git(project.repo_root, "status", "--porcelain") == ""             # the repository untouched
    with pytest.raises(StoreError, match="root is repository"):
        connect(project, store, name="jira_poller2", root="elsewhere")


def test_a_system_with_no_template_is_scaffolded_and_handed_to_the_coordinator(project, store):
    say(store, "Connect Jenkins so I can see builds")
    got = call(project, store, "integrate_system", system="jenkins", wants="builds started and finished",
               base_url="https://ci.acme.test", quote="Connect Jenkins so I can see builds", reason=WHY)
    assert got["ok"] and got["clearance"] == "needs approval" and got["dir"] == str(scaffolded(project, "jenkins_poller"))
    work = asked(store)
    assert len(work) == 1 and got["request"] == work[0]["request"] and "no template for Jenkins" in work[0]["requirements"]
    assert "jenkins_poller.py" in work[0]["requirements"]


def test_jira_cloud_is_approved_with_a_token_file_and_the_accounts_email_by_reference(project, store, tmp_path):
    connect(project, store, base_url="https://acme.atlassian.net")
    keys = tmp_path / "keys"
    keys.mkdir()
    token, email = keys / "jira-cloud-token", keys / "jira-cloud-email"
    token.write_text("not-a-real-token\n")
    token.chmod(0o600)
    say(store, "Yes, you may read Jira Cloud, the token is in a file and the email beside it", 2)
    got = call(project, store, "approve_integration", name="jira_poller", token_file=str(token), user_file=str(email),
               quote="Yes, you may read Jira Cloud", reason=WHY)
    s = machine_toml(project)["settings"]
    assert s["token_file"] == str(token) and s["user_file"] == str(email)
    assert "token_env" not in s                                               # the template's guess never shadows the file
    assert got["credentials"] == {"token_file": str(token), "user_file": str(email)}
    said = " ".join(got["said"])
    assert "not-a-real-token" not in said and "there, chmod 600" in said and "not there yet" in said
    assert got["owner_must_set"] == [f"{email}: the file, chmod 600"]
    assert "HTTP Basic" in said                                               # the template speaks Data Center: a code change
    rec = design_integrate.integrations(store)["jira_poller"]
    assert rec["state"] == "approved" and rec["credentials"]["user_file"] == str(email)


def test_jira_cloud_by_variables_records_both_names(project, store, monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "someone@example.test")
    connect(project, store)
    say(store, YES, 2)
    got = call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN", user_env="JIRA_EMAIL",
               quote="Yes, you may read Jira", reason=WHY)
    s = machine_toml(project)["settings"]
    assert s["token_env"] == "JIRA_TOKEN" and s["user_env"] == "JIRA_EMAIL" and "token_file" not in s
    assert got["owner_must_set"] == ["JIRA_TOKEN, in the environment orchestra run starts with"]


def test_credentials_are_references_never_values(project, store, tmp_path):
    connect(project, store)
    say(store, YES, 2)
    for bad, match in (({}, "name where the token is"),
                       ({"token_file": "jira-token"}, "full path"),
                       ({"token_file": str(project.repo_root / "secrets" / "jira-token")}, "inside the repository"),
                       ({"token_env": "JIRA_TOKEN", "user_env": "me@acme.test"}, "NAME of an environment variable"),
                       ({"token_env": "ghp_" + "a" * 36}, "credential itself|NAME of an environment variable")):
        with pytest.raises(StoreError, match=match):
            call(project, store, "approve_integration", name="jira_poller", quote="Yes, you may read Jira", reason=WHY, **bad)
    assert machine_toml(project)["clearance"] == "needs approval"


def test_a_store_takes_its_token_from_a_file(project, store, tmp_path):
    say(store, "Connect Chronosphere so I can see request rates by service")
    call(project, store, "integrate_system", system="chronosphere", wants="request rates by service",
         base_url="https://acme-corp.chronosphere.io/data/metrics", settings={"match": ['{__name__="http_requests_total"}']},
         quote="Connect Chronosphere so I can see request rates by service", reason=WHY)
    say(store, "Yes, read Chronosphere, metadata only, the token is in a file", 2)
    call(project, store, "approve_integration", name="chronosphere", token_file=str(tmp_path / "chrono-token"),
         quote="Yes, read Chronosphere", reason=WHY)
    assert overlay(project)["sources"][0]["auth"] == {"bearer_file": str(tmp_path / "chrono-token")}
