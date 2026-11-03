"""The branch the Supervisor keeps for its own changes to the repository, ``orchestra/supervisor``
(the owner, 2026-11-02, on self-improvement 5: "they probably need a pull request, but we can use it
locally while working").

Every change the Supervisor makes to the repository is one commit here, never on the base branch:
``orchestra.toml`` (``config_change``), its machine spec and duties (``spec_git``), an override
(``patching.write_override``) and a graduation (``machine_graduate``). The commit is made with git's
plumbing on a private index, from the branch's tip while it holds commits the checkout does not
(``pending``), else from the base branch: the owner's checkout is never switched.

**Used live at once.** What the host and Orchestra read from the working tree (``orchestra.toml``,
``.orchestra/overrides/``, a graduated program) is written there, as before, and the branch carries
the same bytes (``paths``). The machine specs are read from this branch while it is pending
(``spec_git.read_files``), so they are given as text (``texts``) and the working tree is not touched.
The pull request is ``supervisor_pr``'s.

**The checkout stays clean for the merge gate** (``tasking``'s land fast-forwards the root only when
``git status`` shows no change to a tracked file): a tracked file the change modifies is marked
``skip-worktree`` (the index keeps HEAD's bytes, the file the change's), and a new file is left
untracked; only a graduation's move is staged (``git mv``). The price is at the pull: git refuses to
overwrite a flagged or untracked file even with the same bytes, so ``settle`` readies the checkout
once the branch's commit is in ``origin/<base>`` (the flag cleared and the change staged, which a
fast-forward takes), and clears the flags once it is in the checkout. Someone's edit to a flagged
file does not show in ``git status``; ``config_change.someone_editing`` compares the bytes instead."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .config import Project

SIDE = "orchestra/supervisor"
IDENT = {"GIT_AUTHOR_NAME": "Orchestra Supervisor", "GIT_AUTHOR_EMAIL": "supervisor@orchestra.invalid",
         "GIT_COMMITTER_NAME": "Orchestra Supervisor", "GIT_COMMITTER_EMAIL": "supervisor@orchestra.invalid"}


class SideBranchError(ValueError):
    pass


def git(root: Path, *args: str, env: dict | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False,
                          env={**os.environ, **(env or {})}, input=stdin)


def ident(root: Path) -> dict:
    """A committer for a repository with none configured: the commit is the Supervisor's, so it is named so."""
    return {} if git(root, "config", "user.email").stdout.strip() else dict(IDENT)


def ref(root: Path, name: str) -> str | None:
    return git(root, "rev-parse", "--verify", "-q", f"{name}^{{commit}}").stdout.strip() or None


def pending(project: Project) -> str | None:
    """The branch's tip when it holds commits the checkout's HEAD does not have; else None."""
    root = project.repo_root
    tip = ref(root, f"refs/heads/{SIDE}")
    if not tip:
        return None
    head = ref(root, "HEAD")
    if head and git(root, "merge-base", "--is-ancestor", tip, head).returncode == 0:
        return None
    return tip


def parent(project: Project) -> str | None:
    """What the next commit builds on: the branch's pending tip, else the base branch, else HEAD."""
    root = project.repo_root
    return pending(project) or ref(root, f"refs/heads/{project.config.base_branch}") or ref(root, "HEAD")


def version(project: Project, path: str, rev: str | None) -> str | None:
    """``path``'s text at ``rev``; None when it is not there."""
    if not rev:
        return None
    p = git(project.repo_root, "show", f"{rev}:{path}")
    return p.stdout if p.returncode == 0 else None


def _tree_files(root: Path, path: str) -> list[str]:
    """The files the working tree has at ``path`` (a file, or a directory: its tracked and
    untracked files, .gitignore respected)."""
    if (root / path).is_file():
        return [path]
    out = git(root, "ls-files", "-co", "--exclude-standard", "--", path).stdout.splitlines()
    return sorted({f for f in out if f.strip() and (root / f).is_file()})


