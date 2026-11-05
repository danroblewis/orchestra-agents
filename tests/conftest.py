"""What the tests share: a project with an ``[agents]`` section, a free port for a real Orchestra,
and a harness whose sessions are fakes in this process (for host tests that must not start Pi,
Claude Code or Codex, which may well be installed on the machine running them)."""

from __future__ import annotations

import socket
import threading
import tomllib
from pathlib import Path

import pytest
from orchestra.config import init_project as init_orchestra

from orchestra_agents.config import Project, load_project
from orchestra_agents.harness.base import AgentSession, Harness, SessionSpec, TurnResult
from orchestra_agents.harness.pi import PiHarness

pytest.register_assert_rewrite("design_fixtures")          # the design tools' fixtures, a plugin of their tests


def agents_project(repo: Path | str, *, harness: str = "dummy", model: str = "", provider: str = "",
                   set_up: bool = True) -> Project:
    """Orchestra's starter ``orchestra.toml`` (if there is none) with an ``[agents]`` section
    appended (if it has none), and the project registered with Orchestra. ``harness``: the dummy by
    default, so a host built on it opens no Pi, Claude Code or Codex session (a test of one names
    it and stands its fake in). ``set_up``: the
    Supervisor's setup is recorded as having run, so a host started on it wakes nobody for a first
    run (first_run.py); the tests of the first run pass False."""
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
    project = load_project(o.repo_root)
    if set_up:
        from orchestra_agents.store import AgentStore
        AgentStore(project).set_agent_state("supervisor", setup={"ran_at": 1.0, "harness": project.config.harness})
    return project


def ask_always(project) -> None:
    """``[agents] ask = "always"``: the Supervisor asks the owner first, as before 2026-11-04 (ask.py),
    for a test of the old refusals."""
    toml = project.repo_root / "orchestra.toml"
    text = toml.read_text()
    if 'ask = "always"' not in text:
        toml.write_text(text.replace("[agents]\n", '[agents]\nask = "always"\n', 1))
    project.reload_config()


REAL_HARNESSES = {"pi", "claude_code", "codex"}


@pytest.fixture(autouse=True)
def _no_real_harness_in_unit_tests(request, monkeypatch):
    """A unit test never gets a real product's harness from ``get_harness`` (a host built on one
    opens the Supervisor's and the Coordinator's sessions on the real binary, left running until
    pytest exits): it fails instead. A test that means to read a real harness's own code imports
    its class, or ``get_harness`` itself, directly; one that reads a real harness's model or price
    and opens no session is marked ``real_harness``."""
    if "tests/unit/" not in str(request.node.path).replace("\\", "/") or request.node.get_closest_marker("real_harness"):
        return
    import orchestra_agents.harness as harness_pkg
    import orchestra_agents.host as host_mod
    real = harness_pkg.get_harness

    def guarded(name):
        h = real(name)
        if h.name in REAL_HARNESSES:
            pytest.fail(f"a unit test got the real {h.name} harness from get_harness; use the dummy "
                        "harness (agents_project's default) or conftest's FakeHarness")
        return h

    monkeypatch.setattr(harness_pkg, "get_harness", guarded)
    monkeypatch.setattr(host_mod, "get_harness", guarded)


def supervisor_knows(project, agent=None) -> str:
    """What the Supervisor is told, on demand included: its system prompt, then every skill it can
    read (``read_skill``), as it reads them (placeholders filled, task-system words gated)."""
    from orchestra_agents import prompting, skills
    from orchestra_agents.org import built_in_agents
    agent = agent or built_in_agents()["supervisor"]
    return prompting.agent_prompt(project, agent) + "\n\n" + "\n\n".join(skills.text(project, n) for n in skills.names())


def skill_files() -> str:
    """Every skill's file as it ships, one after another."""
    from orchestra_agents.skills import SKILLS_DIR
    return "\n\n".join(p.read_text(encoding="utf-8") for p in sorted(SKILLS_DIR.glob("*.md")))


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

