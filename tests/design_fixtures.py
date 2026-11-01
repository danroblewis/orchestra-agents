"""What the design tools' tests share: a project in a git checkout on main, the Supervisor's
store, a turn begun with the owner's message (as the host records it), and a call through
``tooling`` as an agent's session makes it. The declared layer is the agents' stand-in
(``intended_standin.py``): an in-process, file-backed fake of Orchestra's ``project.intended``
in the proposal's shapes, until Orchestra has its own (``intended_api.py``, the seam)."""

from __future__ import annotations

import subprocess

import pytest
from conftest import agents_project

from orchestra_agents import intended_api, tooling
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore

WHY = "the owner asked for it in the chat just now"


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    return p


@pytest.fixture
def store(project):
    return AgentStore(project, Events.for_project(project, source="supervisor"))


def say(store, message: str, n: int = 1) -> None:
    """The owner's message begins a Supervisor turn (host._turn_began)."""
    store.set_agent_state("supervisor", turn={"n": n, "id": f"sessions/supervisor/{n}", "message": message, "at": 1.0})


def call(project, store, tool, role="supervisor", **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role=role)


def layer(project) -> dict:
    return intended_api.current(project)


def changes(project) -> list[dict]:
    """Every change of the journal, in order, with its op as sent."""
    out = []
    for e in intended_api.layer(project).journal():
        out += [{**op, "change": c["change"]} for op, c in zip(e["ops"], e["changes"], strict=True)]
    return out
