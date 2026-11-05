"""``[agents] ask`` (ask.py; the owner, 2026-11-04: "I want the Supervisor to do most things without
asking me"). Under ``outside``, the default, the Supervisor acts on its own reason and says what it
did: a ``match`` widened within ``explore`` is applied, a read-only kit is approved, a workflow goes
to the repository, work is requested as the Supervisor's own, a shipped program is declared; and its
prompt asks the owner first for no change that stays on this machine. Held in every mode: a kit
that writes, an Outbox draft for the pull request, a push, ``explore`` itself, a wider ``match`` past
``explore``, and a machine's confirm (Orchestra's declared layer settles a proposal only by the
owner). Under ``always`` the old refusals stand."""

import re

import pytest
from conftest import agents_project
from design_fixtures import WHY, call, core_endpoint, git, layer, say

from orchestra_agents import (
    ask,
    design_integrate,
    integrate_kits,
    promises,
    prompting,
    push_guard,
    request_work,
    skills,
    source_limits,
    supervisor_pr,
    workflow_tools,
)
from orchestra_agents.config import Config, ConfigError
from orchestra_agents.events import Events
from orchestra_agents.org import built_in_agents
from orchestra_agents.store import AgentStore, StoreError

pytest_plugins = ["design_fixtures"]

SOURCE = ('\n[[sources]]\nname = "metrics"\nurl = "http://127.0.0.1:9/"\nmatch = [\'{namespace="team"}\']\n'
          'explore = [\'{namespace="team"}\', \'{namespace="ops"}\']\n')
ENTRY = {"name": "metrics", "url": "http://127.0.0.1:9/", "match": ['{namespace="team"}'],
         "explore": ['{namespace="team"}', '{namespace="ops"}']}
WIDER = {**ENTRY, "match": ['{namespace="team"}', '{namespace="ops"}']}          # within explore
PAST = {**ENTRY, "match": ['{namespace="team"}', '{env="prod"}']}                # past explore
DECIDED = "the worker role was unfilled and the owner asked for code work"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """design_fixtures' project, defined here: this file registers no plugin, so it never changes which
    project fixture the files that register test_request_work's get."""
    monkeypatch.delenv("ORCHESTRA_EVENTS_ENDPOINT", raising=False)
    monkeypatch.delenv("ORCHESTRA_EVENTS_TOKEN", raising=False)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    with core_endpoint(p):
        yield p


@pytest.fixture
def store(project):
    s = AgentStore(project, Events.for_project(project, source="supervisor"))
    s.set_agent_state("supervisor", metrics_listed={"at": 1.0, "session": None})
    return s


def set_ask(project, mode: str | None = None, extra: str = "") -> None:
    toml = project.repo_root / "orchestra.toml"
    text = re.sub(r'\nask = "[a-z]+"', "", toml.read_text())
    if mode:
        text = text.replace("[agents]\n", f'[agents]\nask = "{mode}"\n', 1)
    toml.write_text(text + extra)
    project.reload_config()


def test_the_setting_is_outside_by_default_always_on_request_and_nothing_else():
    assert Config.from_raw({}).ask == "outside" and Config.from_raw({"agents": {"ask": "always"}}).ask == "always"
    with pytest.raises(ConfigError, match="outside .* or always"):
        Config.from_raw({"agents": {"ask": "never"}})


# what the prompt says


PERMISSION = re.compile(r"on their yes|their yes, quoted|quoted yes|ask the owner in one sentence|shall i |"
                        r"propose (it )?in one sentence|never fill it on your own|ask in one sentence first", re.IGNORECASE)
OUTSIDE_WRITE = re.compile(r"pull request|outbox|push|graduat|writes to the system|write outside", re.IGNORECASE)
# the no-question rule (T69) quotes the questions it forbids: a sentence that forbids asking asks nothing
FORBIDS_ASKING = re.compile(r"^Never end with ")


def _text(project) -> str:
    agent = built_in_agents()["supervisor"]
    return "\n".join([prompting.agent_prompt(project, agent)] + [skills.text(project, n) for n in skills.names()])


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n\s*\n|\n- ", re.sub(r"[ \t]*\n[ \t]*(?![-\d])", " ", text)) if s.strip()]


def test_under_outside_the_prompt_asks_first_only_before_a_write_outside_this_machine(project):
    set_ask(project)
    text = _text(project)
    asks = [s for s in _sentences(text) if PERMISSION.search(s) and not OUTSIDE_WRITE.search(s)
            and not FORBIDS_ASKING.search(s)]
    assert asks == []
    assert "Decide and act, then say" in text and "[[act]]" not in text and "[[ask]]" not in text
    assert "widens `match` within `explore`" in _flat(text) and "`approve_integration` with what they gave and your reason" in _flat(text)


