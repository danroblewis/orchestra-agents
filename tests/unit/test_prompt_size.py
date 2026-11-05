"""The Supervisor's system prompt, as the harness sends it, stays under 35,000 characters (the owner,
2026-11-04: "we should stay under 35k for a regular system prompt and split the rest into separate
prompt files"): its fixed core, its role, its always-needed rules, the index of its skills, its
machine spec and the tools it holds (by name through the MCP server; with their arguments when they
are called from the shell, the larger)."""

import subprocess

import pytest
from conftest import agents_project

from orchestra_agents import installed, prompting, supervisor_spec
from orchestra_agents.builtins import ROLES
from orchestra_agents.harness import bash_tools
from orchestra_agents.org import built_in_agents

LIMIT = 35_000


def _project(tmp_path, monkeypatch, work: str):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "orchestra.toml").write_text(f'[project]\nwork = "{work}"\n')
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", "--allow-empty"], cwd=repo, check=True)
    project = agents_project(repo)
    supervisor_spec.ensure(project)                  # its starting spec: keep_flow and review_round, in the prompt
    return project


def sent(project, via: str) -> str:
    """The Supervisor's system prompt as its session gets it: the host's (``with_tools``), and on the
    shell route the harness's (``bash_tools.system``)."""
    groups = ROLES["supervisor"].tools
    system = prompting.with_tools(prompting.agent_prompt(project, built_in_agents()["supervisor"]), groups)
    return bash_tools.system(system, groups, path="") if via == "bash" else system


@pytest.mark.parametrize("tasking", [True, False])
@pytest.mark.parametrize("work", ["tasks", "code"])
@pytest.mark.parametrize("via", ["mcp", "bash"])
def test_the_supervisors_prompt_as_sent_is_under_35000_characters(tmp_path, monkeypatch, tasking, work, via):
    monkeypatch.setattr(installed, "tasking", lambda: tasking)
    project = _project(tmp_path, monkeypatch, work)
    text = sent(project, via)
    assert "## Your machine spec" in text and "### Duty `keep_flow`" in text       # the spec is counted
    assert "## Skills: read the one a request needs" in text and "`setup`: " in text
    assert len(text) < LIMIT, f"{len(text):,} characters ({via}, work={work}, tasking={tasking})"


def test_the_always_needed_rules_stay_in_the_prompt(tmp_path, monkeypatch):
    text = " ".join(sent(_project(tmp_path, monkeypatch, "code"), "mcp").split())
    for rule in ("Your fixed core", "Anything sent as the owner goes through the outbox",       # the core's guardrails
                 "You never read outside the project's tree", "never name a path as an example",   # bounds, tokens
                 "Read nothing from another store", "Never write or widen `explore`",            # the owner's scopes
                 "Quote them exactly", "never fill it with a default of yours",                   # the owner's words
                 "You never edit a file in the checkout", "Never guess at a cause",
                 "You are the Orchestra Supervisor", "## Your tools in this session"):
        assert rule in text, rule


def test_on_the_shell_route_the_tools_are_listed_once(tmp_path, monkeypatch):
    text = sent(_project(tmp_path, monkeypatch, "code"), "bash")
    assert "## Your tools in this session" not in text                # the names alone: replaced by the list below
    assert text.count("- `check_setup` {") == 1 and "`reason`, required wherever a tool takes it" in text
