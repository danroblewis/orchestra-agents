"""The project's configuration as the Supervisor changes it: one change at a time, through
Orchestra's own objects (``project.orchestra.config_edit``, Orchestra's SPEC §8.2), never by
opening or editing ``orchestra.toml`` here.

A change goes to one of two places, the Supervisor's choice (``target``):

- ``repo``: the repository's ``orchestra.toml``, which the team shares. The change is committed
  alone to the base branch with the reason, so it is made only when the checkout can take it as a
  commit of its own: the repository root is a git checkout on the base branch, and
  ``orchestra.toml`` has no uncommitted change (someone's edit in progress is never mixed into
  the Supervisor's). When the commit fails the file is put back as it was; for the moment
  between the write and the restore, Orchestra's launcher may act on it.
- ``overlay``: the owner's private overlay, outside the repository (Orchestra's SPEC §8.1): their
  own or their company's settings, as identities, channels and templates. Nothing is committed.

Orchestra keeps the file's comments and layout and refuses a change its readers, or the agents'
``[agents]`` reader (``check``), would say something new against. Nothing that looks like a secret
is ever added (``secret_shapes``), in either file: the change is announced on the bus, and
credentials never go in a configuration file; the Supervisor is told to pass one through an
environment variable or a file the program reads."""

from __future__ import annotations

import subprocess
from collections import Counter
from collections.abc import Callable

from orchestra.config_edit import Change, ConfigEdit
from orchestra.overlay import OVERLAY, REPO

from .config import Config, ConfigError, Project
from .secret_shapes import secret_in
from .store import StoreError

TARGETS = (REPO, OVERLAY)
AUTHOR = ("Orchestra Supervisor", "supervisor@orchestra.invalid")   # only when the checkout names no committer

Edit = Callable[[ConfigEdit, dict], Change]     # Orchestra's editor, its keyword arguments -> the change


def file_name(project: Project) -> str:
    """The repository's configuration file, as git names it."""
    return project.orchestra.config_file.name


def git(project: Project, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(project.repo_root), *args], capture_output=True, text=True, check=False)


def why_not_committable(project: Project) -> str | None:
    """Why a change to orchestra.toml cannot be its own commit on the base branch now, or None."""
    base, name = project.config.base_branch, file_name(project)
    branch = git(project, "rev-parse", "--abbrev-ref", "HEAD")
    if branch.returncode != 0:
        return f"{project.repo_root} is not a git checkout, so a change to {name} cannot be committed"
    if branch.stdout.strip() != base:
        return (f"the repository root has {branch.stdout.strip()!r} checked out, not {base}: a change committed there "
                f"would not be on {base}; the human checks out {base} in the root first")
    if git(project, "status", "--porcelain", "--", name).stdout.strip():
        return (f"{name} has an uncommitted change in the repository root (someone is editing it); the human commits or "
                "drops it first")
    return None


def agents_check(raw: dict) -> list[str]:
    """What the agents' reader says against a merged configuration (Orchestra checks its own)."""
    try:
        Config.from_raw(raw)
    except (ConfigError, ValueError, TypeError) as e:
        return [f"[agents]: {e}"]
    return []


def no_secret(before: str, after: str) -> None:
    """Refuse a change whose new lines hold what looks like a secret."""
    for line in (Counter(after.splitlines()) - Counter(before.splitlines())):
        if what := secret_in(line):
            raise StoreError(f"the change holds what looks like a secret ({what}); configuration is announced on the bus "
                             "and never holds a credential, so pass it through an environment variable or a file the "
                             "program reads")


def commit(project: Project, message: str) -> str:
    """Commit orchestra.toml alone; returns the commit's short id. Raises with git's words."""
    name = file_name(project)
    who = [] if git(project, "config", "user.email").stdout.strip() else ["-c", f"user.name={AUTHOR[0]}", "-c", f"user.email={AUTHOR[1]}"]
    p = subprocess.run(["git", "-C", str(project.repo_root), *who, "commit", "-q", "-m", message, "--", name],
                       capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise StoreError(f"git could not commit {name}: {(p.stderr or p.stdout).strip()}")
    return git(project, "rev-parse", "--short", "HEAD").stdout.strip()


def target_of(a: dict, default: str | None = REPO) -> str:
    t = a.get("target") or default
    if t not in TARGETS:
        raise StoreError("target is repo (orchestra.toml, the project's settings, committed and shared with the team) "
                         "or overlay (the owner's private file, never committed: their identities, channels, templates)")
    return t


def change(project: Project, edit: Edit, describe: Callable[[Change], str], reason: str, target: str) -> dict:
    """Make the change ``edit`` asks Orchestra for, in ``target``; a change to the repository is
    committed with the reason. Returns ``{changed, announce, commit, target, file, written}``
    (``announce``: what the bus is told, without the overlay's values; ``note``: when the overlay
    still wins over a change to the repository)."""
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise StoreError("give the reason for the change in a sentence: it goes in the commit and on the bus")
    if target == REPO and (why := why_not_committable(project)):
        raise StoreError(why)
    ed = project.orchestra.config_edit
    try:
        c = edit(ed, {"target": target, "check": agents_check, "dry_run": True})
    except ConfigError as e:
        raise StoreError(str(e)) from e
    what = describe(c)
    out = {"target": target, "file": c.file, "commit": None, "written": c.changed}
    if not c.changed:
        return {**out, "changed": f"nothing: {what} already", "announce": f"nothing: {c.what} already"}
    no_secret(c.before or "", c.after)
    try:
        ed.apply(c)
    except ConfigError as e:
        raise StoreError(str(e)) from e
    if target == REPO:
        try:
            out["commit"] = commit(project, f"Supervisor: {what}\n\n{reason}\n")
        except StoreError:
            ed.restore(c)
            raise
    project.reload_config()
    out.update(changed=what, announce=what if target == REPO else c.what)
    if c.note:
        out["note"] = c.note
    return out
