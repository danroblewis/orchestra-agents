"""Every harness's session process starts without the host's credentials (``base.session_env``),
keeping only that harness's own sign-in: sessions run without permission prompts, so anything in
their environment is readable by every command they run."""

import subprocess

import pytest

from orchestra_agents.harness import base, codex, dummy, pi
from orchestra_agents.harness.base import SessionSpec
from orchestra_agents.harness.claude_code import ClaudeCodeSession

HOST = {"JIRA_TOKEN": "jira-secret", "ORCHESTRA_EVENTS_TOKEN": "bus-secret", "DB_PASSWORD": "db-secret",
        "SLACK_BOT_SECRET": "slack-secret", "LANG": "en_GB.UTF-8", "HTTPS_PROXY": "http://proxy:3128"}


def spec(tmp_path) -> SessionSpec:
    (tmp_path / "wt").mkdir(exist_ok=True)
    return SessionSpec(session_id="worker-T-1", session_dir=tmp_path / "sessions", cwd=tmp_path / "wt",
                       system_prompt="be good", role="worker", tool_groups=("report_ticket",),
                       env={"ORCHESTRA_TICKET": "T-1", "ORCHESTRA_TOKEN": "the-sessions-own"},
                       log_path=tmp_path / "logs" / "x.log", model="haiku")


@pytest.fixture
def host(monkeypatch):
    for k, v in HOST.items():
        monkeypatch.setenv(k, v)


class Started(Exception):
    pass


@pytest.fixture
def popen(monkeypatch):
    """subprocess.Popen replaced: the environment a harness would start its process with."""
    seen: dict = {}

    def fake(args, **kw):
        seen.update(kw["env"])
        raise Started
    monkeypatch.setattr(subprocess, "Popen", fake)
    return seen


def kept_out(env: dict) -> None:
    assert not {"JIRA_TOKEN", "ORCHESTRA_EVENTS_TOKEN", "DB_PASSWORD", "SLACK_BOT_SECRET"} & set(env)
    assert env["LANG"] == "en_GB.UTF-8" and env["HTTPS_PROXY"] == "http://proxy:3128" and env["PATH"]
    assert env["ORCHESTRA_TICKET"] == "T-1" and env["ORCHESTRA_TOKEN"] == "the-sessions-own"   # the spec's own


def test_session_env_keeps_only_the_named_sign_in():
    env = base.session_env(("OPENAI_API_KEY",), {"OPENAI_API_KEY": "k", "GEMINI_API_KEY": "g", "PATH": "/bin"})
    assert env == {"OPENAI_API_KEY": "k", "PATH": "/bin"}


def test_claude_code_keeps_its_sign_in(tmp_path, host, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "claude-login")
    env = ClaudeCodeSession(spec(tmp_path))._env()
    kept_out(env)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "claude-login"


def test_pi_keeps_its_provider_key(tmp_path, host, popen, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "provider-key")
    with pytest.raises(Started):
        pi.PiSession(spec(tmp_path), pi_binary="pi")
    kept_out(popen)
    assert popen["ANTHROPIC_API_KEY"] == "provider-key"


def test_codex_keeps_its_sign_in(tmp_path, host, popen, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "codex-key")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    with pytest.raises(Started):
        codex.CodexSession(spec(tmp_path), binary="codex").prompt("hi", timeout=5)
    kept_out(popen)
    assert popen["OPENAI_API_KEY"] == "codex-key" and popen["CODEX_HOME"] == str(tmp_path / "codex")


def test_dummy_gets_no_credentials(tmp_path, host, popen):
    with pytest.raises(Started):
        dummy.DummySession(spec(tmp_path)).prompt("hi", timeout=5)
    kept_out(popen)


def test_the_bash_route_lets_a_tool_call_run_as_long_as_any_tool_call(tmp_path):
    from dataclasses import replace

    from orchestra_agents.harness.base import TOOL_CALL_S
    from orchestra_agents.integrate_kits import TEST_TIMEOUT_S
    assert TOOL_CALL_S > TEST_TIMEOUT_S and codex.TOOL_TIMEOUT_S == TOOL_CALL_S
    env = ClaudeCodeSession(replace(spec(tmp_path), tools_via="bash"))._env()
    assert env["BASH_DEFAULT_TIMEOUT_MS"] == env["BASH_MAX_TIMEOUT_MS"] == str(int(TOOL_CALL_S * 1000))
    assert "BASH_DEFAULT_TIMEOUT_MS" not in ClaudeCodeSession(replace(spec(tmp_path), tools_via="mcp"))._env()


