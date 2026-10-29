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

import os
import shutil
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


TOOLS_VIA = ("mcp", "bash", "auto")   # harness/bash_tools.py MODES
DEFAULT_MODEL = "haiku"     # the model chosen when no harness is set and Claude Code is found: the cheapest that does the work


def claude_found() -> bool:
    """Whether Claude Code can be run here: ``CLAUDE_BINARY`` (a path, or a name on the PATH), else
    ``claude`` on the PATH."""
    b = os.environ.get("CLAUDE_BINARY") or "claude"
    return os.access(b, os.X_OK) if os.sep in b else shutil.which(b) is not None


def default_harness() -> tuple[str, str | None, str]:
    """The harness, model and a note for the human, when ``[agents]`` names no harness: Claude Code
    with Haiku when ``claude`` is found, else the dummy harness (scripted, no model, no cost)."""
    how = ("Tell the Supervisor which harness and model to use (it sets [agents] harness and model), or set them "
           "in orchestra.toml yourself.")
    if claude_found():
        return "claude_code", DEFAULT_MODEL, (
            "No harness is set in [agents], so the agents run on Claude Code with Haiku (the claude command was found). "
            + how)
    return "dummy", None, (
        "No harness is set in [agents] and no claude command is on the PATH, so the agents run on the dummy harness: "
        "scripted, with no model and no cost, so they cannot read or set up your repository. Install Claude Code and log "
        "in once (run claude), then restart orchestra run; or set [agents] harness to pi, codex or claude_code. " + how)


@dataclass
class Config:
    harness: str = "pi"                 # pi | claude_code | codex | dummy (from_raw: default_harness when unset)
    harness_note: str | None = None     # when the harness was not set but chosen (default_harness): what to tell the human
    enabled: bool = True                # [agents] enabled = false: the plugin's pages, and no host
    dummy_script: str | None = None     # the dummy harness's script, relative to the repository (harness/dummy_script.py)
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    tools_via: str = "auto"             # how sessions call Orchestra's tools: mcp | bash | auto (harness/bash_tools.py)
    serves: list[str] = field(default_factory=lambda: ["worker", "reviewer"])   # the task system's roles they fill
    coordinator_every: str | None = None   # a scheduled check-in for the Coordinator ("10m"); None: woken by events only
    turn_timeout: float = 0.0           # hard cap per turn; 0 = none
    silence_timeout: float = 1200.0     # end a turn after this long with nothing from the harness
    compact_tokens: int = 100_000       # a context past this many tokens is compacted (residents.trim_if_large)
    prompt_overrides: dict[str, str] = field(default_factory=dict)
    control_port: int = 0               # 0: any free port, recorded for the web and `chat`
    spec_changes_per_day: int = 5       # the Supervisor's changes to its own spec in 24 hours (duties.py), the owner's cap
    review_usd_per_day: float = 2.0     # what the Supervisor's review rounds and flow wake-ups may spend a day (budget.py)
    review_turns_per_day: int = 48      # ... and how many turns, for a harness that reports no cost
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
        via = a.get("tools_via", "auto")
        if via not in TOOLS_VIA:
            raise ConfigError(f"[agents] tools_via is {via!r}; it is one of {', '.join(TOOLS_VIA)}: mcp, Orchestra's MCP server; "
                              "bash, a command the session runs from its shell; auto, the MCP server until it is found blocked")
        harness, model, note = (a["harness"].replace("-", "_"), a.get("model") or None, None) if a.get("harness") else default_harness()
        if a.get("harness") is None and a.get("model"):
            model = a["model"]
        return cls(
            harness=harness, harness_note=note, enabled=a.get("enabled", True) is not False,
            dummy_script=a.get("dummy_script") or None, provider=a.get("provider") or None,
            model=model, thinking=a.get("thinking"), tools_via=via,
            serves=serves, coordinator_every=every,
            turn_timeout=parse_duration(a.get("turn_timeout"), 0.0),
            silence_timeout=parse_duration(a.get("silence_timeout"), 1200.0),
            compact_tokens=int(a.get("compact_tokens", 100_000)),
            prompt_overrides=dict(a.get("prompts", {})), control_port=int(a.get("control_port", 0)),
            spec_changes_per_day=_count(a, "spec_changes_per_day", 5), review_usd_per_day=_amount(a, "review_usd_per_day", 2.0),
            review_turns_per_day=_count(a, "review_turns_per_day", 48),
            base_branch=p.get("base_branch", "main"), ticket_prefix=p.get("ticket_prefix", "ORC"), work=p.get("work", "tasks"))


def _count(a: dict, key: str, default: int) -> int:
    v = a.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ConfigError(f"[agents] {key} must be a whole number, 0 or more (got {v!r})")
    return v


def _amount(a: dict, key: str, default: float) -> float:
    v = a.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int | float) or v < 0:
        raise ConfigError(f"[agents] {key} must be a number of dollars, 0 or more (got {v!r})")
    return float(v)


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
