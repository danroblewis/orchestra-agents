"""The Supervisor beside a live example, from a function (orchestra_agents.live): what the live
delivery test and the Workflows example's --supervisor option call, nothing patched, no model run."""

import subprocess
import tomllib

import pytest
from conftest import init_orchestra

from orchestra_agents import live
from orchestra_agents.config import load_project
from orchestra_agents.store import AgentStore


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    r = tmp_path / "target"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
    init_orchestra(r)
    return r


def test_slack_follows_the_token_file_unless_off(tmp_path):
    token = tmp_path / "slack-token"
    assert live.slack_on("auto", token) is False                     # no file
    token.write_text("")
    assert live.slack_on("auto", token) is False                     # empty
    token.write_text("a token")
    assert live.slack_on("auto", token) is True and live.slack_on("off", token) is False
    with pytest.raises(ValueError, match="auto or off"):
        live.slack_on("on", token)


def test_start_supervisor_sets_agents_and_writes_its_spec(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(live, "SLACK_TOKEN", tmp_path / "none")
    out = live.start_supervisor(repo, role=["design", "observe"], model="haiku", via="bash", slack="off")
    assert out["slack"] is False and out["tools"] == ["design", "observe"]
    agents = tomllib.loads((repo / "orchestra.toml").read_text())["agents"]
    assert agents == {"harness": "claude_code", "model": "haiku", "serves": [], "tools_via": "bash"}
    role = (repo / ".orchestra" / "roles" / "supervisor.json").read_text()
    assert '"design"' in role and '"observe"' in role
    assert (repo / ".orchestra" / "machines" / "supervisor").is_dir()
    project = load_project(repo)
    assert AgentStore(project).agent_state("supervisor")["setup"]["harness"] == "claude_code"


def test_the_daily_cap_pauses_the_supervisor_with_why_and_resumes_it_the_next_day(repo):
    from orchestra_agents import budget
    from orchestra_agents.org_store import all_agents
    from orchestra_agents.org_views import agent_view

    project = load_project(repo)
    store = AgentStore(project)
    day1, day2 = 1_800_000_000.0, 1_800_000_000.0 + 86_400
    assert live.cap_supervisor(repo, 2.0, t=day1) == {"paused": False, "why": None, "usd": 0.0, "changed": False}
    budget.record(store, 1.5, initiative=False, t=day1)
    budget.record(store, 0.75, initiative=False, t=day1)          # the owner's chat counts too
    got = live.cap_supervisor(repo, 2.0, t=day1)
    assert got["paused"] and got["changed"] and "$2.25" in got["why"] and "$2.00" in got["why"]
    st = store.agent_state("supervisor")
    assert st["paused"] and st["paused_by"] == live.CAPPED_BY and st["paused_why"] == got["why"]
    view = agent_view(project, store, all_agents(project)["supervisor"])
    assert view["paused"] and view["paused_why"] == got["why"]      # the Agents page's chip says why
    assert live.cap_supervisor(repo, 2.0, t=day1)["changed"] is False          # said once
    evs, _ = store.events.read(0)
    assert [e.get("by") for e in evs if e["type"] == "agent.paused"] == [live.CAPPED_BY]
    got = live.cap_supervisor(repo, 2.0, t=day2)                    # a new day: resumed
    assert got == {"paused": False, "why": None, "usd": 0.0, "changed": True}
    assert not store.agent_state("supervisor")["paused"]
    evs, _ = store.events.read(0)
    assert any(e["type"] == "agent.resumed" and e.get("by") == live.CAPPED_BY for e in evs)


def test_the_daily_cap_never_undoes_a_pause_it_did_not_make(repo):
    store = AgentStore(load_project(repo))
    store.set_agent_state("supervisor", paused=True)
    got = live.cap_supervisor(repo, 2.0, t=1_800_000_000.0)
    assert got["paused"] is True and got["changed"] is False and store.agent_state("supervisor")["paused"]
