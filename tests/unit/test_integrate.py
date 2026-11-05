"""integrate_system and approve_integration (design_integrate.py) on Orchestra's declared layer
(design_fixtures), with the bridges' kits installed (the dev extra). The scaffolded program's own
test is run for real in tests/e2e/test_integrate_e2e.py; here it is stood in for (``passed``), so
the suite stays in process."""

import tomllib

import pytest
from conftest import ask_always
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


def draft_dir(project, name="jira_poller"):
    """Where a repository scaffold handed to the Coordinator waits, out of the owner's checkout."""
    return project.orchestra.state_dir / "machines" / name


def machine_toml(project, name="jira_poller") -> dict:
    return tomllib.loads((scaffolded(project, name) / "poller.toml").read_text())


def asked(store) -> list[dict]:
    return [e for e in store.events.read(0, 500)[0] if e["type"] == "work.requested"]


def test_a_jira_poller_is_scaffolded_declared_as_jira_and_waits_for_the_owner(project, store):
    got = connect(project, store)
    d = scaffolded(project)
    assert got["dir"] == str(d) and not (project.orchestra.state_dir / "machines" / "jira_poller").exists()
    assert any(str(d) in line for line in got["said"])
    assert any("a coding tool" in line and "live from there" in line and "never ask the owner to commit" in line for line in got["said"])
    assert got["ok"] and got["integration"] == "jira_poller" and got["clearance"] == "needs approval"
    assert {p.name for p in d.iterdir()} >= {"poller.toml", "jira_poller.py", "test_jira_poller.py", "AGENTS.md"}
    m = machine_toml(project)
    assert m["clearance"] == "needs approval" and m["settings"]["jql"] == "project = OPS"
    assert m["settings"]["base_url"] == "https://jira.acme.test" and "wanted" not in m   # the owner's words are the record's
    jira = layer(project)["machines"]["jira"]
    assert jira["managed"] == "external" and jira["kind"] == "programmatic" and jira["aliases"] == ["bot.jira"]
    assert {"jira.issue.transitioned", "jira.issue.assigned"} <= set(jira["emits"]) and jira["spec"]["source"] == "bot.jira"
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
    ask_always(project)
    connect(project, store)
    say(store, YES, 2)
    with pytest.raises(StoreError, match="the owner's alone"):
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
    assert "not installed" in said and "minikit.py" in said and "hand_to_coordinator" in said and "orchestra/guide.md" in said and "Without the kits" in said
    assert not (project.orchestra.state_dir / "machines").exists() and not scaffolded(project).exists()
    assert "jira" not in layer(project)["machines"]


def test_a_failed_test_is_handed_to_the_coordinator_and_declares_nothing(project, store, monkeypatch):
    monkeypatch.setattr(integrate_kits, "run_test", lambda target, name, **_: {
        "ran": True, "passed": False, "summary": "1 failed", "command": "pytest", "tail": ["E assert"]})
    got = connect(project, store)
    assert got["integrated"] is False and "jira" not in layer(project)["machines"]
    draft = draft_dir(project)
    assert got["dir"] == str(draft) and draft.is_dir() and not scaffolded(project).exists()   # out of the owner's checkout
    work = asked(store)
    assert len(work) == 1 and work[0]["request"] == got["request"] and work[0]["to"] == "coordinator"
    req = work[0]["requirements"]
    assert f"cp -R {draft} .orchestra/machines/jira_poller" in req and "1 failed" in req and "Outbox" in req
    assert "`python -m pytest .orchestra/machines/jira_poller/test_jira_poller.py -q` passes" in req
    assert "the ticket's review and gate land it on the base branch" in req and "pull request" not in req
    rec = design_integrate.integrations(store)["jira_poller"]
    assert rec["request"] == got["request"] and rec["lands"] == str(scaffolded(project))
    assert any("call integrate_system again with the same name" in line for line in got["said"])
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
    say(store, "Connect Chronosphere so I can see request rates by service, at https://acme-corp.chronosphere.io/data/metrics")
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
    assert read(target / "poller.toml").extra["jql"] == "project = OPS"           # what fill wrote
    integrate_kits.clear(target, {"token_env": "JIRA_TOKEN"}, "metadata only", "https://jira.acme.test")
    m = read(target / "poller.toml")
    assert (m.clearance, m.token_env, m.data_class, m.base_url) == ("approved", "JIRA_TOKEN", "metadata only", "https://jira.acme.test")
    before = (target / "poller.toml").read_text()
    with pytest.raises(StoreError, match="the poller's own reader refuses the change .*put back as it was"):
        integrate_kits.fill(target, {"per_minute": "lots"}, None)
    assert (target / "poller.toml").read_text() == before


