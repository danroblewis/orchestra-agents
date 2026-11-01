"""The agents' host stops with the `orchestra run` that launched it (H7): stopped (SIGTERM, the
launcher stops what it started) or killed (SIGKILL, the launcher never gets to: the host sees its
parent gone, parent.py). Either way no `orchestra agents run` of the project is left within 10 s.
On the dummy harness, no model."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
from conftest import agents_project

from demo.run import wait_until
from orchestra_agents.store import AgentStore

GONE_WITHIN_S = 10.0


def agents_hosts(repo: Path) -> list[psutil.Process]:
    """Every `orchestra agents run` whose working directory is this project's repository."""
    out = []
    for p in psutil.process_iter(["cmdline", "cwd"]):
        try:
            cmd = p.info["cmdline"] or []
            if p.info["cwd"] == str(repo) and "agents" in cmd and "run" in cmd and p.status() != psutil.STATUS_ZOMBIE:
                out.append(p)
        except psutil.Error:
            continue
    return out


def everything_in(repo: Path) -> list[psutil.Process]:
    out = []
    for p in psutil.process_iter(["cwd"]):
        try:
            if p.info["cwd"] == str(repo) and p.pid != os.getpid():
                out.append(p)
        except psutil.Error:
            continue
    return out


@pytest.fixture
def running(tmp_path, monkeypatch):
    """`orchestra run` on a project whose agents run on the dummy harness, its host up."""
    env = {"ORCHESTRATOR_HOME": str(tmp_path / "home"), "CLAUDE_BINARY": str(tmp_path / "no-claude")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    repo = (tmp_path / "app").resolve()
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    project = agents_project(repo, harness="dummy")
    store = AgentStore(project)
    with (tmp_path / "orchestra.log").open("ab") as log:
        run = subprocess.Popen([sys.executable, "-m", "orchestra", "run"], cwd=repo, stdout=log, stderr=subprocess.STDOUT,
                               env={**os.environ, "ORCHESTRA_REPO": str(repo), "PYTHONUNBUFFERED": "1"})
    try:
        wait_until("the agents' host", lambda: bool((store.runtime().get("control") or {}).get("port")), timeout_s=60)
        assert agents_hosts(repo), (tmp_path / "orchestra.log").read_text()
        yield run, repo, tmp_path
    finally:
        if run.poll() is None:
            run.kill()
            run.wait()
        for p in everything_in(repo):                  # whatever the test left: never an orphan of ours
            try:
                p.kill()
            except psutil.Error:
                pass


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGKILL], ids=["stopped", "killed"])
def test_no_agents_host_outlives_orchestra_run(running, sig):
    run, repo, tmp_path = running
    [host] = agents_hosts(repo)
    assert host.ppid() == run.pid
    run.send_signal(sig)
    deadline = time.time() + GONE_WITHIN_S
    while time.time() < deadline and agents_hosts(repo):
        time.sleep(0.2)
    left = agents_hosts(repo)
    host_log = next(iter((tmp_path / "home").rglob("agents-host.log")), None)
    assert not left, (left, host_log.read_text() if host_log else None)
    if sig == signal.SIGKILL:
        assert "is gone; stopping" in host_log.read_text()
