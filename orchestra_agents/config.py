"""The agents' settings and where they keep their state.

The settings are ``[agents]`` in the repository's ``orchestra.toml`` (which product runs them,
the model, time limits, which roles they fill, the Coordinator's schedule if any), and three of the task system's ``[project]``
settings their prompts are filled with (the base branch, the ticket prefix, whether work is tasks
or code). The bus and the metrics store are Orchestra's, reached through the Orchestra project
(``Project.orchestra``). State lives in ``~/.orchestrator/<key>/agents/``; the project key is
Orchestra's."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from orchestra.config import Config as OrchestraConfig
from orchestra.config import ConfigError, home, parse_duration
from orchestra.config import Project as OrchestraProject
from orchestra.config import load_project as load_orchestra_project

from .builtins import PER_TICKET_ROLES

__all__ = ["Config", "ConfigError", "Project", "load_project", "parse_duration"]


@dataclass
class Config:
    harness: str = "pi"                 # pi | claude_code | codex
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    serves: list[str] = field(default_factory=lambda: ["worker", "reviewer"])   # the task system's roles they fill
    coordinator_every: str | None = None   # a scheduled check-in for the Coordinator ("10m"); None: woken by events only
    turn_timeout: float = 0.0           # hard cap per turn; 0 = none
    silence_timeout: float = 1200.0     # end a turn after this long with nothing from the harness
    session_max_bytes: int = 300_000    # transcript growth since the last compaction that triggers one
    compact_tokens: int = 100_000
    prompt_overrides: dict[str, str] = field(default_factory=dict)
    control_port: int = 0               # 0: any free port, recorded for the web and `chat`
    base_branch: str = "main"           # from the task system's [project], for the prompts
    ticket_prefix: str = "ORC"
    work: str = "tasks"

    @classmethod
    def from_toml(cls, text: str) -> Config:
        # roles.py reads Project from this module, so the interval grammar is imported here at call time
        from .roles import RoleError, parse_interval

        raw = tomllib.loads(text)
        a, p = raw.get("agents", {}) or {}, raw.get("project", {}) or {}
        serves = list(a.get("serves", ["worker", "reviewer"]))
        bad = [r for r in serves if r not in PER_TICKET_ROLES]
        if bad:
            raise ConfigError(f"[agents] serves names {bad}; the task system's roles the agents can fill are {', '.join(PER_TICKET_ROLES)}")
        every = a.get("coordinator_every") or None
        if every:
            try:
                parse_interval(every)
            except RoleError as e:
                raise ConfigError(f"[agents] coordinator_every: {e}") from e
        return cls(
            harness=(a.get("harness") or "pi").replace("-", "_"), provider=a.get("provider") or None,
            model=a.get("model") or None, thinking=a.get("thinking"),
            serves=serves, coordinator_every=every,
            turn_timeout=parse_duration(a.get("turn_timeout"), 0.0),
            silence_timeout=parse_duration(a.get("silence_timeout"), 1200.0),
            session_max_bytes=int(a.get("session_max_bytes", 300_000)), compact_tokens=int(a.get("compact_tokens", 100_000)),
            prompt_overrides=dict(a.get("prompts", {})), control_port=int(a.get("control_port", 0)),
            base_branch=p.get("base_branch", "main"), ticket_prefix=p.get("ticket_prefix", "ORC"), work=p.get("work", "tasks"))


@dataclass
class Project:
    key: str
    repo_root: Path
    config: Config

    @property
    def orchestra(self) -> OrchestraProject:
        return OrchestraProject(self.key, self.repo_root, OrchestraConfig.from_toml((self.repo_root / "orchestra.toml").read_text()))

    @property
    def state_dir(self) -> Path:
        return home() / self.key / "agents"

    @property
    def runtime_file(self) -> Path:
        return self.state_dir / "runtime.json"

    @property
    def lock_file(self) -> Path:
        return self.state_dir / ".lock"

    @property
    def sessions_dir(self) -> Path:
        return self.state_dir / "sessions"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    def ensure_dirs(self) -> None:
        for d in (self.state_dir, self.sessions_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    def reload_config(self) -> None:
        self.config = Config.from_toml((self.repo_root / "orchestra.toml").read_text())


def load_project(repo: Path | str | None = None) -> Project:
    """The project for ``repo`` (default: ORCHESTRA_REPO, else the cwd), as Orchestra resolves it."""
    o = load_orchestra_project(repo)
    project = Project(key=o.key, repo_root=o.repo_root, config=Config.from_toml((o.repo_root / "orchestra.toml").read_text()))
    project.ensure_dirs()
    return project