def test_under_always_the_prompt_asks_as_before(project):
    set_ask(project, "always")
    text = _text(project)
    assert "**ask the owner in one sentence** when it widens `match`" in text
    assert "On their yes, quoted, `approve_integration`" in _flat(text) and "Decide and act" not in text
    assert "[[act]]" not in text and "[[ask]]" not in text


# a wider match, within explore


def test_under_outside_a_match_widened_within_explore_is_applied_and_past_it_refused(project, store):
    set_ask(project, extra=SOURCE)
    say(store, None)                                                     # a wake-up: an explore round
    source_limits.check_scopes(project, store, [WIDER])
    source_limits.check_declared(project, store, [WIDER])
    with pytest.raises(StoreError, match="match widens"):
        source_limits.check_scopes(project, store, [PAST])
    with pytest.raises(StoreError, match="explore .* is the owner's alone"):
        source_limits.check_scopes(project, store, [{**WIDER, "explore": [*ENTRY["explore"], '{env="prod"}']}])
    say(store, "read more of it if the model needs it", 2)              # the owner's turn, no limits in it
    source_limits.check_declared(project, store, [WIDER])
    with pytest.raises(StoreError, match="has not given this store's limits"):
        source_limits.check_declared(project, store, [PAST])
    out = call(project, store, "set_setting", key="sources", value=[WIDER], target="overlay", reason=WHY)
    assert out["ok"]
    got = next(e for e in project.orchestra.config.raw["sources"] if e["name"] == "metrics")
    assert got["match"] == WIDER["match"] and got["explore"] == ENTRY["explore"]


def test_under_always_a_wider_match_waits_for_the_owner(project, store):
    set_ask(project, "always", extra=SOURCE)
    say(store, None)
    with pytest.raises(StoreError, match="match widens"):
        source_limits.check_scopes(project, store, [WIDER])
    say(store, "read more of it if the model needs it", 2)
    with pytest.raises(StoreError, match="has not given this store's limits"):
        source_limits.check_declared(project, store, [WIDER])


# approve_integration


ASKED = "Connect our Jira so I can see issue transitions and open counts, project OPS at https://jira.acme.test"


@pytest.fixture
def passing(monkeypatch):
    monkeypatch.setattr(integrate_kits, "run_test", lambda target, name, **_: {
        "ran": True, "passed": True, "summary": "3 passed", "command": "pytest"})


def connect(project, store):
    say(store, ASKED)
    return call(project, store, "integrate_system", system="jira", wants="issue transitions and open counts",
                settings={"jql": "project = OPS"}, base_url="https://jira.acme.test", stock="Open issues", unit="issues",
                quote="Connect our Jira so I can see issue transitions and open counts", reason=WHY)


def test_under_outside_a_read_only_kit_is_approved_on_the_supervisors_reason(project, store, passing):
    set_ask(project)
    got = connect(project, store)
    assert "I will read Jira" in got["ask_owner"] and "May I" not in got["ask_owner"]
    assert promises.unkept(got["ask_owner"], ["integrate_system"]) is None     # relayed, it waits on the owner's answer
    assert not any("approval, in their words" in m for m in got["owner_must_set"])
    say(store, "The token is in JIRA_TOKEN", 2)
    out = call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN",
               reason="the owner named the token's variable; the kit only reads")
    assert out["ok"] and out["clearance"] == "approved"
    rec = design_integrate.integrations(store)["jira_poller"]["approved"]
    assert rec["by"] == "supervisor" and rec["read_only"] and "only reads" in rec["reason"] and "quote" not in rec


def test_a_kit_that_writes_takes_the_owners_quoted_yes_in_every_mode(project, store, passing):
    set_ask(project)
    connect(project, store)
    rec = design_integrate.integrations(store)["jira_poller"]
    design_integrate._save(store, "jira_poller", {**rec, "writes": True})
    say(store, "Yes, you may read and write Jira with the token in JIRA_TOKEN", 2)
    with pytest.raises(StoreError, match="write outside this machine is the owner's in every mode"):
        call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN", reason=WHY)
    assert design_integrate.integrations(store)["jira_poller"]["state"] == "needs approval"
    out = call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN",
               quote="Yes, you may read and write Jira", reason=WHY)
    assert design_integrate.integrations(store)["jira_poller"]["approved"]["by"] == "owner" and out["ok"]


