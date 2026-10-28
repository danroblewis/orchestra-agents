"""What the plugin asks `orchestra run` to keep running: the agents' host, whether or not the
project has an [agents] section ([agents] enabled = false turns it off)."""

import subprocess
import sys

from conftest import agents_project
from orchestra.config import init_project as init_orchestra

from orchestra_agents.config import load_project
from orchestra_agents.host import SHUTDOWN_WORST_S
from orchestra_agents.web import STOP_GRACE_S, processes


def test_the_host_is_declared_with_or_without_an_agents_section(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    init_orchestra(repo)
    assert [p.name for p in processes(load_project(repo))] == ["host"]   # no [agents]: the host runs on the default harness
    [p] = processes(agents_project(repo))
    assert (p.name, p.restart) == ("host", "always")                # Orchestra runs it as agents.host
    assert p.argv() == [sys.executable, "-m", "orchestra", "agents", "run", "--wait"]
    assert p.stop_grace_s == STOP_GRACE_S > SHUTDOWN_WORST_S + 5      # Orchestra never kills it mid-shutdown
