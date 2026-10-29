"""Orchestra's tools from the session's shell (`orchestra agents tool`, harness/bash_tools.py): the
same call as the MCP server's, the session known by its token, whatever the working directory; and
how each harness offers it ([agents] tools_via: mcp, bash, auto)."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import agents_project

from orchestra_agents import access
from orchestra_agents.cli import main
from orchestra_agents.config import Config, ConfigError
from orchestra_agents.harness import bash_tools, claude_code, mcp_status
from orchestra_agents.harness.base import SessionSpec
from orchestra_agents.harness.claude_code import ClaudeCodeHarness, ClaudeCodeSession
from orchestra_agents.harness.codex import CodexSession
from orchestra_agents.store import AgentStore

READY = {"state": "READY_FOR_REVIEW", "summary": "done"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    for k in ("ORCHESTRA_ROLE", access.TOKEN_ENV, "ORCHESTRA_TICKET", "ORCHESTRA_TOOLS", "ORCHESTRA_REPO"):
        monkeypatch.delenv(k, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    return agents_project(repo)


def session_env(project, token: str, groups: str = "report_ticket") -> dict[str, str]:
    """What the host gives a worker's session, and its shell inherits."""
    return {"ORCHESTRA_REPO": str(project.repo_root), "ORCHESTRA_ROLE": "worker", "ORCHESTRA_TICKET": "T-1",
            access.TOKEN_ENV: token, "ORCHESTRA_TOOLS": groups}


def run_tool(monkeypatch, capsys, env: dict, *argv: str) -> tuple[int, str]:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    capsys.readouterr()
    code = main(["tool", *argv])
    return code, capsys.readouterr().out


def test_a_session_calls_its_tool_from_its_shell_in_any_directory(project, tmp_path):
    """Run as the prompt gives it when `orchestra` is not on the PATH, from another repository whose
    own `orchestra_agents` would break if it were imported: identity and project come from the env."""
    store = AgentStore(project)
    token = access.issue(store, "worker-T-1", "worker", "T-1")
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "orchestra_agents").mkdir(parents=True)
    (elsewhere / "orchestra_agents" / "__init__.py").write_text("raise ImportError('the checkout in the cwd was imported')\n")
    env = {**os.environ, **session_env(project, token)}
    cmd = bash_tools.command(path="")
    assert cmd == f"{shlex.quote(sys.executable)} -P -m orchestra agents tool"
    p = subprocess.run(["/bin/sh", "-c", f"{cmd} report_ticket {shlex.quote(json.dumps(READY))}"], cwd=elsewhere, env=env,
                       capture_output=True, text=True, timeout=60, check=False)
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(p.stdout)["ok"] and store.pop_report("T-1")["state"] == "READY_FOR_REVIEW"
    # long arguments on stdin
    p = subprocess.run(["/bin/sh", "-c", f"{cmd} report_ticket - <<'JSON'\n{json.dumps(READY, indent=2)}\nJSON\n"], cwd=elsewhere,
                       env=env, capture_output=True, text=True, timeout=60, check=False)
    assert p.returncode == 0 and json.loads(p.stdout)["ok"] and store.pop_report("T-1")["summary"] == "done"
    assert token not in p.stdout + p.stderr


def test_an_ended_session_is_refused(project, monkeypatch, capsys):
    store = AgentStore(project)
    token = access.issue(store, "worker-T-1", "worker", "T-1")
    access.revoke(store, "worker-T-1")
    code, out = run_tool(monkeypatch, capsys, session_env(project, token), "report_ticket", json.dumps(READY))
    assert code == 1 and json.loads(out) == {"ok": False, "error": f"refused: {access.TOKEN_ENV} was not issued by the host, "
                                                                   "or its session has ended"}
    assert store.pop_report("T-1") is None and token not in out


def test_without_a_token_nothing_runs(project, monkeypatch, capsys):
    env = session_env(project, "")
    code, out = run_tool(monkeypatch, capsys, env, "report_ticket", json.dumps(READY))
    assert code == 1 and access.TOKEN_ENV in json.loads(out)["error"]


def test_only_the_sessions_tools_are_offered_and_the_role_is_the_tokens(project, monkeypatch, capsys):
    store = AgentStore(project)
    token = access.issue(store, "worker-T-1", "worker", "T-1")
    env = session_env(project, token)
    code, out = run_tool(monkeypatch, capsys, env, "pause_agent", '{"name": "supervisor"}')
    assert code == 1 and json.loads(out)["error"] == "unknown tool pause_agent"           # not among ORCHESTRA_TOOLS
    code, out = run_tool(monkeypatch, capsys, {**env, "ORCHESTRA_TOOLS": "report_ticket,roles"}, "pause_agent", '{"name": "supervisor"}')
    assert code == 1 and "needs the 'roles' tool group, and role 'worker'" in json.loads(out)["error"]   # listed, still refused
    assert not store.agent_state("supervisor").get("paused")
    code, out = run_tool(monkeypatch, capsys, env, "--list")
    assert code == 0 and [ln.split()[0] for ln in out.splitlines()] == ["report_ticket"]


