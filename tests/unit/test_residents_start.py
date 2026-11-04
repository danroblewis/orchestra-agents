"""A resident session is opened only under the agent's turn lock (residents.Residents.start): the
Supervisor's chat, arriving while the host starts, opens the session itself; the host's start must
neither open a second one of that id (whose token would replace the chat's) nor replace it."""

import threading
from types import SimpleNamespace

from orchestra_agents.residents import Residents


class FakeSession:
    session_file, harness_name = None, "dummy"

    def open(self):
        pass


class FakeHost:
    def __init__(self):
        self.locks: dict[str, threading.Lock] = {}
        self.opened: list[str] = []
        self.harness = SimpleNamespace(name="dummy")
        self.store = SimpleNamespace(set_session=lambda *a, **k: None)
        self.log = lambda _m: None

    def _turn_lock(self, name):
        return self.locks.setdefault(name, threading.Lock())

    def _session_for(self, agent, sid):
        self.opened.append(sid)
        return FakeSession()

    def _pid(self, _proc):
        return None


AGENT = SimpleNamespace(name="supervisor", session_id="supervisor")


def test_a_running_turn_is_never_raced_and_the_next_tick_starts_it():
    host = FakeHost()
    r = Residents(host)
    with host._turn_lock("supervisor"):          # the chat's turn, opening its own session
        r.start(AGENT)
    assert host.opened == [] and "supervisor" not in r.procs
    r.start(AGENT)
    assert host.opened == ["supervisor"] and "supervisor" in r.procs


def test_a_session_the_chat_opened_is_kept():
    host = FakeHost()
    r = Residents(host)
    chat = FakeSession()
    r.procs["supervisor"] = chat
    r.start(AGENT)
    assert host.opened == [] and r.procs["supervisor"] is chat


def test_a_restart_waits_for_the_turn_that_holds_the_lock():
    host = FakeHost()
    host.agent_threads = {}
    r = Residents(host)
    chat = FakeSession()
    chat.close = lambda: None
    r.procs["supervisor"] = chat
    with host._turn_lock("supervisor"):          # a chat has the session, not yet in agent_threads
        r.stop_changed({"supervisor": AGENT}, {"supervisor": {"restart_requested": True}})
    assert r.procs["supervisor"] is chat
