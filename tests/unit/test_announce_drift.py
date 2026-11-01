"""What the agents announce is what is read: the mode by the rule the declaration shares, the floor
roles it answers (a change re-announces), and no harness or model, which nothing reads; and a
heartbeat says one line."""

import subprocess
import threading
import time
from types import SimpleNamespace

from conftest import agents_project

from orchestra_agents import offer_turn
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore


def project_at(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def test_an_announcement_carries_what_is_read_and_a_changed_floor_role_announces_again(tmp_path, monkeypatch):
    project = project_at(tmp_path, monkeypatch)
    host = AgentHost(project, AgentStore(project), Events.for_project(project))
    roles = {"triager": "worker"}
    monkeypatch.setattr("orchestra_agents.host.served", lambda p: dict(roles))
    host._announce("worker/1", "worker", "haiku")
    host._announce("worker/1", "worker", "sonnet")              # another model: nothing new to say
    roles["reviewing"] = "worker"
    host._announce("worker/1", "worker", "sonnet")              # a floor role more: said again
    said = [e for e in host.events.read(0, 100)[0] if e["type"] == "orchestra.machine.started"]
    assert [e["roles"] for e in said] == [["triager"], ["reviewing", "triager"]]
    assert all("harness" not in e and "model" not in e and e["mode"] == "ad hoc" for e in said)


def test_a_heartbeat_says_the_last_line_the_agent_said(monkeypatch):
    monkeypatch.setattr(offer_turn, "SAY_S", 0.01)
    session = {"partial_text": "Reading the brief.\n\nRunning the tests now.\n  \n"}
    host = SimpleNamespace(store=SimpleNamespace(find_session=lambda sid: session))
    said, done = [], threading.Event()
    th = offer_turn.heartbeat(host, "s", done, lambda: False, 0.05, said.append)
    time.sleep(0.15)
    done.set()
    th.join(2)
    assert said and said[0] == "Running the tests now."