def commit(project: Project, message: str, *, texts: dict[str, str | None] | None = None,
           paths: list[str] | tuple[str, ...] = (), stage: bool = True) -> str | None:
    """One commit on the branch: ``texts`` (path -> text, or None to delete) and the working tree's
    ``paths`` as they are now (a path gone from the tree is deleted on the branch). Returns the
    commit, or None when the branch already holds exactly that. ``stage``: the ``paths`` staged in
    the checkout too (the module's docstring)."""
    root = project.repo_root
    base = parent(project)
    if not base:
        raise SideBranchError("refused: the repository has no commit to build on")
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if git(root, "read-tree", base, env=env).returncode != 0:
            raise SideBranchError("could not read the base tree")
        for path in paths:
            have = _tree_files(root, path)
            for gone in set(git(root, "ls-tree", "-r", "--name-only", base, "--", path).stdout.splitlines()) - set(have):
                git(root, "update-index", "--force-remove", "--", gone, env=env)
            for f in have:
                blob = git(root, "hash-object", "-w", "--", f).stdout.strip()
                mode = "100755" if os.access(root / f, os.X_OK) else "100644"
                if not blob or git(root, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{f}", env=env).returncode:
                    raise SideBranchError(f"could not add {f} to the branch's commit")
        for path, text in (texts or {}).items():
            if text is None:
                git(root, "update-index", "--force-remove", "--", path, env=env)
                continue
            blob = git(root, "hash-object", "-w", "--stdin", stdin=text).stdout.strip()
            git(root, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        tree = git(root, "write-tree", env=env).stdout.strip()
    if tree == git(root, "rev-parse", f"{base}^{{tree}}").stdout.strip():
        return None
    sha = git(root, "commit-tree", tree, "-p", base, stdin=message, env=ident(root)).stdout.strip()
    old = ref(root, f"refs/heads/{SIDE}") or ""
    if not sha or git(root, "update-ref", f"refs/heads/{SIDE}", sha, old).returncode != 0:
        raise SideBranchError(f"the commit on {SIDE} failed")
    if stage:
        hide(project, paths)
    return sha


def hide(project: Project, paths) -> None:
    """Each tracked file of ``paths`` the working tree changes, marked ``skip-worktree``: the
    checkout reads clean to the merge gate (the module's docstring)."""
    root = project.repo_root
    for path in paths:
        for f in _tree_files(root, path):
            tracked = git(root, "ls-files", "--error-unmatch", "--", f).returncode == 0
            if tracked and git(root, "diff", "--quiet", "--", f).returncode != 0:
                git(root, "update-index", "--skip-worktree", "--", f)


def flagged(project: Project) -> list[str]:
    return [ln[2:] for ln in git(project.repo_root, "ls-files", "-v").stdout.splitlines() if ln.startswith("S ")]


def settle(project: Project) -> str | None:
    """The checkout readied for the merged pull request: once the branch's tip is in the checkout,
    the flags cleared; once it is only in ``origin/<base>`` (fetched), each file the branch changes
    that the working tree already holds as the branch has it is unflagged and staged, so the
    owner's pull fast-forwards. Returns what was done, or None."""
    root = project.repo_root
    tip = ref(root, f"refs/heads/{SIDE}")
    marks = flagged(project)
    if not tip:
        return None
    if not pending(project):
        for f in marks:
            git(root, "update-index", "--no-skip-worktree", "--", f)
        return "cleared" if marks else None
    up = ref(root, f"refs/remotes/origin/{project.config.base_branch}")
    if not up or git(root, "merge-base", "--is-ancestor", tip, up).returncode != 0:
        return None
    ready = []
    for f in git(root, "diff", "--name-only", "--no-renames", "HEAD", tip).stdout.splitlines():
        want, there = version(project, f, tip), root / f
        have = there.read_bytes().decode("utf-8", "replace") if there.is_file() else None
        if have == want:
            git(root, "update-index", "--no-skip-worktree", "--", f)
            git(root, "add", "-A", "--", f)
            ready.append(f)
    return f"staged for the pull: {', '.join(ready)}" if ready else None
