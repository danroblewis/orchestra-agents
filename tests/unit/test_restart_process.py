"""``restart_process``: the Supervisor restarts a launcher process by name, through Orchestra (T2k s16:
the Supervisor fixed the setting that crashed the Outbox sender and then said it could not restart
it). Both a project's program (through the real launcher here) and a plugin's process are restarted
by Orchestra's call, ``runtime.request_restart``: the tool records the request and the launcher
carries it out at its next tick. Each request is the Supervisor's act on the bus, with its reason,
and in its ledger; a refusal is said in the tool's words;
and the dummy harness's Supervisor gets the tool and the digest that names the stopped sender."""

import subprocess
import time
from pathlib import Path

import pytest
from conftest import agents_project
from orchestra import runtime
from test_restart_machine import WHY, launcher, pid_of, project, wait_pid  # noqa: F401 - the fixtures

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
    assert out["ok"] and out["requested"] == "sleeper" and out["owner"] == "project" and "next tick" in out["said"]
    end = time.time() + 10
    while time.time() < end and pid_of(project, "sleeper") in (None, old):
        time.sleep(0.1)
    assert pid_of(project, "sleeper") not in (None, old)                # the launcher restarted it at its tick
    assert "restart_requests" not in runtime.read(project.orchestra)    # and cleared the request
    evs, _ = store.events.read(0, 1000)
    [e] = [e for e in evs if e["type"] == "process.restart_requested"]
    assert (e["name"], e["owner"], e["reason"], e["source"]) == ("sleeper", "project", WHY, "supervisor")


def test_a_plugins_process_is_requested_through_orchestras_restart_call(project):  # noqa: F811
    held(project, {"outbox.sender": SENDER})
    store = supervisor(project)
    args = {"name": "outbox.sender", "reason": WHY}
    out = tooling.handle(project, store, "restart_process", args, role="supervisor")
    assert out["ok"] and out["owner"] == "outbox" and "outbox plugin's process" in out["said"] and "next tick" in out["said"]
    req = runtime.read(project.orchestra)["restart_requests"]["outbox.sender"]
    assert (req["by"], req["reason"]) == ("supervisor", WHY)
    evs, _ = store.events.read(0, 1000)
    [e] = [e for e in evs if e["type"] == "process.restart_requested"]
    assert (e["name"], e["owner"], e["reason"], e["by"], e["source"]) == ("outbox.sender", "outbox", WHY, "supervisor", "supervisor")
    assert ledger.did("restart_process", args, out) == f"asked orchestra run to restart the process outbox.sender: {WHY}"


def test_a_refusal_is_said_in_the_tools_words(project):  # noqa: F811
    with pytest.raises(StoreError, match="no `orchestra run` launches this project's processes now"):
        restart(project, supervisor(project), "outbox.sender")
    held(project, {"outbox.sender": SENDER})
    with pytest.raises(StoreError, match="its processes are: outbox.sender"):
        restart(project, supervisor(project), "outbox")
    with pytest.raises(StoreError, match="the reason for the restart"):
        restart(project, supervisor(project), "outbox.sender", reason="fixed")
    with pytest.raises(StoreError, match="needs the 'setup' tool group"):
        restart(project, supervisor(project), "outbox.sender", role="coordinator")
    assert "restart_requests" not in runtime.read(project.orchestra)


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
    assert not told["is_error"] and "requested" in told["text"] and "next tick" in told["text"]   # told how it restarts
    assert runtime.read(p.orchestra)["restart_requests"]["outbox.sender"]["by"] == "supervisor"