def test_the_settings_file_is_the_one_the_bridges_reader_finds_new_or_legacy(project, store, tmp_path):
    """The kit's layout, not a name of ours: the scaffold writes ``machinefile.FILE`` (``poller.toml``)
    and the agents edit what ``machinefile.locate`` finds, so a scaffold made before the rename (a
    ``machine.toml`` with a top-level ``kit``) is still filled where it is, never beside it."""
    from orchestra_bridges.kits.machinefile import FILE, LEGACY
    connect(project, store)
    target = scaffolded(project)
    assert integrate_kits.settings_file(target) == target / FILE and not (target / LEGACY).exists()
    old = tmp_path / "old_poller"
    old.mkdir()
    (target / FILE).rename(old / LEGACY)
    integrate_kits.fill(old, {"jql": "project = LEGACY"}, None)
    assert not (old / FILE).exists() and integrate_kits.read(old)["settings"]["jql"] == "project = LEGACY"


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
    assert got["dir"] == str(d) and (d / "poller.toml").is_file() and not scaffolded(project).exists()
    assert any("outside the repository" in line for line in got["said"])
    assert git(project.repo_root, "status", "--porcelain") == ""             # the repository untouched
    with pytest.raises(StoreError, match="root is repository"):
        connect(project, store, name="jira_poller2", root="elsewhere")


def test_a_system_with_no_template_is_scaffolded_and_handed_to_the_coordinator(project, store):
    say(store, "Connect Jenkins so I can see builds, at https://ci.acme.test")
    got = call(project, store, "integrate_system", system="jenkins", wants="builds started and finished",
               base_url="https://ci.acme.test", quote="Connect Jenkins so I can see builds", reason=WHY)
    draft = draft_dir(project, "jenkins_poller")
    assert got["ok"] and got["clearance"] == "needs approval" and got["dir"] == str(draft)
    assert not scaffolded(project, "jenkins_poller").exists() and (draft / "jenkins_poller.py").is_file()
    work = asked(store)
    assert len(work) == 1 and got["request"] == work[0]["request"] and "no template for Jenkins" in work[0]["requirements"]
    assert "jenkins_poller.py" in work[0]["requirements"]
    say(store, "Yes, you may read Jenkins, metadata only, token in JENKINS_TOKEN", 2)
    with pytest.raises(StoreError, match="has not landed at .* approve it once its ticket lands"):
        call(project, store, "approve_integration", name="jenkins_poller", token_env="JENKINS_TOKEN",
             quote="Yes, you may read Jenkins", reason=WHY)
    land(project, draft, "jenkins_poller")                                    # the worker's ticket lands it
    got = call(project, store, "approve_integration", name="jenkins_poller", token_env="JENKINS_TOKEN",
               quote="Yes, you may read Jenkins", reason=WHY)
    landed = scaffolded(project, "jenkins_poller")
    assert got["clearance"] == "approved" and not draft.exists()
    assert tomllib.loads((landed / "poller.toml").read_text())["clearance"] == "approved"
    entry = next(e for e in overlay(project)["machines"] if e["name"] == "jenkins_poller")
    assert str(landed / "jenkins_poller.py") in entry["command"]


