"""Roles as data (SPEC.md: the organisation).

A role is a job description: a JSON record (its name, a line on what it is good at, the tool
groups the job needs) plus a prompt file. It has no runtime: no schedule, no wake rules, no
session; those are an agent's (``org.py``). Built-in roles live in ``builtins.py``; custom roles
live in the project repository under ``.orchestra/roles/``, versioned with the code
(``RepoRoleStore``)."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field

from .builtins import ROLES as BUILTIN
from .clock import now
from .config import Project

TOOL_GROUPS = ("tickets", "planning", "handoff", "guides", "context", "pipeline", "capacity", "escalate", "events", "roles", "team", "observe", "model", "experiments",
               "replay", "predict")
# (`setup`, changing orchestra.toml, is the Supervisor's alone: no custom role or team is granted it)
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


class RoleError(ValueError):
    pass


@dataclass
class RoleDef:
    """A custom role. Built-in roles are expressed in these terms too (see `all_roles`)."""

    name: str
    description: str = ""
    tools: list[str] = field(default_factory=lambda: ["tickets", "planning", "escalate"])
    created_by: str | None = "supervisor"      # None for a built-in role: nobody created it
    created_at: str | None = field(default_factory=now)
    updated_at: str | None = field(default_factory=now)
    builtin: bool = False

    def validate(self) -> None:
        if not _NAME.match(self.name):
            raise RoleError("name must be lowercase letters, digits, and underscores, 2 to 41 chars")
        bad = [t for t in self.tools if t not in TOOL_GROUPS]
        if bad:
            raise RoleError(f"unknown tool groups {bad}; known: {list(TOOL_GROUPS)}")

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("builtin", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> RoleDef:
        """A record's known fields; unknown keys are ignored."""
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


class RepoRoleStore:
    """Roles in the target repository: ``.orchestra/roles/<name>.json`` and ``<name>.md``.

    Writes are committed to the base branch when the checkout is on it and otherwise clean.
    Otherwise (another branch, or other changes) the files are written and left uncommitted,
    and the checkout is dirty until a human commits them; `last_commit_note` says which
    happened."""

    def __init__(self, project: Project):
        self.project = project
        self.dir = project.repo_root / ".orchestra" / "roles"
        self.last_commit_note = ""

    def list(self) -> list[RoleDef]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                out.append(RoleDef.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def get(self, name: str) -> RoleDef:
        p = self.dir / f"{name}.json"
        if not p.exists():
            raise RoleError(f"no role {name!r}")
        return RoleDef.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def prompt(self, name: str) -> str:
        p = self.dir / f"{name}.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def save(self, role: RoleDef, prompt: str) -> RoleDef:
        role.validate()
        if role.name in BUILTIN:
            raise RoleError(f"{role.name!r} is a built-in role; choose another name")
        self.dir.mkdir(parents=True, exist_ok=True)
        role.updated_at = now()
        (self.dir / f"{role.name}.json").write_text(json.dumps(role.to_dict(), indent=2) + "\n", encoding="utf-8")
        (self.dir / f"{role.name}.md").write_text(prompt.rstrip() + "\n", encoding="utf-8")
        self._commit(f"agents: role {role.name}")
        return role

    def delete(self, name: str) -> None:
        if name in BUILTIN:
            raise RoleError(f"{name!r} is built in and cannot be deleted")
        found = False
        for suffix in (".json", ".md"):
            p = self.dir / f"{name}{suffix}"
            if p.exists():
                p.unlink()
                found = True
        if not found:
            raise RoleError(f"no role {name!r}")
        self._commit(f"agents: remove role {name}")

    def _commit(self, message: str) -> None:
        self.last_commit_note = commit_orchestra_files(self.project, message)


def commit_orchestra_files(project: Project, message: str) -> str:
    """Commit everything under .orchestra/ to the base branch when the checkout is on it
    and otherwise clean. Otherwise nothing is committed: the files stay written and the
    checkout is dirty until a human commits them. Returns a note saying which happened.
    Shared by the role and org stores."""
    root = project.repo_root
    base = project.config.base_branch
    run = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, check=False)
    branch = run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    dirty = [ln for ln in run("status", "--porcelain", "--untracked-files=all").stdout.splitlines() if ln.strip()
             and not ln[3:].strip().strip('"').startswith(".orchestra/")]
    if branch != base or dirty:
        return (f"files written under .orchestra/ but not committed: checkout is on {branch!r}"
                + (" with other changes" if dirty else "") + f"; commit them on {base} when convenient")
    run("add", "-A", ".orchestra")
    p = run("commit", "-q", "-m", message)
    return "committed to " + base if p.returncode == 0 else "nothing to commit"


def all_roles(project: Project) -> dict[str, RoleDef]:
    """Built-in roles plus the project's custom roles, by name."""
    out: dict[str, RoleDef] = {}
    for b in BUILTIN.values():
        out[b.name] = RoleDef(name=b.name, description=b.description, tools=list(b.tools), created_by=None, created_at=None,
                              updated_at=None, builtin=True)
    for r in RepoRoleStore(project).list():
        out[r.name] = r
    return out
