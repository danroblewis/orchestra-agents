"""The agents' settings and where they keep their state.

The settings are ``[agents]`` in the project's configuration (which product runs them, the model,
time limits, which roles they fill, the Coordinator's schedule if any), and three of the task
system's ``[project]`` settings their prompts are filled with (the base branch, the ticket prefix,
whether work is tasks or code). They are read only through Orchestra's objects: the Orchestra
project (``Project.orchestra``) holds the configuration, ``orchestra.toml`` with the owner's
private overlay merged over it, and reloads it; nothing here opens ``orchestra.toml`` (Orchestra's
AGENTS.md rule 10). The bus and the metrics store are Orchestra's too. State lives in
``~/.orchestrator/<key>/agents/``; the project key is Orchestra's."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestra.config import ConfigError, home, parse_duration
from orchestra.config import Project as OrchestraProject
from orchestra.config import load_project as load_orchestra_project

from .builtins import PER_TICKET_ROLES
from .clock import parse_interval

__all__ = ["Config", "ConfigError", "Project", "load_project"]


@dataclass
class Config:
    harness: str = "pi"                 # pi | claude_code | codex | dummy
    dummy_script: str | None = None     # the dummy harness's script, relative to the repository (harness/dummy_script.py)
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    serves: list[str] = field(default_factory=lambda: ["worker", "reviewer"])   # the task system's roles they fill
    coordinator_every: str | None = None   # a scheduled check-in for the Coordinator ("10m"); None: woken by events only
    turn_timeout: float = 0.0           # hard cap per turn; 0 = none
    silence_timeout: float = 1200.0     # end a turn after this long with nothing from the harness
    compact_tokens: int = 100_000       # a context past this many tokens is compacted (residents.trim_if_large)
    prompt_overrides: dict[str, str] = field(default_factory=dict)
    control_port: int = 0               # 0: any free port, recorded for the web and `chat`
    base_branch: str = "main"           # from the task system's [project], for the prompts
    ticket_prefix: str = "ORC"
    work: str = "tasks"

    @classmethod
    def from_toml(cls, text: str) -> Config:
        """The settings in a TOML text (tests; the project's come from ``from_raw``)."""
        return cls.from_raw(tomllib.loads(text))

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> Config:
        """The settings in a parsed configuration: Orchestra's ``project.config.raw``."""
        a, p = raw.get("agents", {}) or {}, raw.get("project", {}) or {}
        serves = list(a.get("serves", ["worker", "reviewer"]))
        bad = [r for r in serves if r not in PER_TICKET_ROLES]
        if bad:
            raise ConfigError(f"[agents] serves names {bad}; the task system's roles the agents can fill are {', '.join(PER_TICKET_ROLES)}")
        every = a.get("coordinator_every") or None
        if every:
            try:
                parse_interval(every)
            except ConfigError as e:
                raise ConfigError(f"[agents] coordinator_every: {e}") from e
        return cls(
            harness=(a.get("harness") or "pi").replace("-", "_"), dummy_script=a.get("dummy_script") or None, provider=a.get("provider") or None,
            model=a.get("model") or None, thinking=a.get("thinking"),
            serves=serves, coordinator_every=every,
            turn_timeout=parse_duration(a.get("turn_timeout"), 0.0),
            silence_timeout=parse_duration(a.get("silence_timeout"), 1200.0),
            compact_tokens=int(a.get("compact_tokens", 100_000)),
            prompt_overrides=dict(a.get("prompts", {})), control_port=int(a.get("control_port", 0)),
            base_branch=p.get("base_branch", "main"), ticket_prefix=p.get("ticket_prefix", "ORC"), work=p.get("work", "tasks"))


@dataclass
class Project:
    orchestra: OrchestraProject    # the same repository as Orchestra sees it: its configuration, key, bus and store
    config: Config
    stamp: tuple | None = field(default=None, repr=False, compare=False)   # the files as last read (config_stamp)

    @classmethod
    def of(cls, o: OrchestraProject) -> Project:
        """The agents' view of an Orchestra project (the one a plugin is given, or ``load_project``'s)."""
        return cls(orchestra=o, config=Config.from_raw(o.config.raw))

    @property
    def key(self) -> str:
        return self.orchestra.key

    @property
    def repo_root(self) -> Path:
        return self.orchestra.repo_root

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
        """The configuration as it is now, through Orchestra (the overlay merged): read again only
        when either file changed (``config_stamp``), so the host can ask every second."""
        stamp = self.orchestra.config_stamp()
        if stamp != self.stamp:
            self.orchestra.reload_config()
            self.stamp = stamp
        self.config = Config.from_raw(self.orchestra.config.raw)


def load_project(repo: Path | str | None = None) -> Project:
    """The project for ``repo`` (default: ORCHESTRA_REPO, else the cwd), as Orchestra resolves it."""
    project = Project.of(load_orchestra_project(repo))
    project.ensure_dirs()
    return project