def test_pi_s_extension_is_given_the_limit(tmp_path, popen):
    from orchestra_agents.harness.base import TOOL_CALL_S
    with pytest.raises(Started):
        pi.PiSession(spec(tmp_path), pi_binary="pi")
    assert popen["ORCHESTRA_TOOL_CALL_MS"] == str(int(TOOL_CALL_S * 1000))
    assert "process.env.ORCHESTRA_TOOL_CALL_MS" in pi.EXTENSION_PATH.read_text()


GATEWAY = {"ANTHROPIC_BASE_URL": "https://llm.example.internal", "ANTHROPIC_CUSTOM_HEADERS": "X-Team: data",
           "ANTHROPIC_MODEL": "team-sonnet", "ANTHROPIC_SMALL_FAST_MODEL": "team-haiku", "CLAUDE_CODE_USE_BEDROCK": "1",
           "AWS_ACCESS_KEY_ID": "aws-id-value", "AWS_SECRET_ACCESS_KEY": "aws-secret-value",
           "AWS_SESSION_TOKEN": "aws-session-value", "AWS_PROFILE": "team", "MY_GATEWAY_KEY": "named-by-the-owner",
           "GOOGLE_APPLICATION_CREDENTIALS": "/keys/gcp.json", "ANTHROPIC_FOUNDRY_API_KEY": "foundry-value"}


def test_claude_code_keeps_a_company_gateways_sign_in_and_pass_env(tmp_path, host, monkeypatch):
    """Built from the host's environment: the gateway's route, Bedrock's credentials while Bedrock is on,
    and what [agents] pass_env names; every other credential stripped, and no stripped value anywhere."""
    for k, v in GATEWAY.items():
        monkeypatch.setenv(k, v)
    s = spec(tmp_path)
    s.pass_env = ("MY_GATEWAY_KEY",)
    env = ClaudeCodeSession(s)._env()
    kept_out(env)
    for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL",
              "CLAUDE_CODE_USE_BEDROCK", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
              "MY_GATEWAY_KEY"):
        assert env[k] == GATEWAY[k], k
    stripped = {"GOOGLE_APPLICATION_CREDENTIALS", "ANTHROPIC_FOUNDRY_API_KEY"}     # Vertex and Foundry are off
    assert not stripped & set(env)
    values = "\n".join(env.values())
    for k in stripped | {"JIRA_TOKEN", "ORCHESTRA_EVENTS_TOKEN", "DB_PASSWORD", "SLACK_BOT_SECRET"}:
        assert (GATEWAY.get(k) or HOST[k]) not in values, k


def test_cloud_credentials_follow_their_providers_switch(tmp_path, host, monkeypatch):
    for k, v in GATEWAY.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    env = ClaudeCodeSession(spec(tmp_path))._env()
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == "/keys/gcp.json" and env["CLAUDE_CODE_USE_VERTEX"] == "1"
    assert not {"AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_ACCESS_KEY_ID", "MY_GATEWAY_KEY"} & set(env)
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "0")                 # off is off
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in ClaudeCodeSession(spec(tmp_path))._env()


def test_every_harness_keeps_pass_env(tmp_path, host, popen, monkeypatch):
    monkeypatch.setenv("MY_GATEWAY_KEY", "named-by-the-owner")
    s = spec(tmp_path)
    s.pass_env = ("MY_GATEWAY_KEY",)
    with pytest.raises(Started):
        pi.PiSession(s, pi_binary="pi")
    kept_out(popen)
    assert popen["MY_GATEWAY_KEY"] == "named-by-the-owner"


def test_pass_env_is_names_and_never_an_orchestra_token(tmp_path):
    from orchestra.config import ConfigError

    from orchestra_agents.config import Config
    assert Config.from_raw({"agents": {"harness": "dummy", "pass_env": ["MY_GATEWAY_KEY"]}}).pass_env == ("MY_GATEWAY_KEY",)
    for bad in ("MY_GATEWAY_KEY", ["MY KEY"], ["ORCHESTRA_EVENTS_TOKEN"], [1]):
        with pytest.raises(ConfigError, match="pass_env"):
            Config.from_raw({"agents": {"harness": "dummy", "pass_env": bad}})
