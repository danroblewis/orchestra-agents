"""The project's configuration as the Supervisor changes it: one change at a time, through
Orchestra's own objects (``project.orchestra.config_edit``, Orchestra's SPEC §8.2), never by
opening or editing ``orchestra.toml`` here.

A change goes to one of two places, the Supervisor's choice (``target``):

- ``repo``: the repository's ``orchestra.toml``, which the team shares. The change is written in
  place, so Orchestra runs it at once, and committed alone with the reason on the Supervisor's own
  branch ``orchestra/supervisor`` (``side_branch``; never on the base branch), which becomes a pull
  request (``supervisor_pr``). It is made only when the repository root is a git checkout on the
  base branch, and ``orchestra.toml`` has no uncommitted change but the Supervisor's own (the
  branch's): someone's edit in progress is never mixed into the Supervisor's. When the commit fails
  the file is put back as it was; for the moment between the write and the restore, Orchestra's
  launcher may act on it.
- ``overlay``: the owner's private overlay, outside the repository (Orchestra's SPEC §8.1): their
  own or their company's settings, as identities, channels and templates. Nothing is committed.

Orchestra keeps the file's comments and layout and refuses a change its readers, or any plugin's
check (the agents' ``web.check`` among them), would say something new against. Nothing that looks like a secret
is ever added (``secret_shapes``), in either file: the change is announced on the bus, and
credentials never go in a configuration file; the Supervisor is told to pass one through an
environment variable or a file the program reads."""

from __future__ import annotations

import subprocess
from collections import Counter
from collections.abc import Callable

from orchestra.config_edit import Change, ConfigEdit
from orchestra.overlay import OVERLAY, REPO

from . import side_branch, supervisor_pr
from .config import ConfigError, Project
from .secret_shapes import secret_in
from .store import AgentStore, StoreError

TARGETS = (REPO, OVERLAY)
# Orchestra runs every plugin's check, this one's (web.check) included, on every change.

Edit = Callable[[ConfigEdit, dict], Change]     # Orchestra's editor, its keyword arguments -> the change


def file_name(project: Project) -> str:
    """The repository's configuration file, as git names it."""
    return project.orchestra.config_file.name


def git(project: Project, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(project.repo_root), *args], capture_output=True, text=True, check=False)


def why_not_committable(project: Project) -> str | None:
    """Why a change to orchestra.toml cannot be its own commit on the Supervisor's branch now, or None."""
    base, name = project.config.base_branch, file_name(project)
    branch = git(project, "rev-parse", "--abbrev-ref", "HEAD")
    if branch.returncode != 0:
        return f"{project.repo_root} is not a git checkout, so a change to {name} cannot be committed"
    if branch.stdout.strip() != base:
        return (f"the repository root has {branch.stdout.strip()!r} checked out, not {base}: a change committed there "
                f"would not be on {base}; the human checks out {base} in the root first")
    if someone_editing(project):
        return (f"{name} has an uncommitted change in the repository root (someone is editing it); the human commits "
                "or drops it first")
    return None


def someone_editing(project: Project) -> bool:
    """Whether orchestra.toml has an uncommitted change that is not the Supervisor's own (the
    content its branch holds), by the bytes: the Supervisor's own is ``skip-worktree``, which
    ``git status`` does not show. Untracked, as `orchestra init` left it, is nobody's edit."""
    name = file_name(project)
    if git(project, "ls-files", "--error-unmatch", "--", name).returncode != 0:
        return False
    now = project.orchestra.config_edit.text(REPO)              # Orchestra's reader, never the file here
    return now is None or now not in (side_branch.version(project, name, "HEAD"),
                                      side_branch.version(project, name, side_branch.pending(project)))


def no_secret(before: str, after: str) -> None:
    """Refuse a change whose new lines hold what looks like a secret."""
    for line in (Counter(after.splitlines()) - Counter(before.splitlines())):
        if what := secret_in(line):
            raise StoreError(f"the change holds what looks like a secret ({what}); configuration is announced on the bus "
                             "and never holds a credential, so pass it through an environment variable or a file the "
                             "program reads")


def commit(project: Project, message: str) -> str:
    """Commit orchestra.toml alone, as the working tree has it, on the Supervisor's branch; returns
    the commit's short id. Raises with what failed."""
    try:
        sha = side_branch.commit(project, message, paths=[file_name(project)])
    except side_branch.SideBranchError as e:
        raise StoreError(f"could not commit {file_name(project)} on {side_branch.SIDE}: {e}") from None
    if sha is None:
        raise StoreError(f"{side_branch.SIDE} already holds {file_name(project)} as it is")
    return git(project, "rev-parse", "--short", sha).stdout.strip() or sha


def target_of(a: dict, default: str | None = REPO) -> str:
    t = a.get("target") or default
    if t not in TARGETS:
        raise StoreError("target is repo (orchestra.toml, the project's settings, committed and shared with the team) "
                         "or overlay (the owner's private file, never committed: their identities, channels, templates)")
    return t


def change(project: Project, edit: Edit, describe: Callable[[Change], str], reason: str, target: str, *,
           store: AgentStore | None = None, quote: str | None = None) -> dict:
    """Make the change ``edit`` asks Orchestra for, in ``target``; a change to the repository is
    committed with the reason on the Supervisor's branch and proposed as its pull request
    (``supervisor_pr``, with ``store`` to keep what was proposed and ``quote``, the owner's words,
    for the Jira ID). Returns ``{changed, announce, commit, target, file, written}`` and, for the
    repository, ``branch``, ``pull_request`` and ``said`` (``announce``: what the bus is told,
    without the overlay's values; ``note``: when the overlay still wins over a change to the
    repository)."""
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise StoreError("give the reason for the change in a sentence: it goes in the commit and on the bus")
    if target == REPO and (why := why_not_committable(project)):
        raise StoreError(why)
    ed = project.orchestra.config_edit
    try:
        c = edit(ed, {"target": target, "dry_run": True})
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
    if target == REPO:
        out.update(supervisor_pr.carried(project, store, out["commit"], title=f"Supervisor: {what}"[:120], reason=reason,
                                         verified=(f"Orchestra's configuration checks (every reader's and plugin's) passed "
                                                   f"on the change, and it was applied live to {c.file}"), quote=quote))
    out.update(changed=what, announce=what if target == REPO else c.what)
    if c.note:
        out["note"] = c.note
    return out
