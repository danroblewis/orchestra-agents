"""The owner connects Jira through the chat, with the fake Claude Code (task I3): a real host, each
turn a fake `claude -p` whose tool calls go through the real MCP server (or, with `[agents]
tools_via = "bash"`, the session's shell). The first turn scaffolds a poller from the bridges' Jira
kit into the state directory, runs its own test against the kit's FakeJira (for real, in a child
Python), declares Jira on the Machines page as a machine managed elsewhere heard as `bot.jira`, and
asks the owner for the approval and the token's variable name. The second turn is the owner's yes:
the clearance and the name go into the poller's machine.toml and its `[[machines]]` and
`[[collect]]` entries into the owner's overlay. No real system is read and no token exists."""

import json
import shutil
import stat
import tomllib
from pathlib import Path

import pytest
from conftest import agents_project
from design_fixtures import core_endpoint
from fake_tasks import FakeTasks
from test_design_e2e import answers, git

from orchestra_agents import intended_api
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore

FAKE = Path(__file__).parent / "fake_claude.py"
ASK = "connect our Jira so I can see issue transitions and open counts"
YES = ("Yes, you may read Jira, metadata only, with a read-only token. It is in JIRA_TOKEN, the site is "
       "https://jira.acme.test, and the project is OPS")
WHY = "the owner asked to connect Jira in the chat"


@pytest.fixture(params=["mcp", "bash"])
def chat(request, tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    for k in ("ORCHESTRA_EVENTS_ENDPOINT", "ORCHESTRA_EVENTS_TOKEN", "JIRA_TOKEN", "JIRA_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    fake = tmp_path / "claude"
    shutil.copy(FAKE, fake)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "supervisor.json"
    monkeypatch.setenv("CLAUDE_BINARY", str(fake))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("FAKE_CLAUDE_SUPERVISOR", str(script))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    project = agents_project(repo, harness="claude_code")
    toml = repo / "orchestra.toml"
    toml.write_text(toml.read_text() + f'tools_via = "{request.param}"\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    project.reload_config()
    monkeypatch.setenv("ORCHESTRA_REPO", str(repo))
    events = Events.for_project(project)
    host = AgentHost(project, AgentStore(project, events), events)
    host._refresh_org(host.store.runtime()["agent_state"])
    with FakeTasks(project), core_endpoint(project):
        def say(message: str, steps: list[dict]) -> list[dict]:
            script.write_text(json.dumps(steps))
            out = host.prompt_supervisor(message, lambda e: None)
            assert not out.get("error"), out
            got = answers(out["text"])
            assert [a["tool"] for a in got] == [s["tool"] for s in steps]
            for a in got:
                assert a.get("ok") is True, a
            return got
        try:
            yield say, project, repo
        finally:
            host.residents.close_all()


def test_connect_our_jira_yields_a_declared_tested_poller_scheduled_on_the_owners_word(chat):
    say, project, repo = chat
    head = git(repo, "rev-parse", "HEAD")
    turn1 = say(ASK, [
        {"tool": "read_design", "args": {}},
        {"tool": "integrate_system", "args": {
            "system": "jira", "wants": "issue transitions and open counts", "stock": "Open issues", "unit": "issues",
            "quote": ASK, "reason": WHY}}])
    got = turn1[1]
    d = project.orchestra.repo_root / ".orchestra" / "machines" / "jira_poller"
    assert got["integration"] == "jira_poller" and got["dir"] == str(d)
    assert got["test"]["passed"] is True and "passed" in got["test"]["summary"], got["test"]       # against FakeJira, for real
    m = tomllib.loads((d / "machine.toml").read_text())
    assert m["clearance"] == "needs approval" and "wanted" not in m           # the owner's words are the integration's record
    jira = intended_api.current(project)["machines"]["jira"]
    assert jira["managed"] == "external" and jira["aliases"] == ["bot.jira"] and jira["spec"]["source"] == "bot.jira"
    assert jira["state"] == "proposed" and jira["emits"] == ["jira.issue.transitioned"] and jira["expected_effect"]
    assert "jira_poller" not in intended_api.current(project)["machines"]
    assert "approval" in " ".join(got["owner_must_set"]) and "base URL" in got["ask_owner"]       # the clearance request
    assert "which Jira projects" in got["ask_owner"]
    assert "environment variable" in got["ask_owner"] and "never the token" in got["ask_owner"]
    assert "[[machines]] jira_poller, restart always" in got["planned"]       # a kept process, never a machine of the floor
    project.reload_config()
    assert not project.orchestra.config.raw.get("machines") and not project.orchestra.config.raw.get("workflows")   # not before the yes

    turn2 = say(YES, [{"tool": "approve_integration", "args": {
        "name": "jira_poller", "token_env": "JIRA_TOKEN", "base_url": "https://jira.acme.test", "settings": {"jql": "project = OPS"},
        "quote": "Yes, you may read Jira, metadata only", "reason": WHY}}])
    assert turn2[0]["clearance"] == "approved" and turn2[0]["token_env"] == "JIRA_TOKEN"
    m = tomllib.loads((d / "machine.toml").read_text())
    assert m["clearance"] == "approved" and m["settings"]["token_env"] == "JIRA_TOKEN"
    assert m["settings"]["base_url"] == "https://jira.acme.test" and m["settings"]["jql"] == "project = OPS"
    project.reload_config()
    raw = project.orchestra.config.raw
    entry = next(e for e in raw["machines"] if e["name"] == "jira_poller")
    assert entry["restart"] == "always" and str(d / "jira_poller.py") in entry["command"]
    assert not raw.get("workflows") and not (d / "machine.json").exists()
    assert next(e for e in raw["collect"] if e["name"] == "jira_poller")["command"].endswith("measure")
    assert "machines" not in tomllib.loads((repo / "orchestra.toml").read_text())                  # the overlay, not the repo
    assert git(repo, "rev-parse", "HEAD") == head                                                  # nothing committed

