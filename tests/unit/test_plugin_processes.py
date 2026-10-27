"""What the plugin asks `orchestra run` to keep running: the agents' host, only for a project
that configures the agents."""

import subprocess
import sys

from conftest import agents_project
from orchestra.config import init_project as init_orchestra

from orchestra_agents.config import load_project
from orchestra_agents.web import processes


def test_the_host_is_declared_for_a_project_with_an_agents_section(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    init_orchestra(repo)
    assert processes(load_project(repo)) == []                  # installed beside Orchestra, no [agents]: no AI starts
    [p] = processes(agents_project(repo))
    assert (p.name, p.restart) == ("agents", "always")
    assert p.argv() == [sys.executable, "-m", "orchestra_agents", "run"]
