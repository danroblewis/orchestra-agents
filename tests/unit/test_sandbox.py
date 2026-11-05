"""Claude Code's sandbox and the tool command (harness/sandbox.py, local_http.py): the session's
settings allow exactly Orchestra's local ports, the Supervisor is told never to step outside the
sandbox, and a call the sandbox still refuses stops the turn with the setting to ask IT for."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
import pytest
from conftest import agents_project
from orchestra import endpoint, runtime

from orchestra_agents import local_http, prompting, sandbox_writes
from orchestra_agents.harness import claude_code, sandbox
from orchestra_agents.harness.base import SessionSpec
from orchestra_agents.harness.claude_code import ClaudeCodeSession


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(sandbox, "BLOCKED", sandbox.Blocked())
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def spec(project, tmp_path, via: str = "bash", bounded: bool = True, session_id: str = "supervisor",
         escape: bool = False) -> SessionSpec:
    repo = str(project.repo_root)
    return SessionSpec(session_id=session_id, session_dir=tmp_path / "sessions", cwd=project.repo_root,
                       system_prompt="be good", role="supervisor", tool_groups=("design",),
                       env={"ORCHESTRA_ROLE": "supervisor", "ORCHESTRA_REPO": repo}, log_path=tmp_path / "logs" / "x.log",
                       tools_via=via, sandbox_escape=escape, read_only=True, bounds=(repo,) if bounded else ())


def settings_of(args: list[str]) -> dict:
    return json.loads(Path(args[args.index("--settings") + 1]).read_text())


def record_servers(project, events_port: int, web_port: int) -> None:
    since = psutil.Process(os.getpid()).create_time()
    runtime.update(project.orchestra, lambda d: d.update({
        endpoint.RECORD_KEY: {"url": f"http://127.0.0.1:{events_port}", "pid": os.getpid(), "since": since},
        "web": {"port": web_port, "pid": os.getpid(), "since": since}}))


def test_the_settings_allow_exactly_orchestras_ports_and_nothing_broader(project, tmp_path):
    record_servers(project, 41234, 8123)
    s = ClaudeCodeSession(spec(project, tmp_path))
    s.open()
    got = settings_of(s._args())
    state = str(project.orchestra.state_dir.resolve())
    assert got["sandbox"] == {"network": {"allowedDomains": ["127.0.0.1:8123", "127.0.0.1:41234"]},
                              "filesystem": {"allowWrite": [state]},       # the project's own, and no other path
                              "allowUnsandboxedCommands": False}
    assert state.endswith(project.orchestra.key) and state != str(Path(state).parent)
    text = json.dumps(got)
    for broader in ("allowLocalBinding", "excludedCommands", "allowAllUnixSockets", "localhost\"", "\"*\"", "enabled"):
        assert broader not in text, broader
    assert "Bash(curl *)" in got["permissions"]["deny"] and got["hooks"]["PreToolUse"]      # the bounds are still there
    assert s._env()[local_http.ENV] == "1"


def test_an_mcp_session_gets_no_sandbox_allow_and_a_shell_worker_gets_only_that(project, tmp_path):
    record_servers(project, 41234, 8123)
    mcp = ClaudeCodeSession(spec(project, tmp_path, via="mcp"))
    mcp.open()
    assert "sandbox" not in settings_of(mcp._args()) and local_http.ENV not in mcp._env()
    worker = ClaudeCodeSession(spec(project, tmp_path, bounded=False, session_id="w"))
    worker.open()
    assert settings_of(worker._args()) == sandbox.settings([8123, 41234], str(project.orchestra.state_dir.resolve()),
                                                           escape=False)


def test_a_server_on_another_address_or_gone_is_not_allowed(project):
    runtime.update(project.orchestra, lambda d: d.update({
        endpoint.RECORD_KEY: {"url": "http://0.0.0.0:41234", "pid": os.getpid(), "since": psutil.Process().create_time()},
        "web": {"port": 8123, "pid": 999_999_999}}))
    assert sandbox.ports(str(project.repo_root)) == []
    assert sandbox.ports(str(project.repo_root / "nowhere")) == []
    elsewhere = project.repo_root.parent / "elsewhere"           # no orchestra.toml: no project, nothing to allow
    elsewhere.mkdir()
    assert sandbox.state_dir(str(elsewhere)) is None


def test_the_supervisor_is_told_never_to_rerun_a_command_outside_the_sandbox():
    core = " ".join(prompting.core_text(sandbox_escape=False).split())
    assert "You never rerun a command outside the sandbox." in core
    assert "stop, and tell the owner the exact setting their sandbox needs" in core
    assert '"allowedDomains": ["127.0.0.1:<port>"]' in core and local_http.MARK in core


# what `orchestra agents tool` printed in a session at work, the managed settings ignoring the session's allow
RECORDED = json.dumps({"ok": False, "error": "Orchestra's endpoint at http://127.0.0.1:41234 did not answer (<urlopen error "
                       + local_http.blocked(41234) + ">); nothing was applied"})


def _claude_whose_tool_call_the_sandbox_blocks(tmp_path) -> str:
    """A `claude` whose Bash call of the tool command gets the recorded answer, then would retry it
    outside the sandbox (it writes a file if it gets that far)."""
    f = tmp_path / "fake-claude"
    call = {"type": "tool_use", "id": "t1", "name": "Bash",
            "input": {"command": "orchestra agents tool read_events '{}'"}}
    retry = {**call, "id": "t2", "input": {**call["input"], "dangerouslyDisableSandbox": True}}
    f.write_text(f"""#!{sys.executable}
