"""The branch the Supervisor keeps for its own changes to the repository, ``orchestra/supervisor``
(the owner, 2026-11-02, on self-improvement 5: "they probably need a pull request, but we can use it
locally while working").

Every change the Supervisor makes to the repository is one commit here, never on the base branch:
``orchestra.toml`` (``config_change``), its machine spec and duties (``spec_git``), an override
(``patching.write_override``) and a graduation (``machine_graduate``). The commit is made with git's
plumbing on a private index, from the branch's tip while it holds commits the base does not
(``pending``), else from the base branch's head. **The owner's checkout is never touched**: not
switched, not written, not staged, so ``git status`` is what the owner left, the merge gate lands
tickets as before, and pulling the merged pull request goes through.

"Used live" is the private layer, "shared" is the pull request (the coordinator, 2026-11-02): a
configuration change is live in the owner's overlay (``config_change``) and a spec change in the
state directory (``spec_git``) until the pull request is merged and the checkout has it
(``live_layers.settle`` retires them); an override and a graduation exist only on the branch until
then. The pull request is ``supervisor_pr``'s."""

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
    """The branch's tip while it holds commits the base does not have (the checkout's HEAD, the base
    branch, or ``origin/<base>`` as last fetched); else None."""
    root = project.repo_root
    tip = ref(root, f"refs/heads/{SIDE}")
    if not tip:
        return None
    base = project.config.base_branch
    for there in ("HEAD", f"refs/heads/{base}", f"refs/remotes/origin/{base}"):
        at = ref(root, there)
        if at and git(root, "merge-base", "--is-ancestor", tip, at).returncode == 0:
            return None
    return tip


def parent(project: Project) -> str | None:
    """What the next commit builds on: the branch's pending tip, else the base's head (``origin/<base>``
    when it is ahead of the local base branch), else HEAD."""
    root, base = project.repo_root, project.config.base_branch
    if tip := pending(project):
        return tip
    local, up = ref(root, f"refs/heads/{base}"), ref(root, f"refs/remotes/origin/{base}")
    if up and (not local or git(root, "merge-base", "--is-ancestor", local, up).returncode == 0):
        return up
    return local or ref(root, "HEAD")


def version(project: Project, path: str, rev: str | None) -> str | None:
    """``path``'s text at ``rev``; None when it is not there."""
    if not rev:
        return None
    p = git(project.repo_root, "show", f"{rev}:{path}")
    return p.stdout if p.returncode == 0 else None


def commit(project: Project, message: str, *, texts: dict[str, str | None] | None = None,
           files: dict[str, Path] | None = None) -> str | None:
    """One commit on the branch: ``texts`` (path -> text, or None to delete) and ``files`` (path ->
    a file on disk whose bytes go there, binary or not). Returns the commit, or None when the branch
    already holds exactly that. The checkout is not touched."""
    root = project.repo_root
    base = parent(project)
    if not base:
        raise SideBranchError("refused: the repository has no commit to build on")
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if git(root, "read-tree", base, env=env).returncode != 0:
            raise SideBranchError("could not read the base tree")
        for path, src in (files or {}).items():
            blob = git(root, "hash-object", "-w", "--", str(src)).stdout.strip()
            mode = "100755" if os.access(src, os.X_OK) else "100644"
            if not blob or git(root, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}", env=env).returncode:
                raise SideBranchError(f"could not add {path} to the branch's commit")
        for path, text in (texts or {}).items():
            if text is None:
                git(root, "update-index", "--force-remove", "--", path, env=env)
                continue
            blob = git(root, "hash-object", "-w", "--stdin", stdin=text).stdout.strip()
            if not blob or git(root, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env).returncode:
                raise SideBranchError(f"could not add {path} to the branch's commit")
        tree = git(root, "write-tree", env=env).stdout.strip()
    if tree == git(root, "rev-parse", f"{base}^{{tree}}").stdout.strip():
        return None
    sha = git(root, "commit-tree", tree, "-p", base, stdin=message, env=ident(root)).stdout.strip()
    old = ref(root, f"refs/heads/{SIDE}") or ""
    if not sha or git(root, "update-ref", f"refs/heads/{SIDE}", sha, old).returncode != 0:
        raise SideBranchError(f"the commit on {SIDE} failed")
    return sha


def tree_files(project: Project, rev: str | None, prefix: str) -> list[str]:
    """The files under ``prefix`` at ``rev``."""
    if not rev:
        return []
    return [f for f in git(project.repo_root, "ls-tree", "-r", "--name-only", rev, "--", prefix).stdout.splitlines() if f]