def land(project, draft, name):
    """What the Coordinator's ticket does: the adapted script committed at .orchestra/machines/<name>/ on main."""
    import shutil
    shutil.copytree(draft, scaffolded(project, name))
    git(project.repo_root, "add", "-A", ".orchestra")
    git(project.repo_root, "commit", "-qm", f"{name}: the collector")


def test_jira_cloud_is_approved_with_a_token_file_and_the_accounts_email_by_reference(project, store, tmp_path):
    say(store, ASK + " (our site is acme.atlassian.net)")
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
    assert "HTTP Basic" not in said and "hand_to_coordinator" not in said     # the template speaks Cloud too (flavour): no code change
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
                       ({"token_file": str(project.repo_root / "secrets" / "jira-token")}, "inside the project.s own repository"),
                       ({"token_env": "JIRA_TOKEN", "user_env": "me@acme.test"}, "NAME of an environment variable"),
                       ({"token_env": "ghp_" + "a" * 36}, "credential itself|NAME of an environment variable")):
        with pytest.raises(StoreError, match=match):
            call(project, store, "approve_integration", name="jira_poller", quote="Yes, you may read Jira", reason=WHY, **bad)
    assert machine_toml(project)["clearance"] == "needs approval"


def test_a_store_takes_its_token_from_a_file(project, store, tmp_path):
    say(store, "Connect Chronosphere so I can see request rates by service, at https://acme-corp.chronosphere.io/data/metrics")
    call(project, store, "integrate_system", system="chronosphere", wants="request rates by service",
         base_url="https://acme-corp.chronosphere.io/data/metrics", settings={"match": ['{__name__="http_requests_total"}']},
         quote="Connect Chronosphere so I can see request rates by service", reason=WHY)
    say(store, "Yes, read Chronosphere, metadata only, the token is in a file", 2)
    call(project, store, "approve_integration", name="chronosphere", token_file=str(tmp_path / "chrono-token"),
         quote="Yes, read Chronosphere", reason=WHY)
    assert overlay(project)["sources"][0]["auth"] == {"bearer_file": str(tmp_path / "chrono-token")}


def fixed_when_marked(monkeypatch):
    """The scaffold's test stood in for: it passes once the script in its directory is fixed (a FIXED file)."""
    def run(target, name, **_):
        ok = (target / "FIXED").exists()
        return {"ran": True, "passed": ok, "summary": "3 passed" if ok else "1 failed", "command": "pytest",
                **({} if ok else {"tail": ["E assert"]})}
    monkeypatch.setattr(integrate_kits, "run_test", run)


def test_a_failed_test_fixed_in_place_is_finished_by_the_same_call(project, store, monkeypatch):
    fixed_when_marked(monkeypatch)
    got = connect(project, store, root="state")
    d = project.orchestra.state_dir / "machines" / "jira_poller"
    assert got["integrated"] is False and got["dir"] == str(d)
    again = connect(project, store, root="state")                           # not fixed yet: no second hand-off
    assert again["integrated"] is False and again["request"] == got["request"] and len(asked(store)) == 1
    assert "still does not pass" in " ".join(again["said"])
    (d / "FIXED").write_text("the worker's adaptation\n")
    (d / "jira_poller.py").write_text((d / "jira_poller.py").read_text() + "# adapted by the worker\n")
    done = connect(project, store, root="state")
    assert done["clearance"] == "needs approval" and done["request"] == got["request"] and len(asked(store)) == 1
    assert "# adapted by the worker" in (d / "jira_poller.py").read_text()       # kept, not scaffolded again
    assert layer(project)["machines"]["jira"]["aliases"] == ["bot.jira"] and done["changes"]
    assert design_integrate.integrations(store)["jira_poller"]["state"] == "needs approval"
    say(store, YES, 2)
    ok = call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN",
              quote="Yes, you may read Jira", reason=WHY)
    assert ok["clearance"] == "approved"