import json, sys, time
sys.stdin.read()
def out(ev): print(json.dumps(ev), flush=True)
out({{"type": "system", "subtype": "init", "mcp_servers": []}})
out({{"type": "assistant", "message": {{"content": [{call!r}]}}}})
out({{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                                              "content": {RECORDED!r}}}]}}}})
time.sleep(5)
out({{"type": "assistant", "message": {{"content": [{retry!r}]}}}})
open({str(tmp_path / "retried")!r}, "w").write("outside")
out({{"type": "result", "subtype": "success", "result": "ran it outside the sandbox"}})
""")
    f.chmod(0o755)
    return str(f)


def test_a_blocked_tool_call_stops_the_turn_and_tells_the_owner_once(project, tmp_path):
    binary = _claude_whose_tool_call_the_sandbox_blocks(tmp_path)
    res = ClaudeCodeSession(spec(project, tmp_path), binary=binary).prompt("hello", timeout=30)
    assert res.error and "turn was stopped instead of the command being run outside the sandbox" in res.error
    assert "allowManagedDomainsOnly" in res.error and "Ask IT" in res.error
    assert '"sandbox": {"network": {"allowedDomains": ["127.0.0.1:41234"]}}' in res.error
    assert not (tmp_path / "retried").exists()
    again = ClaudeCodeSession(spec(project, tmp_path, session_id="other"), binary=binary).prompt("hello", timeout=30)
    assert again.error.startswith("Orchestra's tools are still blocked") and "allowManagedDomainsOnly" not in again.error


def test_a_retry_outside_the_sandbox_is_stopped(project, tmp_path):
    f = tmp_path / "fake-claude"
    use = {"type": "tool_use", "id": "t1", "name": "Bash",
           "input": {"command": "orchestra agents tool read_events '{}'", "dangerouslyDisableSandbox": True}}
    f.write_text(f"""#!{sys.executable}
import json, sys, time
sys.stdin.read()
print(json.dumps({{"type": "assistant", "message": {{"content": [{use!r}]}}}}), flush=True)
time.sleep(5)
open({str(tmp_path / "retried")!r}, "w").write("outside")
""")
    f.chmod(0o755)
    res = ClaudeCodeSession(spec(project, tmp_path), binary=str(f)).prompt("hello", timeout=30)
    assert res.error.startswith(sandbox.ESCAPE) and not (tmp_path / "retried").exists()


class _Proxy(BaseHTTPRequestHandler):
    """The sandbox's proxy, as far as the tool command sees it: it refuses, or answers for the server."""
    refuse = True

    def do_GET(self):
        type(self).seen = self.path
        if type(self).refuse:
            body = b"Connection to 127.0.0.1 blocked by network allowlist"
            self.send_response(403)
        else:
            body = b'{"ok": true}'
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def proxy():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _closed_port() -> int:
    s = ThreadingHTTPServer(("127.0.0.1", 0), _Proxy)
    port = s.server_address[1]
    s.server_close()
    return port


def test_in_the_sandbox_the_call_goes_through_its_proxy(proxy):
    _Proxy.refuse = False
    port = _closed_port()                       # a direct connection fails, as in the sandbox
    env = {local_http.ENV: "1", "HTTP_PROXY": f"http://127.0.0.1:{proxy.server_address[1]}"}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/intended", headers={"Authorization": "Bearer x"})
    with local_http.urlopen(req, 5, env) as r:
        assert json.loads(r.read()) == {"ok": True}
    assert _Proxy.seen == f"http://127.0.0.1:{port}/intended"


