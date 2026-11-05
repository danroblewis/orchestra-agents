"""The D5 findings (2026-11-04): ``[agents] model`` (and ``provider``) sets the Supervisor's model at
start on every harness, over the shipped default in its spec, and its card says what it runs on; the
owner's chat turns have a spend cap of their own (``chat_usd_per_day``), refused before any turn
with what was spent and how to go on."""

import subprocess

import pytest
from conftest import FakeHarness, agents_project
from fake_tasks import FakeTasks

from orchestra_agents import budget, supervisor_spec
from orchestra_agents.config import Config, ConfigError
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.machine_spec import load as load_spec
from orchestra_agents.org_views import agents_status, runs_on
from orchestra_agents.store import AgentStore, StoreError


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def make(tmp_path, monkeypatch, **kw):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo, **kw)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    supervisor_spec.ensure(p)
    return p


def host_on(project, reply="ok"):
    store, events = AgentStore(project), Events.for_project(project)
    h = AgentHost(project, store, events)
    h.harness = FakeHarness(reply=reply)
    h.harness.model_for = lambda wanted: wanted              # a harness that takes the spec's name, as Claude Code does
    h._refresh_org(store.runtime()["agent_state"])
    return h


def test_agents_model_overrides_the_supervisor_s_shipped_default(tmp_path, monkeypatch):
    p = make(tmp_path, monkeypatch, model="claude-haiku-4-5")
    assert load_spec(p, "supervisor").model == supervisor_spec.DEFAULT_MODEL == "sonnet"
    h = host_on(p)
    assert h.model_of("supervisor") == "claude-haiku-4-5" == h.model_of("coordinator")
    proc = h._session_for(h.agents["supervisor"], "supervisor")
    assert proc.spec.model == "claude-haiku-4-5"                       # the session starts on it


def test_without_agents_model_the_spec_s_default_stands(tmp_path, monkeypatch):
    p = make(tmp_path, monkeypatch)
    assert not p.config.model_set
    assert host_on(p).model_of("supervisor") == "sonnet"


def test_a_model_written_into_the_spec_since_is_a_choice_and_stands(tmp_path, monkeypatch):
    p = make(tmp_path, monkeypatch, model="claude-haiku-4-5")
    spec = load_spec(p, "supervisor")
    monkeypatch.setattr("orchestra_agents.machine_spec.load", lambda pr, n: type(spec)(**{**spec.__dict__, "model": "opus"})
                        if n == "supervisor" else None)
    assert host_on(p).model_of("supervisor") == "opus"


def test_a_provider_starts_the_supervisor_with_its_model(tmp_path, monkeypatch):
    """As on Pi ([agents] harness = "pi", provider = "anthropic"): a unit test never opens the real
    harness, so the fake stands in for it, taking no alias, as Pi takes none."""
    p = make(tmp_path, monkeypatch, model="claude-sonnet-5", provider="anthropic")
    h = host_on(p)
    h.harness.model_for = lambda wanted: None
    proc = h._session_for(h.agents["supervisor"], "supervisor")
    assert (proc.spec.model, proc.spec.provider) == ("claude-sonnet-5", "anthropic")


def test_the_supervisor_s_card_says_what_it_runs_on(tmp_path, monkeypatch):
    p = make(tmp_path, monkeypatch, harness="dummy", model="claude-haiku-4-5")
    on = runs_on(p)["runs_on"]
    assert on == {"harness": "dummy", "provider": None, "model": "claude-haiku-4-5", "model_from": "[agents] model",
                  "tools_via": p.config.tools_via}
    sup = next(a for a in agents_status(p, AgentStore(p))["agents"] if a["name"] == "supervisor")
    assert sup["runs_on"]["model"] == "claude-haiku-4-5"
    p2 = make(tmp_path / "b", monkeypatch, harness="dummy")
    assert runs_on(p2)["runs_on"]["model_from"] == "its spec"          # the dummy records the spec's name


def test_chat_usd_per_day_is_a_setting():
    assert Config.from_raw({"agents": {"harness": "dummy"}}).chat_usd_per_day == 10.0
    assert Config.from_raw({"agents": {"harness": "dummy", "chat_usd_per_day": 0}}).chat_usd_per_day == 0.0
    with pytest.raises(ConfigError, match="chat_usd_per_day"):
        Config.from_raw({"agents": {"harness": "dummy", "chat_usd_per_day": -1}})


def test_past_the_chat_cap_a_message_is_refused_before_any_turn(tmp_path, monkeypatch):
    p = make(tmp_path, monkeypatch)
    h = host_on(p, reply="here is what changed")
    h.cfg.chat_usd_per_day = 1.0
    budget.record(h.store, 0.6, initiative=False, chat=True)
    budget.record(h.store, 5.0, initiative=True)                       # its own initiative is the other cap's
    assert budget.chat_over(h.store, h.cfg) is None
    with FakeTasks(p):
        assert h.prompt_supervisor("what changed?", lambda ev: None)["text"] == "here is what changed"
    assert budget.spent(h.store)["chat_turns"] == 2          # the recorded one, and this turn
    budget.record(h.store, 0.5, initiative=False, chat=True)
    sessions = len(h.harness.sessions)
    with pytest.raises(StoreError) as e:
        h.prompt_supervisor("and now?", lambda ev: None)
    assert "$1.10 today" in str(e.value) and "chat_usd_per_day" in str(e.value) and "not sent" in str(e.value)
    assert len(h.harness.sessions) == sessions and all("and now?" not in x for s in h.harness.sessions for x in s.prompts)
    h.cfg.chat_usd_per_day = 0                                         # 0: no cap
    assert budget.chat_over(h.store, h.cfg) is None
