"""End-to-end: the agents' host answers the Workflows floor's offers (floor_runner.py), beside the
task system's, in one host. The host runs as its own process (``orchestra agents run``) on the
dummy harness; a scripted floor publishes ``offer.made`` on the project's file bus as
``coordinator/dispatch`` and a rival machine claims as itself, the way orchestra-workflows' tests
drive its floor. Seen on the bus:

- a confirmed Universal Machine (``triage``, spec ``role = "triager"``) announces itself with its
  role before it claims, claims, says ``offer.alive`` within the offer's heartbeat while its turn
  runs and finishes with the outcome it reported through ``report_outcome``;
- the built-in worker takes a ``worker`` offer in the handed worktree, commits there, and finishes
  ``ready`` (its ``report_ticket``, mapped);
- an offer a rival claimed first: the host's claim loses and it says nothing more;
- a withdrawn offer stops the turn: the session is cut, and nothing more is said for it;
- a task system's assignment in the same host is answered as before (``task.accepted``, then
  ``review.requested``)."""

import os
import signal
import subprocess
import sys
import time

import pytest
from conftest import agents_project
from floor_helpers import ScriptedFloor, universal_machine

from demo.run import wait_until
from orchestra_agents.store import AgentStore

SCRIPT = '''
[[rule]]
role = "triage"
prompt = "SLOW"
steps = [{ sleep = 120 }]

[[rule]]
role = "triage"
steps = [{ say = "Reading the report." }, { sleep = 2.5 }, { tool = "report_outcome", args = { outcome = "triaged", summary = "It is a bug." } }]
reply = "Triaged."
'''


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ORCHESTRA_SOURCE", raising=False)
    monkeypatch.delenv("ORCHESTRA_ROLE", raising=False)
    repo = (tmp_path / "app").resolve()
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    project = agents_project(repo, harness="dummy")
    (repo / ".orchestra").mkdir(exist_ok=True)
    (repo / ".orchestra" / "dummy.toml").write_text(SCRIPT)
    universal_machine(project)
    for k, v in {"user.email": "t@example.invalid", "user.name": "t"}.items():
        git("config", k, v, cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "start", cwd=repo)
    store = AgentStore(project)
    log = (tmp_path / "host.log").open("ab")
    proc = subprocess.Popen([sys.executable, "-m", "orchestra", "agents", "run"], cwd=repo, stdout=log, stderr=subprocess.STDOUT,
                            env={**os.environ, "ORCHESTRA_REPO": str(repo), "PYTHONUNBUFFERED": "1"}, start_new_session=True)
    try:
        wait_until("the agents' host", lambda: bool((store.runtime().get("control") or {}).get("port")), timeout_s=60)
        time.sleep(1.5)                                   # its first tick has read the bus's end: offers from now are new
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


def types_of(floor, oid, source):
    return [e["type"] for e in floor.of(oid) if e["source"] == source]


def test_the_host_answers_the_floor_s_offers(host):
    project, store, floor, tmp_path = host
    log = lambda: (tmp_path / "host.log").read_text()

    # a confirmed Universal Machine: announced with its role, claims, heartbeats, finishes as it reported
    t1 = floor.offer("triager", "I1", "A user says the login page is blank.", outcomes=["triaged", "duplicate"], heartbeat_s=1.0)
    wait_until("the triage offer finished", lambda: floor.of(t1, "offer.finished"), timeout_s=60)
    evs = floor.of(t1)
    assert types_of(floor, t1, "triage/1")[0] == "offer.claimed", log()
    assert types_of(floor, t1, "triage/1").count("offer.alive") >= 2
    [fin] = floor.of(t1, "offer.finished")
    assert fin["source"] == "triage/1" and fin["data"]["outcome"] == "triaged" and fin["data"]["summary"] == "It is a bug."
    assert all(e["subject"] == "I1" and e.get("causationid") == t1 for e in evs if e["type"] != "offer.made")
    all_evs, _ = floor.events.after(0)
    [hello] = [e for e in all_evs if e["type"] == "orchestra.machine.started" and e["source"] == "triage/1"]
    assert hello["data"]["roles"] == ["triager"] and {"offer.made", "offer.withdrawn"} <= set(hello["data"]["listens_for"])
    assert all_evs.index(hello) < all_evs.index(next(e for e in evs if e["type"] == "offer.claimed"))

    # the built-in worker in the handed worktree: commits there, finishes ready (report_ticket, mapped)
    wt = tmp_path / "wt-I2"
    git("worktree", "add", "-q", "-b", "item/I2", str(wt), cwd=project.repo_root)
    base = git("rev-parse", "HEAD", cwd=wt)
    w = floor.offer("worker", "I2", "Write the note.", resources=[{"kind": "worktree", "id": "wt1", "handle": str(wt)}])
    wait_until("the worker offer finished", lambda: floor.of(w, "offer.finished"), timeout_s=60)
    [fin] = floor.of(w, "offer.finished")
    assert fin["source"].startswith("worker/") and fin["data"]["outcome"] == "ready", fin
    assert git("rev-list", "--count", f"{base}..HEAD", cwd=wt) == "1"

    # a rival's claim came first: the host's claim loses, and it says nothing more
    rival = floor.as_machine("worker/9")
    lost = floor.offer("worker", "I3", "Somebody else's.")
    rival.publish("offer.claimed", {"offer": lost}, subject="I3", causationid=lost)
    wait_until("the host's claim", lambda: [e for e in floor.of(lost, "offer.claimed") if e["source"] != "worker/9"], timeout_s=30)
    time.sleep(4)
    ours = [e for e in floor.of(lost) if e["source"] not in ("worker/9", "coordinator/dispatch")]
    assert [e["type"] for e in ours] == ["offer.claimed"], ours
    assert not [s for s in store.sessions() if s["id"] == "worker-I3"]          # no session was opened for it

    # withdrawn mid-turn: the turn stops and nothing more is said for the offer
    slow = floor.offer("triager", "I4", "SLOW: a long one.", heartbeat_s=1.0)
    wait_until("the slow turn running", lambda: floor.of(slow, "offer.alive"), timeout_s=60)
    floor.withdraw(slow, "I4")
    wait_until("the slow turn stopped", lambda: (store.find_session("triage-I4") or {}).get("status") == "idle", timeout_s=30)
    time.sleep(2)
    after = len(floor.of(slow))
    time.sleep(3)
    assert len(floor.of(slow)) == after and not floor.of(slow, "offer.finished")
    cuts = [m for m in (store.find_session("triage-I4") or {}).get("markers") or [] if m.get("type") == "session.interrupted"]
    assert cuts and cuts[-1]["reason"] == "released by the Workflows floor: a withdraw request", cuts

    # the task system's assignment, in the same host
    folder = tmp_path / "T-1"
    folder.mkdir()
    floor.as_machine("coordinator/dispatch").publish("task.assigned", {
        "assignment": "T-1.worker.1", "role": "worker", "ticket": "T-1", "kind": "work", "workspace": str(folder), "branch": None,
        "lease_s": 180, "brief": {"id": "T-1", "title": "A task", "description": "Do it."}}, subject="T-1")
    wait_until("the task's review requested", lambda: [e for e in floor.events.after(0)[0] if e["type"] == "review.requested"], timeout_s=60)
    tasked = [e for e in floor.events.after(0)[0] if (e.get("data") or {}).get("assignment") == "T-1.worker.1"]
    assert [e["type"] for e in tasked][:2] == ["task.assigned", "task.accepted"] and (folder / "RESULT.md").exists()
