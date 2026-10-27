"""Every tool call is checked against the caller's role and its groups, and the role is the one the
host issued the session's token for (access.py; AGENTS.md rules 7 and 10)."""

import json
import subprocess

import pytest
from conftest import FakeHarness, agents_project

from orchestra_agents import access
from orchestra_agents import tooling as api
from orchestra_agents.cli import main
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore, StoreError

SUP = {"role": "supervisor"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    for k in ("ORCHESTRA_ROLE", access.TOKEN_ENV, "ORCHESTRA_TICKET"):
        monkeypatch.delenv(k, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    p = agents_project(repo)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "orchestra"], cwd=repo, check=True)
    return p


def call_api(project, monkeypatch, capsys, env: dict, command: str, args: dict | None = None) -> tuple[int, dict]:
    """`orchestra-agents api` as a shell inside a session runs it, with ``env`` as that shell's environment."""
    for k in ("ORCHESTRA_ROLE", access.TOKEN_ENV, "ORCHESTRA_TICKET"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    capsys.readouterr()
    code = main(["--repo", str(project.repo_root), "api", command, "--args", json.dumps(args or {})])
    return code, json.loads(capsys.readouterr().out)


def test_a_worker_calling_a_supervisor_tool_through_handle_is_refused(project):
    store = AgentStore(project)
    for command, args, group in (("pause_agent", {"name": "supervisor"}, "roles"),
                                 ("create_team", {"name": "x", "lead": "worker", "responsibilities": "r" * 30}, "roles"),
                                 ("set_work_mode", {"work": "code", "reason": "because it is needed now"}, "setup"),
                                 ("list_processes", {}, "observe"), ("escalate_to_supervisor", {"note": "hi"}, "escalate")):
        with pytest.raises(StoreError, match=f"refused {command}: it needs the '{group}' tool group, and role 'worker' has report_ticket"):
            api.handle(project, store, command, args, role="worker")
    assert not store.agent_state("supervisor").get("paused")
    assert not [w for w in store.runtime()["wakeups"] if w["target"] == "supervisor"]


def test_an_unknown_role_is_refused(project):
    with pytest.raises(StoreError, match="unknown role 'ghost'"):
        api.handle(project, AgentStore(project), "list_roles", {}, role="ghost")


def test_a_worker_calling_a_supervisor_tool_through_the_api_is_refused(project, monkeypatch, capsys):
    store = AgentStore(project)
    token = access.issue(store, "worker-T-1", "worker", "T-1")
    code, out = call_api(project, monkeypatch, capsys, {"ORCHESTRA_ROLE": "worker", access.TOKEN_ENV: token},
                         "pause_agent", {"name": "supervisor"})
    assert code == 1 and not out["ok"] and "needs the 'roles' tool group, and role 'worker'" in out["error"]
    assert not store.agent_state("supervisor").get("paused")
    code, out = call_api(project, monkeypatch, capsys, {"ORCHESTRA_ROLE": "worker", access.TOKEN_ENV: token},
                         "report_ticket", {"state": "READY_FOR_REVIEW", "summary": "done"})
    assert code == 0 and out["ok"] and store.pop_report("T-1")["state"] == "READY_FOR_REVIEW"     # its own tool works


def test_a_worker_forging_its_role_is_refused(project, monkeypatch, capsys):
    store = AgentStore(project)
    token = access.issue(store, "worker-T-1", "worker", "T-1")
    access.issue(store, "supervisor", "supervisor")
    forged = {"ORCHESTRA_ROLE": "supervisor", access.TOKEN_ENV: token}
    code, out = call_api(project, monkeypatch, capsys, forged, "pause_agent", {"name": "supervisor"})
    assert code == 1 and "ORCHESTRA_ROLE is 'supervisor', but this session was started as 'worker'" in out["error"]
    for env in ({"ORCHESTRA_ROLE": "supervisor", access.TOKEN_ENV: "made-up"},):
        code, out = call_api(project, monkeypatch, capsys, env, "pause_agent", {"name": "supervisor"})
        assert code == 1 and "was not issued by the host" in out["error"]
    monkeypatch.setenv("ORCHESTRA_ROLE", "supervisor")                    # no token at all: the api does not run
    monkeypatch.delenv(access.TOKEN_ENV, raising=False)
    with pytest.raises(SystemExit, match=access.TOKEN_ENV):
        main(["--repo", str(project.repo_root), "api", "pause_agent", "--args", '{"name": "supervisor"}'])
    with pytest.raises(StoreError, match="no ORCHESTRA_SESSION_TOKEN"):
        access.identify(store, {"ORCHESTRA_ROLE": "supervisor"})
    assert not store.agent_state("supervisor").get("paused")


def test_the_ticket_is_the_tokens_and_an_ended_session_is_refused(project):
    store = AgentStore(project)
    token = access.issue(store, "worker-T-1", "worker", "T-1")
    assert access.identify(store, {access.TOKEN_ENV: token, "ORCHESTRA_TICKET": "T-9"}) == ("worker", "T-1")
    access.revoke(store, "worker-T-1")
    with pytest.raises(StoreError, match="its session has ended"):
        access.identify(store, {access.TOKEN_ENV: token})
    old = access.issue(store, "supervisor", "supervisor")
    new = access.issue(store, "supervisor", "supervisor")                 # the session started over
    assert access.identify(store, {access.TOKEN_ENV: new})[0] == "supervisor"
    with pytest.raises(StoreError):
        access.identify(store, {access.TOKEN_ENV: old})
    assert token not in json.dumps(store.runtime()) and new not in json.dumps(store.runtime())   # only digests are kept


def test_the_host_issues_each_session_its_token(project):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    host.harness = FakeHarness()
    host._open_session("worker-T-1", "worker", "p", ("report_ticket",), project.repo_root, "T-1")
    env = host.harness.sessions[-1].spec.env
    assert access.identify(store, {**env, "ORCHESTRA_TICKET": "T-2"}) == ("worker", "T-1")
    host.recover()                                                        # a new host honours no earlier session
    with pytest.raises(StoreError):
        access.identify(store, env)


def test_a_custom_role_granted_roles_cannot_pause_the_supervisor(project):
    store = AgentStore(project)
    api.handle(project, store, "create_role", {"name": "org_admin", "description": "d", "prompt": "# You admin\n\n" + "Carefully. " * 6,
                                               "tools": ["roles", "team"]}, **SUP)
    api.handle(project, store, "create_agent", {"name": "admin", "roles": ["org_admin"], "kind": "ad_hoc", "session": "ephemeral"}, **SUP)
    assert api.handle(project, store, "org_guide", {}, role="admin")["ok"]          # what the group still gives it
    for command in ("pause_agent", "resume_agent", "restart_agent", "reset_agent", "create_role", "delete_agent"):
        with pytest.raises(StoreError, match=f"refused {command}: .* is the supervisor's alone, and the caller's role is 'admin'"):
            api.handle(project, store, command, {"name": "supervisor"}, role="admin")
    st = store.agent_state("supervisor")
    assert not st.get("paused") and not st.get("restart_requested") and not st.get("reset_requested")


def test_the_supervisors_own_calls_still_work(project, monkeypatch, capsys):
    store = AgentStore(project)
    api.handle(project, store, "create_role", {"name": "owner", "description": "d", "prompt": "# You own\n\n" + "Carefully. " * 6}, **SUP)
    api.handle(project, store, "create_agent", {"name": "po", "roles": ["owner"]}, **SUP)
    assert api.handle(project, store, "pause_agent", {"name": "po"}, **SUP)["paused"]
    token = access.issue(store, "supervisor", "supervisor")
    code, out = call_api(project, monkeypatch, capsys, {"ORCHESTRA_ROLE": "supervisor", access.TOKEN_ENV: token},
                         "resume_agent", {"name": "po"})
    assert code == 0 and out["paused"] is False and not store.agent_state("po")["paused"]
    code, out = call_api(project, monkeypatch, capsys, {access.TOKEN_ENV: token}, "restart_agent", {"name": "po"})
    assert code == 0 and store.agent_state("po")["restart_requested"]     # the role need not be repeated: it is the token's