def test_a_refusal_by_the_proxy_says_the_sandbox_blocked_it_and_the_setting(proxy):
    _Proxy.refuse = True
    port = _closed_port()
    env = {local_http.ENV: "1", "HTTP_PROXY": f"127.0.0.1:{proxy.server_address[1]}"}
    with pytest.raises(urllib.error.URLError) as e:
        local_http.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/x"), 5, env)
    assert not isinstance(e.value, urllib.error.HTTPError)
    said = str(e.value)
    assert local_http.MARK in said and f'"allowedDomains": ["127.0.0.1:{port}"]' in said
    assert "Never rerun this command outside the sandbox" in said
    assert sandbox.BLOCKED.found(json.dumps({"error": said})) == ("port", str(port))


def test_outside_the_sandbox_a_server_that_is_down_is_said_as_before():
    port = _closed_port()
    with pytest.raises(urllib.error.URLError) as e:
        local_http.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/x"), 5, {})
    assert local_http.MARK not in str(e.value)


def test_claude_codes_harness_imports_the_sandbox_module():
    assert claude_code.sandbox is sandbox


def test_a_write_the_sandbox_refuses_is_said_with_the_path_to_allow(project, monkeypatch, capsys):
    """The tool command in the sandbox, its write to the event log refused as macOS refuses it."""
    import errno

    from orchestra_agents import cli
    from orchestra_agents.harness.caller import Caller
    events_file = project.orchestra.events_file

    def refused(self, name, args):
        self._ctx()                                   # the project is read, then its event log is written
        raise PermissionError(errno.EPERM, "Operation not permitted", str(events_file))
    monkeypatch.setattr(Caller, "answer", refused)
    for k, v in {"ORCHESTRA_REPO": str(project.repo_root), "ORCHESTRA_TOOLS": "events", local_http.ENV: "1"}.items():
        monkeypatch.setenv(k, v)
    capsys.readouterr()
    code = cli.main(["tool", "read_events", "{}"])
    out = capsys.readouterr().out
    state = str(project.orchestra.state_dir.resolve())
    assert code == 1 and sandbox_writes.MARK in out, out
    said = json.loads(out)["error"]
    assert f'"allowWrite": ["{state}"]' in said and "Never rerun this command outside the sandbox" in said
    assert sandbox.BLOCKED.found(out) == ("path", state)
    monkeypatch.delenv(local_http.ENV)
    capsys.readouterr()
    cli.main(["tool", "read_events", "{}"])
    assert sandbox_writes.MARK not in capsys.readouterr().out          # outside the sandbox: said as before


def test_the_words_are_only_for_a_refused_write_in_the_state_dir(tmp_path):
    import errno
    state = tmp_path / "state"
    inside = PermissionError(errno.EPERM, "Operation not permitted", str(state / "events.jsonl"))
    assert sandbox_writes.blocked(RuntimeError("wrapped"), state) is None
    try:
        raise RuntimeError("could not publish") from inside
    except RuntimeError as e:
        assert sandbox_writes.MARK in sandbox_writes.blocked(e, state)
    assert sandbox_writes.blocked(PermissionError(errno.EPERM, "no", str(tmp_path / "other")), state) is None
    assert sandbox_writes.blocked(OSError(errno.ENOSPC, "full", str(state / "x")), state) is None
    assert sandbox_writes.blocked(OSError(errno.EROFS, "Read-only file system", str(state / "x")), state)   # Linux


def test_a_blocked_write_stops_the_turn_and_names_the_path_for_it(project, tmp_path):
    state = str(project.orchestra.state_dir.resolve())
    recorded = json.dumps({"ok": False, "error": sandbox_writes.words(state, state + "/events.jsonl")})
    f = tmp_path / "fake-claude"
    call = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "orchestra agents tool read_events '{}'"}}
    f.write_text(f"""#!{sys.executable}
import json, sys, time
sys.stdin.read()
def out(ev): print(json.dumps(ev), flush=True)
out({{"type": "assistant", "message": {{"content": [{call!r}]}}}})
out({{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                                              "content": [{{"type": "text", "text": {recorded!r}}}]}}]}}}})
time.sleep(5)
open({str(tmp_path / "retried")!r}, "w").write("outside")
""")
    f.chmod(0o755)
    res = ClaudeCodeSession(spec(project, tmp_path), binary=str(f)).prompt("hello", timeout=30)
    assert res.error and f"writing in Orchestra's state directory {state}" in res.error and "Ask IT" in res.error
    assert f'"sandbox": {{"filesystem": {{"allowWrite": ["{state}"]}}}}' in res.error
    assert not (tmp_path / "retried").exists()