def test_a_failed_test_whose_ticket_landed_is_finished_from_the_landed_copy(project, store, monkeypatch):
    fixed_when_marked(monkeypatch)
    connect(project, store)
    draft = draft_dir(project)
    (draft / "FIXED").write_text("fixed\n")
    land(project, draft, "jira_poller")
    done = connect(project, store)
    landed = scaffolded(project)
    assert done["clearance"] == "needs approval" and done["dir"] == str(landed) and not draft.exists()
    say(store, YES, 2)
    call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN",
         quote="Yes, you may read Jira", reason=WHY)
    assert machine_toml(project)["clearance"] == "approved"
    assert str(landed / "jira_poller.py") in next(e for e in overlay(project)["machines"])["command"]


def jenkins(project, store):
    say(store, "Connect Jenkins so I can see builds, at https://ci.acme.test")
    return call(project, store, "integrate_system", system="jenkins", wants="builds started and finished",
                base_url="https://ci.acme.test", stock="Builds", quote="Connect Jenkins so I can see builds", reason=WHY)


def test_no_work_is_handed_on_before_the_declarations_hold(project, store, monkeypatch):
    def broken(*a, **k):
        raise StoreError("the declared layer refused it")
    monkeypatch.setattr(design_integrate, "_declare", broken)
    with pytest.raises(StoreError, match="refused it"):
        jenkins(project, store)
    assert not asked(store)
    assert not scaffolded(project, "jenkins_poller").exists() and not draft_dir(project, "jenkins_poller").exists()


def test_a_refusal_after_the_stock_names_the_stock(project, store, monkeypatch):
    def broken(*a, **k):
        raise StoreError("the effect was refused")
    monkeypatch.setattr(design_integrate, "_effect", broken)
    with pytest.raises(StoreError) as e:
        connect(project, store)
    said = str(e.value)
    sid = next(k for k, s in layer(project)["stocks"].items() if s["name"] == "Open issues")
    assert f"stock {sid} (Open issues) declared (" in said and "machine jira declared, heard as bot.jira" in said
    assert all(c["change"] in said for c in layer_changes(project))


def test_the_answer_lists_every_declaration(project, store):
    got = connect(project, store)
    assert sorted(got["changes"]) == sorted(c["change"] for c in layer_changes(project)) and len(got["changes"]) >= 3


def test_a_killed_calls_scaffold_is_replaced_and_a_tracked_one_kept(project, store):
    d = scaffolded(project)
    d.mkdir(parents=True)
    (d / "jira_poller.py").write_text("half written\n")
    got = connect(project, store)
    assert got["clearance"] == "needs approval" and "half written" not in (d / "jira_poller.py").read_text()
    s = scaffolded(project, "slack_poller")
    s.mkdir(parents=True)
    (s / "slack_poller.py").write_text("landed\n")
    git(project.repo_root, "add", "-A", str(s))
    git(project.repo_root, "commit", "-qm", "a landed poller")
    say(store, "Connect Slack", 3)
    with pytest.raises(StoreError, match="exists"):
        call(project, store, "integrate_system", system="slack", wants="messages per channel", quote="Connect Slack", reason=WHY)
    assert (s / "slack_poller.py").read_text() == "landed\n"


def test_every_kit_the_bridges_have_is_offered_and_github_is_its_own():
    from orchestra_agents.harness.tools import BY_NAME as DESIGN
    offered = DESIGN["integrate_system"].input_schema["properties"]["kit"]["enum"]
    assert set(offered) == set(integrate_kits.kits())                                 # the enum drifts from the bridges never
    assert integrate_kits.KIT_OF["github"] == "github" and integrate_kits.kit_for("github", "poller") == "github"


