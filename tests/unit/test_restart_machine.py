"""Restarting a program through Orchestra's real launcher, run here in a thread: the entry is taken
out until the launcher has stopped it, then put back byte for byte, and the launcher starts it
again. Nothing is committed; the restart is on the bus."""

import subprocess
import sys
import threading
import time

import pytest
from conftest import agents_project
from orchestra import runtime
from orchestra.launcher import Launcher, Process

from orchestra_agents import tooling
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore, StoreError

SLEEPER = f"""
[[machines]]
name = "sleeper"
command = "{sys.executable} -c 'import time; time.sleep(600)'"
stop_grace = 2

[[collect]]
name = "disk"
command = "machines/disk.sh"
"""
WHY = "it stopped writing after the network came back"


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    (repo / "orchestra.toml").write_text((repo / "orchestra.toml").read_text() + SLEEPER)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return p


@pytest.fixture
def launcher(project):
    """Orchestra's launcher for the project, holding the launch as `orchestra run` does, ticking."""
    o = project.orchestra
    runtime.update(o, lambda d: d.__setitem__("launcher", runtime.me()))
    plugin = Process("host", [sys.executable, "-c", "import time; time.sleep(600)"], owner="agents")
    lau = Launcher(o, publish=lambda *a: None, log=lambda m: None, plugin_processes=[plugin])
    stop = threading.Event()

    def run():
        while not stop.wait(0.2):
            lau.tick()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    yield lau
    stop.set()
    t.join(timeout=5)
    lau.stop_all()


def pid_of(project, name):
    return (runtime.read(project.orchestra).get("processes") or {}).get(name, {}).get("pid")


def wait_pid(project, name):
    end = time.time() + 10
    while time.time() < end and not pid_of(project, name):
        time.sleep(0.1)
    return pid_of(project, name)


def test_a_machine_is_stopped_and_started_again_with_its_entry_unchanged(project, launcher):
    store = AgentStore(project, Events.for_project(project, source="supervisor"))
    before_text, head = (project.repo_root / "orchestra.toml").read_text(), git(project.repo_root, "rev-parse", "HEAD")
    old = wait_pid(project, "sleeper")
    assert old
    out = tooling.handle(project, store, "restart_machine", {"name": "sleeper", "reason": WHY}, role="supervisor")
    assert out["ok"] and out["pid_before"] == old and out["pid"] not in (None, old) and "started again" in out["said"]
    assert not runtime.alive(old) and runtime.alive(out["pid"])
    assert (project.repo_root / "orchestra.toml").read_text() == before_text
    assert git(project.repo_root, "rev-parse", "HEAD") == head                       # nothing to commit
    evs, _ = store.events.read(0, 1000)
    [ev] = [e for e in evs if e["type"] == "machine.restarted"]
    assert ev["name"] == "sleeper" and ev["reason"] == WHY and ev["source"] == "supervisor" and ev["pid"] == out["pid"]


def test_what_cannot_be_restarted_this_way_says_how_it_can(project, launcher):
    store = AgentStore(project, Events.for_project(project, source="supervisor"))
    wait_pid(project, "agents.host")
    restart = lambda name, role="supervisor": tooling.handle(project, store, "restart_machine",
                                                             {"name": name, "reason": WHY}, role=role)
    with pytest.raises(StoreError, match="plugin's process"):
        restart("agents.host")
    with pytest.raises(StoreError, match="is a collector"):
        restart("disk")
    with pytest.raises(StoreError, match="the entries are: sleeper"):
        restart("nope")
    with pytest.raises(StoreError, match="only the supervisor"):
        restart("sleeper", role="coordinator")


def test_without_orchestra_run_there_is_nothing_to_restart(project):
    store = AgentStore(project, Events.for_project(project, source="supervisor"))
    with pytest.raises(StoreError, match="no `orchestra run` launches"):
        tooling.handle(project, store, "restart_machine", {"name": "sleeper", "reason": WHY}, role="supervisor")
