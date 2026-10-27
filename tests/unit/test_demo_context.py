"""The demo starts with a project context, so the Supervisor acts on a request instead of first
asking what the project is for."""

from demo.run import CONTEXT, make_project
from orchestra_agents.config import load_project
from orchestra_agents.prompting import read_context


def test_the_demo_project_has_a_context_the_supervisor_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    target = make_project(tmp_path / "demo")
    text = read_context(load_project(target))
    assert text == CONTEXT and "## Objectives\n1. Show two kinds of worker on one queue" in text
    assert "## Non-goals\n- No product" in text