def test_jira_data_center_and_github_enterprise_are_settings_never_code(project, store):
    """R3 g (O1, O2): "connect our Jira Data Center and GitHub Enterprise" is answered with the
    kits' settings (bridges' templates/jira/poller.toml flavour, templates/github/poller.toml
    base_url), never "would need changes to the code"."""
    from pathlib import Path

    from orchestra_agents.harness.tools import BY_NAME
    text = BY_NAME["integrate_system"].description
    assert "Jira Data Center (settings flavour datacenter: REST API v2, a personal access token sent as Bearer)" in text
    assert "GitHub Enterprise (base_url https://<host>/api/v3)" in text and "no code changed" in text
    props = BY_NAME["integrate_system"].input_schema["properties"]
    assert "datacenter" in props["settings"]["properties"]["flavour"]["enum"] and "/api/v3" in props["base_url"]["description"]
    guide = " ".join((Path(design_integrate.__file__).parent / "guide.md").read_text().split())
    assert 'speaks **Jira Data Center** as well as Cloud: `flavour = "datacenter"`' in guide
    assert "personal access token sent as Bearer" in guide and '`base_url = "https://<host>/api/v3"`' in guide
    assert "would need changes" not in guide and "would need changes" not in text

    got = connect(project, store, base_url="https://jira.acme.test", settings={"jql": "project = OPS", "flavour": "datacenter"})
    assert machine_toml(project)["settings"]["flavour"] == "datacenter" and not asked(store)
    assert "hand_to_coordinator" not in str(got)
    say(store, "Connect our GitHub Enterprise so I can see pull requests, repository acme/service, at ghe.acme.test", 2)
    call(project, store, "integrate_system", system="github", wants="pull requests", settings={"repo": "acme/service"},
         base_url="https://ghe.acme.test/api/v3", quote="Connect our GitHub Enterprise so I can see pull requests",
         reason=WHY)
    assert machine_toml(project, "github_poller")["settings"]["base_url"] == "https://ghe.acme.test/api/v3"
    assert not asked(store)


def test_a_relays_declared_types_are_kept_before_they_are_heard(project, store):
    """R3 e (T2i s10): the Jira poller, approved but not yet run, has published nothing; a machine
    the owner names as listening to its transitions keeps them, marked the kit's, not heard yet.
    A type no poller declares is still left out until heard."""
    from design_fixtures import layer
    connect(project, store)
    say(store, "Create a machine called Ticket Watcher that listens for the Jira issue transitions", 2)
    out = call(project, store, "declare_machine", name="Ticket Watcher", kind="universal", description="Watches tickets",
               receives=["jira.issue.transitioned", "jira.issue.deleted"], quote="Create a machine called Ticket Watcher",
               reason=WHY, owner_named=[{"said": "the Jira issue transitions",
                                         "types": ["jira.issue.transitioned", "jira.issue.deleted"]}])
    assert layer(project)["machines"]["ticket_watcher"]["receives"] == ["jira.issue.transitioned"]
    said = " ".join(out["said"])
    assert ("'the Jira issue transitions' -> jira.issue.transitioned (the kit's, not heard yet: jira_poller declares it "
            "in [[publishes]])") in said
    assert "jira.issue.deleted never heard on the bus" in said
    assert "jira.issue.created" not in said                    # the kit's other types: neither added nor called unheard


def test_a_system_the_owner_has_not_named_is_never_scaffolded(project, store):
    say(store, "Connect our Jira so I can see issue transitions and open counts")     # Jira, never GitHub
    with pytest.raises(StoreError, match="has not named GitHub in their words.*Propose it in one line"):
        call(project, store, "integrate_system", system="github", wants="pull requests opened and merged", reason=WHY)
    assert not scaffolded(project, "github_poller").exists() and "github_poller" not in design_integrate.integrations(store)
    say(store, "Yes, our GitHub Enterprise too, for pull requests", 2)              # named in a later message: allowed
    got = call(project, store, "integrate_system", system="github", wants="pull requests opened and merged", reason=WHY)
    assert got["integration"] == "github_poller"