def test_bad_arguments_are_answered_with_what_the_tool_takes(project, monkeypatch, capsys):
    store = AgentStore(project)
    env = session_env(project, access.issue(store, "worker-T-1", "worker", "T-1"))
    code, out = run_tool(monkeypatch, capsys, env, "report_ticket", '{"state": "READY_FOR_REVIEW", "colour": "red"}')
    assert code == 1 and "no argument colour" in json.loads(out)["error"] and "It takes: state (required)" in json.loads(out)["error"]
    for bad in ("{not json", "[1, 2]"):
        code, out = run_tool(monkeypatch, capsys, env, "report_ticket", bad)
        assert code == 1 and not json.loads(out)["ok"]
    assert store.pop_report("T-1") is None
    code, out = run_tool(monkeypatch, capsys, env, "report_ticket", "--help")
    schema = json.loads(out)
    assert code == 0 and schema["name"] == "report_ticket" and "state" in schema["arguments"]["properties"]
    code, out = run_tool(monkeypatch, capsys, env)
    assert code == 2 and "usage" in json.loads(out)["error"]


def test_the_prompt_names_the_command_and_each_tool_in_a_line():
    text = bash_tools.prompt(("report_ticket", "observe"), path="")
    assert f"    {shlex.quote(sys.executable)} -P -m orchestra agents tool <tool> '<arguments as one JSON object>'" in text
    assert "- `report_ticket`: " in text and "--help" in text and "--list" in text
    assert all(len(ln) < 200 for ln in text.splitlines() if ln.startswith("- `"))   # a name and one line each, no schemas
    assert "TOKEN=" not in text


def test_a_call_from_the_shell_is_read_as_the_tool_it_called():
    py = shlex.quote(sys.executable)
    assert bash_tools.parse(f"orchestra agents tool report_ticket {shlex.quote(json.dumps(READY))}") == ("report_ticket", READY)
    assert bash_tools.parse(shlex.join(["/bin/zsh", "-lc", f"{py} -P -m orchestra agents tool report_ticket '{{\"state\": \"BLOCKED\"}}'"])) \
        == ("report_ticket", {"state": "BLOCKED"})
    assert bash_tools.parse("orchestra agents tool report_ticket - <<'JSON'\n{\"it's\": 1}\nJSON") == ("report_ticket", {})
    assert bash_tools.parse("orchestra agents tool report_ticket --help") is None
    assert bash_tools.parse("ls -la") is None and bash_tools.parse("orchestra agents tool no_such_tool '{}'") is None


def test_tools_via_is_read_and_checked():
    assert Config.from_toml('[agents]\nharness = "claude_code"\n').tools_via == "auto"
    assert Config.from_toml('[agents]\nharness = "claude_code"\ntools_via = "bash"\n').tools_via == "bash"
    with pytest.raises(ConfigError, match="tools_via is 'shell'"):
        Config.from_toml('[agents]\ntools_via = "shell"\n')


TOKEN = "tok-not-a-real-secret"


def spec(tmp_path, tools_via: str, **kw) -> SessionSpec:
    (tmp_path / "wt").mkdir(exist_ok=True)
    return SessionSpec(session_id=kw.get("session_id", "worker-T-1"), session_dir=tmp_path / "sessions", cwd=tmp_path / "wt",
                       system_prompt="be good", role="worker", tool_groups=("report_ticket",),
                       env={"ORCHESTRA_ROLE": "worker", "ORCHESTRA_REPO": str(tmp_path), "ORCHESTRA_TICKET": "T-1",
                            access.TOKEN_ENV: TOKEN},
                       log_path=tmp_path / "logs" / "x.log", tools_via=tools_via)


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_code, "BACKOFF", mcp_status.Backoff())
    monkeypatch.setattr(claude_code, "FALLBACK", mcp_status.Fallback())
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))


def _servers(args: list[str]) -> dict:
    return json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())["mcpServers"]


def _system(args: list[str]) -> str:
    return Path(args[args.index("--append-system-prompt-file") + 1]).read_text()


def test_claude_bash_has_no_mcp_server_and_says_how_to_call_the_tools(tmp_path, fresh):
    s = ClaudeCodeSession(spec(tmp_path, "bash"))
    args = s._args()
    assert "--strict-mcp-config" in args and _servers(args) == {}            # no server of Orchestra's, nor any other
    assert "be good" in _system(args) and "agents tool <tool>" in _system(args) and "`report_ticket`" in _system(args)
    assert TOKEN not in _system(args) and TOKEN not in " ".join(args)
    env = s._env()
    assert env[access.TOKEN_ENV] == TOKEN and env["ORCHESTRA_TOOLS"] == "report_ticket"   # what its Bash tool passes on