# [agents] sandbox_escape (the owner, 2026-11-04: "it's fine to escape the sandbox")

def test_the_setting_defaults_to_escape_and_takes_only_a_boolean():
    from orchestra_agents.config import Config, ConfigError
    assert Config.from_raw({}).sandbox_escape is True
    assert Config.from_raw({"agents": {"sandbox_escape": False}}).sandbox_escape is False
    with pytest.raises(ConfigError, match="sandbox_escape"):
        Config.from_raw({"agents": {"sandbox_escape": "no"}})


@pytest.mark.parametrize("escape", [True, False])
def test_the_session_settings_leave_the_retry_on_only_when_escape_is_allowed(project, tmp_path, escape):
    record_servers(project, 41234, 8123)
    s = ClaudeCodeSession(spec(project, tmp_path, escape=escape))
    s.open()
    got = settings_of(s._args())["sandbox"]
    assert got["network"] == {"allowedDomains": ["127.0.0.1:8123", "127.0.0.1:41234"]}
    if escape:
        assert "allowUnsandboxedCommands" not in got and s._env()[local_http.ESCAPE_ENV] == "1"
    else:
        assert got["allowUnsandboxedCommands"] is False and local_http.ESCAPE_ENV not in s._env()


@pytest.mark.parametrize("escape", [True, False])
def test_the_core_prompt_has_the_rule_the_setting_picks(project, escape):
    from orchestra_agents.config import Config
    project.config = Config.from_raw({"agents": {"sandbox_escape": escape}})
    core = " ".join(prompting._core(project, type("A", (), {"name": "supervisor"})()).split())
    rerun = ("When Claude Code's sandbox blocks a command Orchestra needs** (the tool command, a source's probe reading its "
             "token file), rerun it outside the sandbox (`dangerouslyDisableSandbox: true`) and say so in one line")
    assert (rerun in core) is escape
    assert ("You never rerun a command outside the sandbox." in core) is not escape
    assert "sandbox_escape" not in core and "<!--" not in core
    assert "never list or offer credential files" in core          # that guard stays either way


def test_the_refusal_says_to_rerun_outside_and_still_what_to_ask_it_for():
    said = local_http.blocked(41234, {local_http.ESCAPE_ENV: "1"})
    assert "dangerouslyDisableSandbox: true" in said and "Never rerun" not in said
    assert '"allowedDomains": ["127.0.0.1:41234"]' in said and "ask IT" in said
    assert "Never rerun this command outside the sandbox" in local_http.blocked(41234, {})
    wrote = sandbox_writes.words("/s", "/s/events.jsonl", escape=True)
    assert "dangerouslyDisableSandbox: true" in wrote and '"allowWrite": ["/s"]' in wrote


def test_with_escape_allowed_a_blocked_tool_call_goes_back_to_the_model(project, tmp_path):
    binary = _claude_whose_tool_call_the_sandbox_blocks(tmp_path)
    res = ClaudeCodeSession(spec(project, tmp_path, escape=True), binary=binary).prompt("hello", timeout=30)
    assert not res.error and res.text == "ran it outside the sandbox"
    assert (tmp_path / "retried").exists()                     # its retry with dangerouslyDisableSandbox was not stopped


def test_the_token_files_the_configuration_names_are_readable_and_still_denied_to_read(project, tmp_path):
    token = Path.home() / ".config" / "orchestra" / "store-token"      # named, never opened here
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text() + f"""
[[sources]]
name = "shop"
url = "http://127.0.0.1:9"
match = ['{{job="shop"}}']
auth = {{ bearer_file = "{token}" }}

[scm]
token_file = "~/.config/orchestra/scm-token"
""")
    s = ClaudeCodeSession(spec(project, tmp_path, escape=True))
    s.open()
    got = settings_of(s._args())
    readable = got["sandbox"]["filesystem"]["allowRead"]
    assert str(token) in readable and str(Path.home() / ".config/orchestra/scm-token") in readable
    assert all(not p.endswith("**") and "*" not in p for p in readable)        # exact files, never a directory
    assert "Read(~/.config/**)" in got["permissions"]["deny"]                  # the Read tool and cat are still refused
