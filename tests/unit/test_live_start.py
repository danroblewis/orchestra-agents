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
