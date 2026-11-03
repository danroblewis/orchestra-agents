"""End-to-end: ``[agents] tools_via = "file"`` through the host (``orchestra agents run``, on the
dummy harness). A confirmed Universal Machine answers a Workflows offer with no Orchestra tools: its
turn writes ``$ORCHESTRA_OUTPUT``, and after the turn the host publishes its event and finishes the
offer with the outcome, the summary and the fields as ``facts.set``, as the machine's instance."""

import os
import signal
import subprocess
import sys
import time

import pytest
from conftest import agents_project
from floor_helpers import ScriptedFloor, universal_machine

from demo.run import wait_until
from orchestra_agents.roles import RepoRoleStore, RoleDef
from orchestra_agents.store import AgentStore

SCRIPT = """
[[rule]]
role = "triage"

[[rule.steps]]
tool = "emit_event"
args = { type = "triage_noted" }

[[rule.steps]]
write = "{output}"
text = '''events=[{"type": "triage_noted", "subject": "I1", "data": "a blank page"}]
summary=It is a bug.
outcome=triaged
fields={"severity": "high"}
'''
"""


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    for k in ("ORCHESTRA_SOURCE", "ORCHESTRA_ROLE"):
        monkeypatch.delenv(k, raising=False)
    repo = (tmp_path / "app").resolve()
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    project = agents_project(repo, harness="dummy")
    (repo / "orchestra.toml").write_text((repo / "orchestra.toml").read_text() + 'tools_via = "file"\n')
    (repo / ".orchestra").mkdir(exist_ok=True)
    (repo / ".orchestra" / "dummy.toml").write_text(SCRIPT)
    d = universal_machine(project)
    (d / "machine.toml").write_text((d / "machine.toml").read_text() + 'emits = ["triage_noted"]\n')
    RepoRoleStore(project).save(RoleDef(name="triage_job", description="triages", tools=["escalate", "events"]), "Triage.")
    store = AgentStore(project)
    log = (tmp_path / "host.log").open("ab")
    proc = subprocess.Popen([sys.executable, "-m", "orchestra", "agents", "run"], cwd=repo, stdout=log, stderr=subprocess.STDOUT,
                            env={**os.environ, "ORCHESTRA_REPO": str(repo), "PYTHONUNBUFFERED": "1"}, start_new_session=True)
    try:
        wait_until("the agents' host", lambda: bool((store.runtime().get("control") or {}).get("port")), timeout_s=60)
        time.sleep(1.5)
        yield project, store, ScriptedFloor(project), tmp_path
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        log.close()


def test_a_file_mode_machine_finishes_its_offer_from_the_file(host):
    project, store, floor, tmp_path = host
    log = lambda: (tmp_path / "host.log").read_text()
    oid = floor.offer("triager", "I1", "A user says the login page is blank.", outcomes=["triaged", "duplicate"], heartbeat_s=1.0)
    wait_until("the offer finished", lambda: floor.of(oid, "offer.finished"), timeout_s=60)
    [fin] = floor.of(oid, "offer.finished")
    assert fin["source"] == "triage/1", log()
    assert fin["data"]["outcome"] == "triaged" and fin["data"]["summary"] == "It is a bug."
    assert fin["data"]["facts"] == {"set": {"severity": "high"}}
    evs, _ = floor.events.after(0)
    noted = [e for e in evs if e["type"] == "triage_noted"]
    assert [(e["source"], e["data"]["subject"], e["data"]["note"]) for e in noted] == [("triage/1", "I1", "a blank page")]
    assert evs.index(noted[0]) < evs.index(next(e for e in evs if e["type"] == "offer.finished"))   # in file order
    prompt = (project.logs_dir / "agents" / "triage-I1.prompt.md").read_text()
    assert "no Orchestra tools" in prompt and "Your tools in this session" not in prompt
    assert (store.find_session("triage-I1") or {}).get("output", {}).get("refused") == []