def test_the_token_file_is_checked_before_anything_is_scaffolded(project, store, tmp_path):
    with pytest.raises(StoreError, match=r"token_file: .*jira-token is refused: it is inside the project's own repository"):
        connect(project, store, token_file=str(project.repo_root / ".secrets" / "jira-token"))
    assert not scaffolded(project).exists() and "jira_poller" not in design_integrate.integrations(store)
    f = tmp_path / "outside" / "jira-token"
    got = connect(project, store, token_file=str(f))
    assert any("checked (no commit can carry them)" in line and str(f) in line for line in got["said"])
    say(store, "Yes, you may read Jira, metadata only", 2)
    call(project, store, "approve_integration", name="jira_poller", quote="Yes, you may read Jira", reason=WHY)
    assert machine_toml(project)["settings"]["token_file"] == str(f)              # the file named before, checked again


SLACK = "Connect Slack so I can see the help, dev and incident channels C0HKHELP, C0HKDEV and C0HKINC"
SLACK_YES = "Yes, you may read Slack, metadata only, the token's variable is SLACK_TOKEN"


def slack(project, store, **over):
    say(store, SLACK)
    args = {"system": "slack", "wants": "threads awaiting a reply per channel",
            "settings": {"channels": ["C0HKHELP", "C0HKDEV", "C0HKINC"]}, "quote": SLACK, "reason": WHY}
    return call(project, store, "integrate_system", **{**args, **over})


def test_an_address_the_owner_never_gave_is_never_used(project, store):
    """Bootstrap run 3: Slack was approved with the template's https://slack.com, which the owner never
    gave, and the first poll sent the test token there. A guess is refused; with no address the
    answer asks in one sentence, naming the template's value, and nothing is approved."""
    with pytest.raises(StoreError, match="never guessed"):
        slack(project, store, base_url="https://slack.com")
    assert not scaffolded(project, "slack_poller").exists()                     # nothing written
    got = slack(project, store)
    template = machine_toml(project, "slack_poller")["settings"]["base_url"]
    assert f"What is Slack's address, its API's base URL (the template's default is {template}" in got["ask_owner"]
    assert "Slack's base URL" in got["owner_must_set"]
    say(store, SLACK_YES, 2)
    for given in ({}, {"base_url": "https://slack.com"}):                      # the record's guess, or the Supervisor's
        with pytest.raises(StoreError, match="nothing was changed"):
            call(project, store, "approve_integration", name="slack_poller", token_env="SLACK_TOKEN",
                 quote="Yes, you may read Slack", reason=WHY, **given)
    assert machine_toml(project, "slack_poller")["clearance"] == "needs approval" and "machines" not in overlay(project)
    say(store, "Our Slack is the usual one, https://slack.com", 3)                # the owner's words: now it is theirs
    call(project, store, "approve_integration", name="slack_poller", token_env="SLACK_TOKEN", base_url="https://slack.com",
         quote="Our Slack is the usual one", reason=WHY)
    m = machine_toml(project, "slack_poller")
    assert m["clearance"] == "approved" and m["settings"]["base_url"] == "https://slack.com"
    rec = design_integrate.integrations(store)["slack_poller"]
    assert rec["base_url_from"] == "the owner's words"


def test_an_address_on_this_machine_is_no_guess(project, store):
    got = slack(project, store, base_url="http://127.0.0.1:8931")
    assert "address" not in got["ask_owner"]
    say(store, SLACK_YES, 2)
    call(project, store, "approve_integration", name="slack_poller", token_env="SLACK_TOKEN",
         quote="Yes, you may read Slack", reason=WHY)
    assert design_integrate.integrations(store)["slack_poller"]["base_url_from"] == "this machine"


