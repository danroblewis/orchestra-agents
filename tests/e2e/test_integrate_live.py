"""A live run of "connect our Jira" with a real model (task I3): Claude Code on Haiku as the
Supervisor, with its real prompt and a narrowed tool set (``design``, ``observe``, ``guides``), on
Orchestra's declared layer served in process. Turn one should call ``integrate_system`` (the kit's
Jira poller scaffolded and tested against FakeJira, Jira declared); turn two, the owner's yes,
``approve_integration`` with the variable's name. Nothing real is read and no token exists: the
site is a placeholder host and the variable is never set.

It costs money and needs a logged-in ``claude``, so it runs only when asked:

    ORCHESTRA_LIVE_HAIKU=1 uv run python -m pytest -q -s tests/e2e/test_integrate_live.py

It prints each reply, the tools called and the cost (from Claude Code's own ``result`` events)."""

import json
import os
import shutil
import stat
import subprocess
import tomllib

import pytest
from conftest import agents_project
from design_fixtures import core_endpoint

pytestmark = pytest.mark.skipif(os.environ.get("ORCHESTRA_LIVE_HAIKU") != "1" or not shutil.which("claude"),
                                reason="a live run on Haiku: set ORCHESTRA_LIVE_HAIKU=1, with a logged-in claude on the PATH")
ASK = "Connect our Jira so I can see issue transitions and open counts."
YES = ("Yes, you may read Jira: metadata only, with a read-only token. The variable is JIRA_TOKEN, the site is "
       "https://jira.acme.test, and follow the project OPS.")
GROUPS = ("design", "observe", "guides")


def test_haiku_connects_jira_and_asks_for_the_variable(tmp_path, monkeypatch):
    from orchestra_agents import access, intended_api, prompting
    from orchestra_agents.events import Events
    from orchestra_agents.harness import SessionSpec, get_harness
    from orchestra_agents.org_store import all_agents
    from orchestra_agents.store import AgentStore

    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    for k in ("ORCHESTRA_EVENTS_URL", "ORCHESTRA_METRICS_URL", "ORCHESTRA_SOURCE", "ORCHESTRA_ROLE", "ORCHESTRA_EVENTS_ENDPOINT",
              "ORCHESTRA_EVENTS_TOKEN", "JIRA_TOKEN", "JIRA_API_TOKEN", access.TOKEN_ENV):
        monkeypatch.delenv(k, raising=False)
    intended_api._cache.clear()
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    project = agents_project(repo, harness="claude_code", model="haiku")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    project.reload_config()

    log = tmp_path / "claude-stream.jsonl"
    wrapper = tmp_path / "claude"
    wrapper.write_text(f'#!/bin/bash\n"{shutil.which("claude")}" "$@" | tee -a "{log}"\nexit ${{PIPESTATUS[0]}}\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CLAUDE_BINARY", str(wrapper))

    store = AgentStore(project, Events.for_project(project, source="supervisor"))
    sid = "supervisor-live-integrate"
    with core_endpoint(project):
        spec = SessionSpec(session_id=sid, session_dir=project.sessions_dir, cwd=repo,
                           system_prompt=prompting.agent_prompt(project, all_agents(project)["supervisor"]),
                           role="supervisor", tool_groups=GROUPS,
                           env={"ORCHESTRA_ROLE": "supervisor", "ORCHESTRA_REPO": str(repo), "ORCHESTRA_TICKET": "",
                                "ORCHESTRA_SOURCE": "supervisor", access.TOKEN_ENV: access.issue(store, sid, "supervisor")},
                           log_path=project.logs_dir / "agents" / f"{sid}.stderr.log", model="haiku")
        (project.logs_dir / "agents").mkdir(parents=True, exist_ok=True)
        session = get_harness("claude_code").start(spec)
        session.open()
        calls, replies = [], []
        try:
            for n, message in enumerate((ASK, YES), 1):
                store.set_agent_state("supervisor", turn={"n": n, "id": f"sessions/supervisor/{n}", "message": message, "at": 1.0})
                res = session.prompt(message, timeout=900, idle_timeout=600, on_event=lambda ev: calls.append(
                    ev["name"].removeprefix("mcp__orchestra__")) if ev.get("type") == "tool_start" else None)
                replies.append(res.text)
                assert not res.error and not res.timed_out, res
        finally:
            session.close()
        results = [json.loads(ln) for ln in log.read_text().splitlines() if ln.startswith("{") and '"type":"result"' in ln.replace(" ", "")]
        cost = sum(float(r.get("total_cost_usd") or 0) for r in results)
        print("\nREPLY 1:\n" + replies[0] + "\n\nREPLY 2:\n" + replies[1] + f"\n\nTOOLS: {calls}\nCOST: ${cost:.4f}"
              f"\nTRANSCRIPT: {session.session_file}")
        assert "integrate_system" in calls and "approve_integration" in calls, calls
        jira = intended_api.current(project)["machines"].get("jira")
        assert jira and jira["managed"] == "external" and "bot.jira" in jira["aliases"], jira
    d = project.orchestra.state_dir / "machines"
    polls = [p for p in d.iterdir() if (p / "machine.toml").exists()]
    assert polls, "a poller was scaffolded"
    m = tomllib.loads((polls[0] / "machine.toml").read_text())
    assert m["clearance"] == "approved" and m["settings"]["token_env"] == "JIRA_TOKEN" and "OPS" in m["settings"]["jql"]
    project.reload_config()
    assert any(e.get("restart") == "always" for e in project.orchestra.config.raw.get("machines") or [])
    assert "test-token" not in replies[0] + replies[1]
    assert cost < 5.0
