"""End-to-end: Orchestra's tools called from the session's shell (``[agents] tools_via``), with the
real task system, the host and the fake Claude Code, which runs each tool as the command the system
prompt gives, through a shell with the session's environment, as Claude Code's Bash tool would.
``bash``: no MCP server at all. ``auto``: the MCP server is blocked (as by an organisation's
policy), so the first turn is stopped at its init and run again from the shell, and the
Supervisor is told once; or it connects and a hook refuses its first call (an organisation's
whitelist of MCP servers), so that turn is stopped there and carried on from the shell."""

import json
from pathlib import Path

import pytest
from test_agents_e2e import APPROVED, WORK_READY, agent_store, check_protocol, make_env

from orchestra_agents.harness import claude_code, mcp_status, read_transcript


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(claude_code, "BACKOFF", mcp_status.Backoff())
    monkeypatch.setattr(claude_code, "FALLBACK", mcp_status.Fallback())


def _worked_from_the_shell(env, t):
    archived = agent_store(env).runtime()["archived_sessions"]
    for kind, tool in (("worker", "report_ticket"), ("reviewer", "report_review")):
        msgs = read_transcript(archived[f"{kind}-{t}"])
        calls = [c for m in msgs for c in m.get("tool_calls") or []]
        assert [c["name"] for c in calls] == [tool]                   # a Bash call, shown as the tool it ran
        raw = Path(archived[f"{kind}-{t}"]["session_file"]).read_text()
        assert '"name": "Bash"' in raw and "agents tool " + tool in raw and "mcp__orchestra__" not in raw
        prompt = (env.project.logs_dir / "agents").glob(f"{kind}-{t}*")
        assert all("ORCHESTRA_SESSION_TOKEN=" not in p.read_text(errors="replace") for p in prompt)
    cfgs = list((env.project.sessions_dir / "claude-code").glob(f"worker-{t}.none.json"))
    assert cfgs and json.loads(cfgs[0].read_text()) == {"mcpServers": {}}


def test_bash_tools_ticket_goes_open_to_done(tmp_path, monkeypatch):
    with make_env(tmp_path, monkeypatch, "claude_code", tools_via="bash") as env:
        t = env.create(title="Do the thing", description="x", acceptance="y")
        env.settle(t)
        assert env.state(t) == "DONE"
        check_protocol(env.events, t, WORK_READY, APPROVED)
        _worked_from_the_shell(env, t)
        assert not claude_code.FALLBACK.on


def test_auto_switches_to_the_shell_when_the_server_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MCP_BLOCKED", "1")
    with make_env(tmp_path, monkeypatch, "claude_code", tools_via="auto") as env:
        t = env.create(title="Do the thing", description="x", acceptance="y")
        env.settle(t)
        assert env.state(t) == "DONE"                                  # nothing lost: the turn ran again from the shell
        check_protocol(env.events, t, WORK_READY, APPROVED)
        _worked_from_the_shell(env, t)
        assert claude_code.FALLBACK.on
        env.settle(t)
        sup = agent_store(env).find_session("supervisor") or {}
        told = [m["text"] for m in read_transcript(sup) if m["role"] == "user" and "Orchestra notice" in m["text"]]
        assert len(told) == 1 and "call Orchestra's tools from their shell" in told[0]      # said once, for both turns


def test_auto_switches_to_the_shell_when_a_hook_refuses_the_servers_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MCP_HOOK", "1")
    with make_env(tmp_path, monkeypatch, "claude_code", tools_via="auto") as env:
        t = env.create(title="Do the thing", description="x", acceptance="y")
        env.settle(t)
        assert env.state(t) == "DONE"                                  # the refused report went through from the shell
        check_protocol(env.events, t, WORK_READY, APPROVED)
        assert claude_code.FALLBACK.on and "MCP server is not in the whitelist" in claude_code.FALLBACK.why
        archived = agent_store(env).runtime()["archived_sessions"]
        worker = Path(archived[f"worker-{t}"]["session_file"]).read_text()
        assert "mcp__orchestra__report_ticket" in worker and "agents tool report_ticket" in worker   # refused, then from the shell
        assert "[Orchestra] Your call to Orchestra's `report_ticket`" in worker                     # carried on, not started over
        assert "From now on Orchestra's tools are called from your shell" in worker   # its system prompt is kept on resume: told
        reviewer = Path(archived[f"reviewer-{t}"]["session_file"]).read_text()
        assert "mcp__orchestra__" not in reviewer and "agents tool report_review" in reviewer       # every session from the shell since
        env.settle(t)
        sup = agent_store(env).find_session("supervisor") or {}
        told = [m["text"] for m in read_transcript(sup) if m["role"] == "user" and "Orchestra notice" in m["text"]]
        assert len(told) == 1 and "MCP server is not in the whitelist" in told[0]
