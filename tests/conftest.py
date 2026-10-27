"""What the tests share: a project with an ``[agents]`` section, a free port for a real Orchestra,
and a harness whose sessions are fakes in this process (for host tests that must not start Pi,
Claude Code or Codex, which may well be installed on the machine running them)."""

from __future__ import annotations

import socket
import threading
import tomllib
from pathlib import Path

from orchestra.config import init_project as init_orchestra

from orchestra_agents.config import Project, load_project
from orchestra_agents.harness.base import AgentSession, Harness, SessionSpec, TurnResult
from orchestra_agents.harness.pi import PiHarness


def agents_project(repo: Path | str, *, harness: str = "pi", model: str = "", provider: str = "") -> Project:
    """Orchestra's starter ``orchestra.toml`` (if there is none) with an ``[agents]`` section
    appended (if it has none), and the project registered with Orchestra."""
    o = init_orchestra(Path(repo))
    toml = o.repo_root / "orchestra.toml"
    text = toml.read_text()
    if "agents" not in tomllib.loads(text):
        extra = f'\n[agents]\nharness = "{harness}"\nserves = ["worker", "reviewer"]\n'
        if model:
            extra += f'model = "{model}"\n'
        if provider:
            extra += f'provider = "{provider}"\n'
        toml.write_text(text.rstrip() + "\n" + extra)
    return load_project(o.repo_root)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeSession(AgentSession):
    """A session whose turn blocks until the session is killed or closed (``prompt`` then returns
    an error, as a harness does when its process is stopped), unless ``reply`` is set, in which
    case the turn ends at once with that text."""

    def __init__(self, spec: SessionSpec, reply: str | None):
        self.spec, self.reply = spec, reply
        self.session_file = None
        self.prompts: list[str] = []          # what each turn was given
        self._stopped = threading.Event()
        self._closed = False

    @property
    def harness_name(self) -> str:
        return "fake"

    def open(self) -> None:
        f = self.spec.session_dir / f"{self.spec.session_id}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
        self.session_file = str(f)

    def prompt(self, text: str, timeout: float | None, on_event=None, idle_timeout: float | None = None) -> TurnResult:
        self.prompts.append(text)
        if self.reply is not None:
            return TurnResult(text=self.reply)
        self._stopped.wait()
        return TurnResult(error="fake exited")

    @property
    def pid(self) -> int | None:
        return None

    def alive(self) -> bool:
        return not self._closed

    def kill(self) -> None:
        self._stopped.set()

    def close(self) -> None:
        self._closed = True
        self._stopped.set()

    def retire(self) -> str | None:
        return archive(self.session_file)


def archive(session_file: str | None) -> str | None:
    """As the Pi harness retires a session: the file moves under archive/ beside its siblings."""
    if not session_file or not Path(session_file).exists():
        return None
    f = Path(session_file)
    dest = f.parent / "archive" / f.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    f.rename(dest)
    return str(dest)


class FakeHarness(Harness):
    name = "fake"

    def __init__(self, reply: str | None = None):
        self.reply = reply
        self.sessions: list[FakeSession] = []

    def start(self, spec: SessionSpec) -> FakeSession:
        s = FakeSession(spec, self.reply)
        self.sessions.append(s)
        return s

    @staticmethod
    def read_transcript(session_file) -> list[dict]:
        return []

    context = staticmethod(PiHarness.context)      # the fakes' transcripts, when a test writes one, are Pi's


class Confirming:
    """The task system's answer to an acceptance, as its scheduler gives it: the first instance to
    accept an offer holds it (``ticket.transitioned`` naming it as the owner); a later one is told
    ``task.released``. The runner starts no session before one of the two."""

    def __init__(self, events):
        self.events = events
        self.holders: dict[str, str] = {}
        self._stop = threading.Event()
        self._offset = events.end()
        self._t = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=5)

    def _run(self):
        while not self._stop.wait(0.05):
            evs, self._offset = self.events.after(self._offset)
            for ev in evs:
                if ev.get("type") != "task.accepted":
                    continue
                aid, tid, who = ev.get("assignment"), ev.get("ticket"), ev.get("source")
                if aid in self.holders:
                    self.events.publish("task.released", {"assignment": aid, "ticket": tid, "role": ev.get("role"), "instance": who,
                                                          "reason": f"already taken by {self.holders[aid]}"},
                                        source="task_scheduler", subject=tid)
                    continue
                self.holders[aid] = who
                to = "IN_PROGRESS" if ev.get("role") == "worker" else "IN_REVIEW"
                self.events.publish("ticket.transitioned", {"ticket": tid, "from": "OPEN", "to": to, "owner": who},
                                    source="task_scheduler", subject=tid)