def test_under_always_approval_is_the_owners_alone(project, store, passing):
    set_ask(project, "always")
    got = connect(project, store)
    assert "May I read Jira" in got["ask_owner"]
    with pytest.raises(StoreError, match="the owner's alone"):
        call(project, store, "approve_integration", name="jira_poller", token_env="JIRA_TOKEN", reason=WHY)


# declare_workflow, request_work, set_agent_roles


@pytest.fixture
def plugin(monkeypatch, project):
    from test_request_work import Plugin
    from test_workflow_tools import DEF
    f = Plugin()
    monkeypatch.setattr(workflow_tools, "plugin", f.ns)
    monkeypatch.setattr(request_work, "floor_down", lambda project: None)
    f.write(project.orchestra, DEF)
    return f


def test_under_outside_declare_workflow_takes_a_reason_for_the_repository(project, store, plugin):
    from test_workflow_tools import DEF
    set_ask(project)
    say(store, "Declare a two-step workflow: write then review")
    out = call(project, store, "declare_workflow", definition={**DEF, "name": "other"}, root="repository", reason=DECIDED)
    assert out["ok"] and ".orchestra" in str(out.get("path") or out)
    set_ask(project, "always")
    with pytest.raises(StoreError, match="root repository is the owner's choice"):
        call(project, store, "declare_workflow", definition={**DEF, "name": "third"}, root="repository", reason=DECIDED)


def test_under_outside_request_work_is_the_supervisors_own_with_its_reason(project, store, plugin):
    set_ask(project)
    say(store, "The tests are slow")                                      # no words asking for this work
    out = call(project, store, "request_work", workflow="write_review", brief="Make the test suite run in parallel",
               reason=DECIDED)
    assert out["source"] == "supervisor"
    ev = next(e for e in store.events.read(0, 500)[0] if e["type"] == "item.requested")
    assert ev["source"] == "supervisor" and ev.get("reason") == DECIDED and "via" not in ev
    out = call(project, store, "request_work", workflow="write_review", brief="Make the test suite run in parallel",
               quote="The tests are slow", reason=DECIDED)
    assert out["source"] == "owner"                                       # with their words it is theirs, in every mode
    set_ask(project, "always")
    with pytest.raises(StoreError, match="owner's to ask"):
        call(project, store, "request_work", workflow="write_review", brief="Make the test suite run in parallel",
             reason=DECIDED)


def test_an_unfilled_role_is_filled_not_proposed_under_outside(project):
    set_ask(project)
    assert "Make the AI agents fill it (set_agent_roles, with your reason)" in \
        request_work._one_off_notes(project, "tester", "x-1", [])["unfilled"]
    set_ask(project, "always")
    assert "Propose in one sentence" in request_work._one_off_notes(project, "tester", "x-1", [])["unfilled"]


def test_set_agent_roles_takes_a_reason_and_no_quote(project, store):
    set_ask(project)
    out = call(project, store, "set_agent_roles", serves=["worker"], target="overlay", reason=DECIDED)
    assert out["ok"] and project.config.serves == ["worker"]


# a machine's confirm stays the owner's: Orchestra's declared layer


def test_a_machines_confirm_stays_the_owners_under_outside(project, store):
    set_ask(project)
    say(store, "Declare a machine that writes release notes every day")
    call(project, store, "declare_machine", name="notes_writer", title="Release notes writer", kind="universal",
         description="writes the release notes each day", mode="ad hoc", receives=["git.commit"], reason=WHY)
    assert layer(project)["machines"]["notes_writer"]["state"] == "proposed"
    with pytest.raises(StoreError, match="owner's to ask"):
        call(project, store, "confirm", element="machine:notes_writer", reason=DECIDED)
    assert layer(project)["machines"]["notes_writer"]["state"] == "proposed"


# writes outside this machine, held in every mode


def test_the_pull_request_and_a_push_stay_gated_under_outside(project, store, monkeypatch):
    set_ask(project)
    assert ask.acts(project)
    assert push_guard.blocked("worker", project.config.allow_push)        # a session's push, unless allow_push names it
    drafts = []

    class Outbox:
        def draft(self, kind, **kw):
            drafts.append(kind)
            return {"id": "d-1", "state": "pending"}
    monkeypatch.setattr(supervisor_pr, "outbox_installed", lambda: True)
    monkeypatch.setattr(supervisor_pr, "outbox_client", lambda project, drafter: Outbox())
    out = call(project, store, "set_capacity_limit", workers=2, reason=DECIDED)
    assert out.get("pull_request", {}).get("number") is None              # nothing opened as the owner here