@pytest.mark.parametrize("via", ["mcp", "auto"])
def test_claude_mcp_and_auto_start_with_the_server(tmp_path, fresh, via):
    args = ClaudeCodeSession(spec(tmp_path, via))._args()
    assert _servers(args)["orchestra"]["args"] == ["-P", "-m", "orchestra_agents.harness.mcp"]
    assert "agents tool" not in _system(args)


def test_codex_bash_has_no_mcp_server_and_sets_the_shells_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    args = CodexSession(spec(tmp_path, "bash"))._args()
    overrides = [args[i + 1] for i, a in enumerate(args) if a == "-c"]
    assert not any(o.startswith("mcp_servers.") for o in overrides)
    shell = next(o for o in overrides if o.startswith("shell_environment_policy.set="))
    assert f'{access.TOKEN_ENV}="{TOKEN}"' in shell and 'ORCHESTRA_TOOLS="report_ticket"' in shell   # Codex drops *TOKEN* by default
    instructions = next(o for o in overrides if o.startswith("developer_instructions="))
    assert "agents tool <tool>" in instructions and TOKEN not in instructions
    auto = CodexSession(spec(tmp_path, "auto", session_id="other"))._args()
    assert any(a.startswith("mcp_servers.orchestra=") for a in auto)            # auto is the MCP server on Codex


def _claude_blocked_unless_bash(tmp_path) -> str:
    """A `claude` whose orchestra MCP server never connects (a policy blocks it), and which answers
    when started with no server at all; each start is counted."""
    f = tmp_path / "fake-claude"
    f.write_text(f"""#!{sys.executable}
import json, sys
sys.stdin.read()
a = sys.argv
servers = json.load(open(a[a.index("--mcp-config") + 1]))["mcpServers"]
open({str(tmp_path / "starts")!r}, "a").write("mcp\\n" if servers else "bash\\n")
if servers:
    print(json.dumps({{"type": "system", "subtype": "init", "mcp_servers": [{{"name": "orchestra", "status": "failed"}}]}}), flush=True)
    import time; time.sleep(60)
print(json.dumps({{"type": "system", "subtype": "init", "mcp_servers": []}}))
print(json.dumps({{"type": "result", "subtype": "success", "result": "done from the shell"}}))
""")
    f.chmod(0o755)
    return str(f)


def test_auto_runs_the_turn_again_from_the_shell_and_says_so_once(tmp_path, fresh):
    import time
    binary = _claude_blocked_unless_bash(tmp_path)
    seen: list[dict] = []
    began = time.time()
    res = ClaudeCodeSession(spec(tmp_path, "auto"), binary=binary).prompt("work", timeout=30, on_event=seen.append)
    assert time.time() - began < 15 and res.error is None and res.text == "done from the shell"
    notices = [e for e in seen if e["type"] == "notice"]
    assert len(notices) == 1 and "call Orchestra's tools from their shell" in notices[0]["text"] and "managed-mcp.json" in notices[0]["text"]
    assert claude_code.FALLBACK.on and claude_code.BACKOFF.refusal([]) is None    # no backing off
    seen.clear()
    again = ClaudeCodeSession(spec(tmp_path, "auto", session_id="other"), binary=binary).prompt("more", timeout=30, on_event=seen.append)
    assert again.text == "done from the shell" and not [e for e in seen if e["type"] == "notice"]   # said once
    assert (tmp_path / "starts").read_text().split() == ["mcp", "bash", "bash"]   # the second session went straight to the shell


def test_mcp_mode_still_fails_loudly_and_names_the_bash_setting(tmp_path, fresh):
    res = ClaudeCodeSession(spec(tmp_path, "mcp"), binary=_claude_blocked_unless_bash(tmp_path)).prompt("work", timeout=30)
    assert res.error and "did not connect" in res.error
    assert 'tools_via = "bash"' in res.error and not claude_code.FALLBACK.on


def test_the_transcript_shows_a_bash_call_as_the_tool(tmp_path):
    f = tmp_path / "t.jsonl"
    use = {"type": "tool_use", "id": "x", "name": "Bash",
           "input": {"command": f"orchestra agents tool report_ticket {shlex.quote(json.dumps(READY))}"}}
    f.write_text(json.dumps({"type": "assistant", "message": {"content": [use]}}) + "\n")
    msgs = ClaudeCodeHarness.read_transcript(f)
    assert msgs[0]["tool_calls"] == [{"name": "report_ticket", "args": READY}]
