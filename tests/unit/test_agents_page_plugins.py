"""The Agents page says what starts the built-in team's work by what is installed: the tasking
plugin's tickets, a workflow's offers, or neither (R2: no "when the task system assigns one"
without the task system)."""

import subprocess
from pathlib import Path

import pytest
from conftest import agents_project
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestra_agents import installed, web

PAGE = Path(__file__).parents[2] / "orchestra_agents" / "static" / "agents.html"


class EP:
    def __init__(self, name):
        self.name = name


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo, provider="p", model="m")


def test_the_org_read_says_which_plugins_start_the_teams_work(project, monkeypatch):
    monkeypatch.setattr(installed, "entry_points", lambda group: [EP("agents"), EP("workflows")])
    app = FastAPI()
    app.include_router(web.router(project), prefix="/api/plugins/agents")
    got = TestClient(app).get("/api/plugins/agents/org").json()
    assert got["plugins"] == {"tasking": False, "workflows": True}


def test_the_page_never_says_the_task_system_assigns_without_reading_whether_it_is_there():
    html = PAGE.read_text(encoding="utf-8")
    body = html[html.index("function perOffer"):html.index("function teamNote")]
    assert html.count("when the task system assigns") == body.count("when the task system assigns") == 2
    assert "if (p.tasking)" in body                                       # said only when the tasking plugin is there
    assert "the tasking plugin (orchestra-tasking), which is not installed" in html
