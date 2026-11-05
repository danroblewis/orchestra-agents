"""`delegate` runs in the agents host (delegate_host.py; the owner, 2026-11-04: a delegated job "got a
401 error from my LLM service" while the Supervisor itself was signed in). The tool command asks the
host's control endpoint and waits for the report; the host starts the sub-session from its own
environment; a host that is down is a refusal that says so; a wait past the bound returns what is
known; a sub-session the provider refused to sign in is reported by variable names, never values."""

import argparse
import json
import os
import subprocess
import threading

import pytest
from conftest import FakeHarness, FakeSession, agents_project

import orchestra_agents.harness as harness_pkg
from orchestra_agents import access, cli, delegate, delegate_host
from orchestra_agents.control import ControlServer
from orchestra_agents.harness import claude_code
from orchestra_agents.harness.base import TurnResult
from orchestra_agents.harness.caller import Caller
from orchestra_agents.store import AgentStore, StoreError

BRIEF = "Survey the store prod (declared, match job=shop): its families, the levels and their flows."
SECRET = "sk-gateway-value-not-to-be-seen"


class Scripted(FakeHarness):
    """A harness whose one-shot turn returns ``result`` and calls one tool on the way."""

    name = "claude_code"

    def __init__(self, result: TurnResult):
        super().__init__(reply=None)
        self.result = result

    def start(self, spec):
        result = self.result

        class S(FakeSession):
            def prompt(self, text, timeout, on_event=None, idle_timeout=None):
                self.prompts.append(text)
                if on_event:
                    on_event({"type": "tool_start", "name": "list_metrics", "args": {}})
                return result
        s = S(spec, None)
        self.sessions.append(s)
        return s

    def priced(self, model):
        return True

    def sign_in(self, environ=None):
        return claude_code.sign_in(environ)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(delegate.DELEGATED_ENV, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


@pytest.fixture
def host(project, monkeypatch):
    """The host's control endpoint, serving ``POST /delegate`` with ``delegate_host.serve``, recorded
    in the runtime record as a live host's; ``seen`` holds the tokens it was asked with."""
    store = AgentStore(project)
    seen: list[str] = []

    def run(token, body, emit):
        seen.append(token)
        return delegate_host.serve(project, store, token, body, emit)
    server = ControlServer(0, lambda m, e: {}, "control-token", delegate=run)
    server.start()
    store.set_control(server.port, os.getpid())
    yield store, seen, server
    server.stop()


def session_env(project, store, monkeypatch, sid="supervisor-main", **grant) -> dict:
    token = access.issue(store, sid, "supervisor", **grant)
    env = {"ORCHESTRA_REPO": str(project.repo_root), "ORCHESTRA_ROLE": "supervisor", access.TOKEN_ENV: token}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


def tool_command(name: str, args: dict, capsys) -> tuple[int, dict]:
    rc = cli.cmd_tool(argparse.Namespace(repo=None, name=name, json=json.dumps(args), list=False, help=False))
    return rc, json.loads(capsys.readouterr().out)


def test_the_tool_command_asks_the_host_and_gets_the_report(project, host, monkeypatch, capsys):
    store, seen, _ = host
    h = Scripted(TurnResult(text="Families: shop_orders_placed_total (counter).", cost_usd=0.02))
    monkeypatch.setattr(harness_pkg, "get_harness", lambda name: h)
    project.config.pass_env = ("MY_GATEWAY_KEY",)
    env = session_env(project, store, monkeypatch)
    real, ran_here = delegate.delegate, []

    def spy(*a, **kw):
        ran_here.append(kw)
        return real(*a, **kw)
    monkeypatch.setattr(delegate, "delegate", spy)
    rc, out = tool_command("delegate", {"job": "survey", "brief": BRIEF}, capsys)
    assert rc == 0 and out["ok"] and out["report"].startswith("Families:") and out["started_by"] == "host"
    assert seen == [env[access.TOKEN_ENV]]                       # asked of the host, with the session's own token
    assert ran_here == [{"env": {}, "by_host": True, "on_event": ran_here[0]["on_event"]}]   # run in the host only
    (s,) = h.sessions
    assert s.spec.env[delegate.DELEGATED_ENV] == "survey" and s.spec.session_id not in store.grants()
    assert s.spec.pass_env == ("MY_GATEWAY_KEY",) and s.prompts == [f"[Delegated job: survey]\n\n{BRIEF}"]


def test_pass_env_is_the_owners_alone(project):
    from orchestra_agents import setup_tools
    for key, value in (("agents.pass_env", ["MY_GATEWAY_KEY"]), ("agents", {"pass_env": ["X"]})):
        with pytest.raises(StoreError, match="pass_env is the owner's alone"):
            setup_tools._set_setting(project, AgentStore(project), {"key": key, "value": value})


def test_a_delegated_sub_session_never_delegates_through_the_host(project, host, monkeypatch):
    store, seen, _ = host
    monkeypatch.setattr(harness_pkg, "get_harness", lambda name: Scripted(TurnResult(text="x")))
    env = session_env(project, store, monkeypatch, sid="supervisor-survey-1", delegated="survey")
    with pytest.raises(StoreError, match="never delegates again"):    # its grant says so, whatever its environment
        Caller(env).call("delegate", {"job": "survey", "brief": BRIEF})
    assert len(seen) == 1


def test_the_host_down_is_a_refusal_that_says_so(project, monkeypatch, capsys):
    store = AgentStore(project)
    env = session_env(project, store, monkeypatch)
    with pytest.raises(StoreError, match="agents host is not running .no control endpoint is recorded"):
        delegate_host.ask(project, store, {"job": "survey", "brief": BRIEF}, "supervisor", env)
    dead = subprocess.Popen(["true"])
    dead.wait()
    store.set_control(1, dead.pid)
    with pytest.raises(StoreError, match=f"pid {dead.pid}, has ended"):
        delegate_host.ask(project, store, {"job": "survey", "brief": BRIEF}, "supervisor", env)
    server = ControlServer(0, lambda m, e: {}, "t")              # bound, then closed: nothing listens there
    port = server.port
    server._server.server_close()
    store.set_control(port, os.getpid())
    rc, out = tool_command("delegate", {"job": "survey", "brief": BRIEF}, capsys)
    assert rc == 1 and not out["ok"] and "agents host is not running" in out["error"] and "did not answer" in out["error"]


def test_a_wait_past_the_bound_returns_what_is_known(project, monkeypatch):
    store = AgentStore(project)
    release = threading.Event()

    def slow(token, body, emit):
        emit({"type": "started", "job": "survey", "model": "haiku", "session": "supervisor-survey-x", "transcript": "/t.jsonl"})
        emit({"type": "tool", "name": "list_metrics"})
        release.wait(10)
        return {"ok": True}
    server = ControlServer(0, lambda m, e: {}, "t", delegate=slow)
    server.start()
    try:
        store.set_control(server.port, os.getpid())
        env = session_env(project, store, monkeypatch)
        out = delegate_host.ask(project, store, {"job": "survey", "brief": BRIEF}, "supervisor", env, wait=1.0)
    finally:
        release.set()
        server.stop()
    assert not out["ok"] and out["session"] == "supervisor-survey-x" and out["transcript"] == "/t.jsonl"
    assert out["tools_called"] == ["list_metrics"] and "no report from the agents host within 1s" in out["error"]
    assert delegate_host.wait_s(project) == delegate.turn_s(project) + delegate_host.MARGIN_S < delegate.TOOL_CALL_S


def test_a_refused_sign_in_names_the_variables_never_their_values(project, host, monkeypatch, capsys):
    store, _, _ = host
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://llm.example.internal")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", SECRET)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    said = f'API Error: 401 {{"type":"error","error":{{"type":"authentication_error","message":"bad token {SECRET}"}}}}'
    monkeypatch.setattr(harness_pkg, "get_harness", lambda name: Scripted(TurnResult(error=said)))
    session_env(project, store, monkeypatch)
    rc, out = tool_command("delegate", {"job": "survey", "brief": BRIEF}, capsys)
    text = out["error"]
    assert rc == 0 and not out["ok"] and out["sign_in"] == text and out["started_by"] == "host"
    assert "could not sign in" in text and "started by the agents host" in text
    assert "ANTHROPIC_AUTH_TOKEN" in text and "ANTHROPIC_BASE_URL" in text and "pass_env" in text
    assert "ANTHROPIC_API_KEY," not in text                       # one it does not have is not listed as had
    assert SECRET not in json.dumps(out) and "https://llm.example.internal" not in text


def test_a_sign_in_refused_only_in_the_stderr_log_is_found_there(project, monkeypatch):
    store = AgentStore(project)
    h = Scripted(TurnResult(error="claude exited with status 1"))
    monkeypatch.setattr(harness_pkg, "get_harness", lambda name: h)
    real_start = h.start

    def start(spec):
        spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        spec.log_path.write_text("Invalid API key · Please run /login\n")
        return real_start(spec)
    h.start = start
    out = delegate.delegate(project, store, {"job": "survey", "brief": BRIEF}, "supervisor", env={})
    assert "could not sign in" in out["error"] and "not by the agents host" in out["error"]