def test_a_scaffold_is_committed_on_the_supervisors_branch_and_the_checkout_stays_clean(project, store):
    """Bootstrap run 3: the 7 pollers' scaffolds (28 files under .orchestra/machines/) were left
    untracked on main. Each scaffold's own files are one commit on orchestra/supervisor; the live
    copy is ignored in this checkout only, and git status is what the owner left."""
    head = git(project.repo_root, "rev-parse", "HEAD")
    got = connect(project, store)
    assert git(project.repo_root, "status", "--porcelain") == ""
    assert git(project.repo_root, "rev-parse", "HEAD") == head and git(project.repo_root, "branch", "--show-current").strip() == "main"
    on_branch = set(git(project.repo_root, "ls-tree", "-r", "--name-only", "orchestra/supervisor", "--",
                        ".orchestra/machines/jira_poller").splitlines())
    rel = ".orchestra/machines/jira_poller/"
    assert on_branch == {rel + f for f in ("AGENTS.md", "jira_poller.py", "poller.toml", "test_jira_poller.py")}
    assert any("one commit on orchestra/supervisor" in line for line in got["said"])
    assert (scaffolded(project) / "jira_poller.py").is_file()                   # still live where it runs
    assert "/.orchestra/machines/jira_poller/" in (project.repo_root / ".git" / "info" / "exclude").read_text()
    say(store, YES, 2)
    call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN",
         quote="Yes, you may read Jira", reason=WHY)
    shown = git(project.repo_root, "show", f"orchestra/supervisor:{rel}poller.toml")
    assert 'clearance = "approved"' in shown and git(project.repo_root, "status", "--porcelain") == ""
    git(project.repo_root, "merge", "-q", "orchestra/supervisor")             # the pull request merged and pulled
    assert git(project.repo_root, "ls-files", rel).splitlines() and git(project.repo_root, "status", "--porcelain") == ""


def _core_machines(project) -> dict:
    """The Machines page's answer as Orchestra builds it: the bus's machines, with the declared layer merged."""
    from orchestra.events import Events as CoreEvents
    from orchestra.sd.intended.machines_view import merge
    from orchestra.web.machines import machines_view
    ev = CoreEvents.for_project(project.orchestra)
    out = machines_view(ev.bus.read(0, 10_000)[0])
    merge(out, project.orchestra.intended)
    return {m["id"]: m for m in out["machines"]}


def test_a_relayed_system_is_heard_once_its_poller_publishes_as_its_actors(project, store, tmp_path):
    """Bootstrap run 7, finding 3: Jira, GitHub and Slack were "declared · never heard" beside some 120
    of their events, published as person.<system>.<account> and bot.<system>.<account>, never as the
    guessed bot.<system>. The poller publishes as who acted; each actor heard is joined to the system."""
    from orchestra.events import Events as CoreEvents
    from orchestra_bridges.kits.poller import Poller, actor_source

    from orchestra_agents import integrate_heard
    connect(project, store)
    seen = _core_machines(project)
    assert seen["jira"]["unseen"] and seen["jira"]["comparison"]                 # nothing of Jira on the bus yet
    p = Poller("jira_poller", 300, tmp_path / "poll", events=CoreEvents.for_project(project.orchestra), project=project.orchestra)
    p.emit("jira.issue.assigned", "OPS-1", {"assignee": "amara"}, source=actor_source("jira", "amara"))
    p.emit("jira.issue.transitioned", "OPS-1", {"to": "Done"}, source=actor_source("jira", "sail", bot=True))
    assert _core_machines(project)["jira"].get("unseen")                        # the guessed alias alone is never heard
    said = integrate_heard.sweep(project, store)
    assert said == ["machine jira heard as bot.jira.sail, person.jira.amara"]
    assert layer(project)["machines"]["jira"]["aliases"] == ["bot.jira", "bot.jira.sail", "person.jira.amara"]
    seen = _core_machines(project)
    assert "jira" not in seen                                                   # no "declared · never heard" card
    assert seen["bot.jira.sail"]["comparison"] == "heard" and seen["bot.jira.sail"]["intent"]   # on its first alias heard
    assert integrate_heard.sweep(project, store) == []                           # each joined once
