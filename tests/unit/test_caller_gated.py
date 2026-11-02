"""The caller offers the tools ``tools_for`` returns, whose text is gated for whether the task system
is installed; a call matches them by name, so a gated tool is callable over the shell (bash) path as
over MCP (T9: the T8 gating made every gated tool answer "unknown tool" through ``orchestra agents tool``)."""

import json

import pytest

from orchestra_agents import access, installed, tooling
from orchestra_agents.builtins import ROLES
from orchestra_agents.cli import main
from orchestra_agents.harness import caller as caller_mod
from orchestra_agents.harness import mcp
from orchestra_agents.harness.tools import BY_NAME, tools_for

SIX = ("declare_workflow", "request_work", "integrate_system", "add_instrument", "switch_kind", "read_transcript")
GROUPS = ",".join(ROLES["supervisor"].tools)


@pytest.fixture
def supervisor_env(monkeypatch, tmp_path):
    monkeypatch.setattr(installed, "tasking", lambda: False)
    calls: list[str] = []
    monkeypatch.setattr(caller_mod.Caller, "_ctx", lambda self: (None, None))
    monkeypatch.setattr(access, "identify", lambda store, env: ("supervisor", None))
    monkeypatch.setattr(tooling, "check_args", lambda name, args: None)
    monkeypatch.setattr(tooling, "handle", lambda project, store, name, args, **kw: calls.append(name) or {"ok": True, "tool": name})
    env = {"ORCHESTRA_REPO": str(tmp_path), "ORCHESTRA_TOOLS": GROUPS, access.TOKEN_ENV: "t"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return calls


def test_the_supervisors_gated_tools_answer_over_the_bash_path(supervisor_env, capsys):
    offered = {t.name: t for t in tools_for(ROLES["supervisor"].tools)}
    assert set(SIX) <= set(offered)
    assert any(offered[n] != BY_NAME[n] for n in SIX)          # gating changed some: the case that broke
    c = caller_mod.Caller(groups=list(ROLES["supervisor"].tools))
    for name in SIX:
        assert c.tool(name) is not None
        assert c.tool(name).description == offered[name].description   # the tool as offered, gated
    for name in SIX:
        capsys.readouterr()
        assert main(["tool", name, "{}"]) == 0
        out = capsys.readouterr().out
        assert "unknown tool" not in out and json.loads(out) == {"ok": True, "tool": name}
    assert supervisor_env == list(SIX)


def test_the_mcp_path_lists_the_same_names(supervisor_env, capsys):
    server = mcp.Server()
    capsys.readouterr()
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    listed = [t["name"] for t in json.loads(capsys.readouterr().out)["result"]["tools"]]
    assert listed == [t.name for t in caller_mod.Caller(groups=GROUPS.split(",")).tools]
    assert set(SIX) <= set(listed)
    for name in SIX:
        server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": {}}})
        assert "unknown tool" not in capsys.readouterr().out
