"""What the design tools' tests share: a project in a git checkout on main, the Supervisor's
store, a turn begun with the owner's message (as the host records it), and a call through
``tooling`` as an agent's session makes it.

The declared layer is Orchestra's own (``orchestra.sd.intended``) through its host's events
endpoint, served here in-process (``intended_api.py`` is the seam)."""

from __future__ import annotations

import subprocess
from contextlib import contextmanager

import pytest
from conftest import agents_project

from orchestra_agents import intended_api, tooling
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore

WHY = "the owner asked for it in the chat just now"


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@contextmanager
def core_endpoint(project):
    """Orchestra's events endpoint for ``project``, as its host serves it (only with its declared layer installed)."""
    from orchestra import endpoint
    from orchestra.endpoint.server import EventsEndpoint
    from orchestra.events import Events as CoreEvents
    ep = EventsEndpoint(project.orchestra, CoreEvents.for_project(project.orchestra), ("127.0.0.1", 0),
                        endpoint.token(project.orchestra), log=lambda _: None)
    ep.start()
    try:
        yield ep
    finally:
        ep.stop()


@pytest.fixture
def project(tmp_path, monkeypatch):
    intended_api._cache.clear()
    monkeypatch.delenv("ORCHESTRA_EVENTS_ENDPOINT", raising=False)
    monkeypatch.delenv("ORCHESTRA_EVENTS_TOKEN", raising=False)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    with core_endpoint(p):
        yield p


@pytest.fixture
def store(project):
    return AgentStore(project, Events.for_project(project, source="supervisor"))


@pytest.fixture
def drafting(monkeypatch):
    """The installed Orchestra takes the draft flag (an Orchestra that does not ignores the keys it
    does not know), so a draft instrument expires as it does with the flag."""
    from orchestra_agents import design_instruments
    monkeypatch.setattr(design_instruments, "supports_draft", lambda: True)


def say(store, message: str, n: int = 1) -> None:
    """The owner's message begins a Supervisor turn (host._turn_began)."""
    store.set_agent_state("supervisor", turn={"n": n, "id": f"sessions/supervisor/{n}", "message": message, "at": 1.0})


def call(project, store, tool, role="supervisor", **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role=role)


def layer(project) -> dict:
    return intended_api.current(project)


def journal(project) -> list[dict]:
    """The declared layer's journal, read in process (in a test only)."""
    return project.orchestra.intended.journal()


def changes(project) -> list[dict]:
    """Every change of the journal, in order, with its op as sent."""
    out = []
    for e in journal(project):
        out += [{**op, "change": c["change"]} for op, c in zip(e.get("sent") or e["ops"], e["changes"], strict=True)]
    return out


def request(store, control: str, element: str, change: str | None = None, source: str = "owner") -> str:
    """The owner's click on a page, as Orchestra's web server publishes and notes it
    (``requests.publish``); its id. From any other ``source``, a
    bare event on the bus, which nothing notes."""
    part, _, eid = element.partition(":")
    body = {"control": control, "part": part, "id": eid, "change": change, "from": "Machines page"}
    if source != "owner":
        return store.events.publish("orchestra.intent.requested", body, source=source, subject=element)["id"]
    from orchestra.events import Events as CoreEvents
    from orchestra.sd.intended import requests
    project = store.project
    return requests.publish(project.orchestra.intended, CoreEvents.for_project(project.orchestra), body)["id"]


def bare_request(store, control: str, element: str) -> str:
    """An ``orchestra.intent.requested`` from ``owner`` put on the bus by hand: not noted."""
    part, _, eid = element.partition(":")
    return store.events.publish("orchestra.intent.requested", {"control": control, "part": part, "id": eid, "change": None,
                                                              "from": "Machines page"}, source="owner", subject=element)["id"]


def model_with(project, stocks: dict[str, tuple[str, dict]], flows: list[tuple[str, str | None, str | None]] = ()):
    """Save a discovered model version: each stock ``id: (series name, labels)``, each flow ``(id, from, to)``."""
    from orchestra.sd.model import Flow, Model, SeriesRef, Stock
    from orchestra.sd.store import ModelStore
    m = Model("m", "m", stocks=[Stock(sid, sid, series=SeriesRef(name, labels)) for sid, (name, labels) in stocks.items()],
              flows=[Flow(fid, fid, a, b) for fid, a, b in flows])
    return ModelStore.for_state(project.orchestra.state_dir).save(m, "for the test")
