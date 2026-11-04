"""``restart_process``: the Supervisor restarts a launcher process by name, through Orchestra (T2k s16:
the Supervisor fixed the setting that crashed the Outbox sender and then said it could not restart
it). A project's program goes the way ``restart_machine`` does, through the real launcher here; a
plugin's process waits for Orchestra's restart call and says so until it is there; with it, that
call is used. Each restart is the Supervisor's act on the bus, with its reason, and in its ledger;
and the dummy harness's Supervisor gets the tool and the digest that names the stopped sender."""

import subprocess
from pathlib import Path

import pytest
from conftest import agents_project
from orchestra import runtime
from test_restart_machine import WHY, launcher, project, wait_pid  # noqa: F401 - the fixtures

from orchestra_agents import ledger, prompting, tooling
from orchestra_agents.events import Events
from orchestra_agents.harness.tools import BY_NAME, tools_for
from orchestra_agents.store import AgentStore, StoreError

SENDER = {"pid": None, "state": "gave_up", "code": 2, "owner": "outbox", "restarts": 4, "stop_grace_s": 1.0}


def supervisor(p):
    return AgentStore(p, Events.for_project(p, source="supervisor"))


def restart(p, store, name, role="supervisor", reason=WHY):
    return tooling.handle(p, store, "restart_process", {"name": name, "reason": reason}, role=role)


def held(p, processes):
    """The record as a live `orchestra run` would have it (this test process holds the launch)."""
    runtime.update(p.orchestra, lambda d: d.update(launcher=runtime.me(), processes=processes))


def test_a_project_program_is_restarted_through_the_launcher(project, launcher):  # noqa: F811
    store = supervisor(project)
    old = wait_pid(project, "sleeper")
    out = restart(project, store, "sleeper")
    assert out["ok"] and out["pid_before"] == old and out["pid"] not in (None, old)
    evs, _ = store.events.read(0, 1000)
    [e] = [e for e in evs if e["type"] == "machine.restarted"]
    assert e["reason"] == WHY and e["source"] == "supervisor"


def test_a_plugins_process_waits_for_orchestras_restart_call(project):  # noqa: F811
    held(project, {"outbox.sender": SENDER})
    assert not hasattr(runtime, "request_restart")                  # the core has none yet: asked of its coordinator
    with pytest.raises(StoreError, match="outbox plugin's process, and Orchestra's launcher has no call yet"):
        restart(project, supervisor(project), "outbox.sender")
    with pytest.raises(StoreError, match="its processes are: outbox.sender"):
        restart(project, supervisor(project), "outbox")
    with pytest.raises(StoreError, match="the reason for the restart"):
        restart(project, supervisor(project), "outbox.sender", reason="fixed")
    with pytest.raises(StoreError, match="needs the 'setup' tool group"):
        restart(project, supervisor(project), "outbox.sender", role="coordinator")


def test_with_orchestras_restart_call_it_is_used_and_logged_as_the_supervisors_act(project, monkeypatch):  # noqa: F811
    held(project, {"outbox.sender": SENDER})
    asked = []

    def request_restart(o, name, *, by, reason):                    # what the launcher would do at its next tick
        asked.append((name, by, reason))
        runtime.update(o, lambda d: d["processes"][name].update(pid=4242, state="running", code=None))
    monkeypatch.setattr(runtime, "request_restart", request_restart, raising=False)
    store = supervisor(project)
    args = {"name": "outbox.sender", "reason": WHY}
    out = tooling.handle(project, store, "restart_process", args, role="supervisor")
    assert asked == [("outbox.sender", "supervisor", WHY)] and out["pid"] == 4242 and "started again" in out["said"]
    evs, _ = store.events.read(0, 1000)
    [e] = [e for e in evs if e["type"] == "process.restarted"]
    assert (e["name"], e["owner"], e["reason"], e["by"], e["source"]) == ("outbox.sender", "outbox", WHY, "supervisor", "supervisor")
    assert ledger.did("restart_process", args, out) == f"restarted the process outbox.sender (pid None -> 4242): {WHY}"


def test_the_tool_is_the_supervisors_setup_and_its_prompt_says_after_the_fix():
    assert BY_NAME["restart_process"].group == "setup"
    assert "restart_process" in [t.name for t in tools_for(("setup",))]
    text = (Path(prompting.__file__).parent / "prompts" / "supervisor.md").read_text()
    assert "`restart_process`" in text and "after its cause is fixed, never\nbefore" in text


def test_the_dummy_supervisor_reads_the_stopped_sender_first_and_is_told_how_it_restarts(tmp_path, monkeypatch):
    # the dummy harness's Supervisor, as it ships: one turn reads "what changed since" and tries the restart
    from test_dummy import session

    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    p = agents_project(repo, harness="dummy")
    (repo / "orchestra.toml").write_text((repo / "orchestra.toml").read_text() + 'dummy_script = "dummy.toml"\n')
    (repo / "dummy.toml").write_text('''
[[rule]]
role = "supervisor"
steps = [
  { tool = "read_events", args = { since = "1h" }, keep = "d" },
  { tool = "restart_process", args = { name = "outbox.sender", reason = "its overlay keys are back; it crashed on them" } },
]
reply = "{kept.d.digest}"
''')
    p.reload_config()
    held(p, {"outbox.sender": SENDER})
    events = Events.for_project(p, source="orchestra-host")
    events.publish("orchestra.process.exited", {"name": "outbox.sender", "pid": 1, "code": 2, "restart_in": None})
    events.publish("orchestra.process.gave_up", {"name": "outbox.sender", "code": 2, "failures": 5})
    s = session(p, "supervisor", ("events", "setup", "observe"), repo)
    res = s.prompt("[The human's message]\nWhat changed since an hour ago?", timeout=None, idle_timeout=120)
    assert res.error is None, res.error
    look = res.text.split("\n\n")[1].splitlines()
    assert look[0] == "Look first:" and look[1].startswith(
        "- A process the launcher stopped retrying: outbox.sender (outbox plugin's) stopped at ")
    assert "(exit code 2, 5 crashes in a row), not tried again." in look[1]
    from orchestra_agents.harness import read_transcript
    msgs = read_transcript({"harness": "dummy", "session_file": s.session_file})
    told = next(m for m in msgs if m["role"] == "toolResult" and m["tool_name"] == "restart_process")
    assert told["is_error"] and "orchestra run" in told["text"]       # refused, saying how it restarts
