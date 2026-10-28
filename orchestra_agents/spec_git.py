"""Where the machine specs live in git, and how a change to one is committed (the owner,
2026-10-28: every change to a machine's spec is a commit with a reason, on a side branch when the
checkout is dirty, and can be reverted).

The specs are the files under ``.orchestra/machines/`` in the project's repository
(``machine_spec.py`` reads them). A change is written in one of two places:

- **the base branch**, when the checkout is on it and has no uncommitted change outside
  ``.orchestra/machines/``: the files are written in the working tree and committed alone
  (``git commit -- .orchestra/machines``), so nothing else of the owner's is swept in. A side
  branch left from earlier is merged first (its commits touch only the specs);
- **the side branch** ``orchestra/supervisor`` otherwise (another branch checked out, or other
  changes in the checkout): the commit is made with git's plumbing on a private index, from the
  side branch's tip (or the base branch's), and the working tree and the owner's index are not
  touched. Until it is merged into the base branch, the specs are read from the side branch
  (``read_files``), so the host runs what was committed, never a dirty checkout.

A checkout that is not a git repository has no history to revert to: changes are refused there
(``SpecGitError``), except the first spec written by the plugin, which is only written.

This module and ``roles.commit_orchestra_files`` are the agents' git writes to the project; the
other is the scratch worktree a code patch is written in (``patching.py``)."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .config import Project

ROOT = ".orchestra/machines"
SIDE = "orchestra/supervisor"


class SpecGitError(ValueError):
    pass


def _git(root: Path, *args: str, env: dict | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False,
                          env={**os.environ, **(env or {})}, input=stdin)


def _ident(root: Path) -> dict:
    """A committer for a repository with none configured (a fresh test repository, a CI box): the
    commit is the Supervisor's, so it is named so."""
    if _git(root, "config", "user.email").stdout.strip():
        return {}
    return {"GIT_AUTHOR_NAME": "Orchestra Supervisor", "GIT_AUTHOR_EMAIL": "supervisor@orchestra.invalid",
            "GIT_COMMITTER_NAME": "Orchestra Supervisor", "GIT_COMMITTER_EMAIL": "supervisor@orchestra.invalid"}


def is_git(project: Project) -> bool:
    return _git(project.repo_root, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def _ref(root: Path, ref: str) -> str | None:
    p = _git(root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
    return p.stdout.strip() or None


def side_pending(project: Project) -> str | None:
    """The side branch's tip when it holds spec commits the checkout's HEAD does not have; else None."""
    root = project.repo_root
    tip = _ref(root, f"refs/heads/{SIDE}")
    if not tip:
        return None
    head = _ref(root, "HEAD")
    if head and _git(root, "merge-base", "--is-ancestor", tip, head).returncode == 0:
        return None
    return tip


def _may_have_side(root: Path) -> bool:
    """Whether the side branch may exist, without starting git when it plainly does not: the
    agents read the specs on every tool call and every tick."""
    gitdir = root / ".git"
    if not gitdir.exists():
        return False
    if not gitdir.is_dir():
        return True                          # a worktree's .git file: ask git
    packed = gitdir / "packed-refs"
    try:
        in_packed = packed.exists() and f"refs/heads/{SIDE}" in packed.read_text(encoding="utf-8")
    except OSError:
        in_packed = True
    return in_packed or (gitdir / "refs" / "heads" / SIDE).exists()


def read_files(project: Project) -> dict[str, str]:
    """Every file under ``.orchestra/machines/`` as the host should run it: from the side branch
    while it holds commits the checkout does not, else from the working tree. Paths are relative
    to the repository."""
    root = project.repo_root
    tip = side_pending(project) if _may_have_side(root) else None
    if tip:
        names = _git(root, "ls-tree", "-r", "--name-only", tip, "--", ROOT).stdout.splitlines()
        return {n: _git(root, "show", f"{tip}:{n}").stdout for n in names}
    base = root / ROOT
    if not base.is_dir():
        return {}
    out = {}
    for p in sorted(base.rglob("*")):
        if p.is_file():
            try:
                out[str(p.relative_to(root))] = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    return out


def _clean_on_base(project: Project) -> tuple[bool, str]:
    root, base = project.repo_root, project.config.base_branch
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    dirty = [ln for ln in _git(root, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
             if ln.strip() and not ln[3:].strip().strip('"').startswith(ROOT + "/")]
    if branch != base:
        return False, f"the checkout is on {branch!r}, not {base!r}"
    if dirty:
        return False, f"the checkout has {len(dirty)} uncommitted change(s) outside {ROOT}/"
    return True, ""


def _merge_side(project: Project) -> str | None:
    """On a clean base checkout: the side branch's pending commits merged in. Returns why not, or None."""
    tip = side_pending(project)
    if not tip:
        return None
    p = _git(project.repo_root, "merge", "--no-ff", "--no-edit", "-q", "-m", f"Merge {SIDE}: the machine specs committed "
             "while the checkout was busy", tip, env=_ident(project.repo_root))
    if p.returncode != 0:
        _git(project.repo_root, "merge", "--abort")
        return f"could not merge {SIDE} ({(p.stderr or p.stdout).strip()[:200]}); it is left for the owner"
    return None


def commit(project: Project, changes: dict[str, str | None], message: str, *, allow_plain: bool = False,
           not_base: str | None = None) -> dict:
    """Write ``changes`` (path under ``.orchestra/machines/`` -> text, or None to delete) as one
    commit with ``message``. Returns ``{commit, where, note}``: ``where`` is ``base``, ``side``
    or ``files`` (written without git, only with ``allow_plain``). ``not_base``: why the base
    branch must not move now (the merge gate is landing), which sends the commit to the side branch."""
    for path in changes:
        if not path.startswith(ROOT + "/") or ".." in Path(path).parts:
            raise SpecGitError(f"refused: {path} is not under {ROOT}/")
    root = project.repo_root
    if not is_git(project):
        if not allow_plain:
            raise SpecGitError("refused: the repository is not a git checkout, so the change could not be committed or reverted")
        _write_tree(root, changes)
        return {"commit": None, "where": "files", "note": "written; the repository is not a git checkout, so nothing was committed"}
    ok, why = _clean_on_base(project)
    if ok and not_base:
        ok, why = False, not_base
    if ok:
        merged = _merge_side(project)
        if merged is None:
            _write_tree(root, changes)
            _git(root, "add", "-A", "--", ROOT)
            p = _git(root, "commit", "-q", "-m", message, "--", ROOT, env=_ident(root))
            if p.returncode != 0 and "nothing" in (p.stdout + p.stderr):
                return {"commit": None, "where": "base", "note": "nothing changed"}
            if p.returncode != 0:
                raise SpecGitError(f"the commit failed: {(p.stderr or p.stdout).strip()[:300]}")
            sha = _ref(root, "HEAD")
            return {"commit": sha, "where": "base", "note": f"committed to {project.config.base_branch}"}
        why = merged
    sha = _side_commit(project, changes, message)
    return {"commit": sha, "where": "side", "note": (f"committed on the side branch {SIDE} ({why}); the agents run the specs "
                                                    f"from it until it is merged, which the next change on a clean "
                                                    f"{project.config.base_branch} checkout does")}


def _write_tree(root: Path, changes: dict[str, str | None]) -> None:
    for path, text in changes.items():
        p = root / path
        if text is None:
            if p.exists():
                p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")


def _side_commit(project: Project, changes: dict[str, str | None], message: str) -> str:
    root = project.repo_root
    parent = _ref(root, f"refs/heads/{SIDE}") if side_pending(project) else None
    parent = parent or _ref(root, f"refs/heads/{project.config.base_branch}") or _ref(root, "HEAD")
    if not parent:
        raise SpecGitError("refused: the repository has no commit to build on")
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if _git(root, "read-tree", parent, env=env).returncode != 0:
            raise SpecGitError("could not read the base tree")
        for path, text in changes.items():
            if text is None:
                _git(root, "update-index", "--force-remove", "--", path, env=env)
                continue
            blob = _git(root, "hash-object", "-w", "--stdin", stdin=text).stdout.strip()
            _git(root, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        tree = _git(root, "write-tree", env=env).stdout.strip()
    sha = _git(root, "commit-tree", tree, "-p", parent, stdin=message, env=_ident(root)).stdout.strip()
    if not sha or _git(root, "update-ref", f"refs/heads/{SIDE}", sha).returncode != 0:
        raise SpecGitError("the side-branch commit failed")
    return sha


def history(project: Project, limit: int = 20) -> list[dict]:
    """The spec commits, newest first: ``{commit, at, subject}`` (from the side branch while it is pending)."""
    root = project.repo_root
    if not is_git(project):
        return []
    ref = side_pending(project) or "HEAD"
    out = _git(root, "log", f"-{limit}", "--format=%H%x1f%ct%x1f%s", ref, "--", ROOT).stdout.splitlines()
    return [{"commit": c, "at": int(t), "subject": s} for c, t, s in (ln.split("\x1f", 2) for ln in out if ln.count("\x1f") == 2)]


def files_at(project: Project, rev: str | None) -> dict[str, str]:
    """Every spec file as it was at ``rev`` (none before the first commit)."""
    if not rev:
        return {}
    root = project.repo_root
    names = _git(root, "ls-tree", "-r", "--name-only", rev, "--", ROOT).stdout.splitlines()
    return {n: _git(root, "show", f"{rev}:{n}").stdout for n in names}


def commit_of(project: Project, sha: str) -> tuple[dict[str, str], dict[str, str], list[str], str]:
    """A spec commit to undo: the spec files before it and after it, the paths it touched, and its
    subject. Refused for a commit that is not a spec commit, or that touched anything outside
    ``.orchestra/machines/``."""
    root = project.repo_root
    full = _ref(root, sha)
    if not full:
        raise SpecGitError(f"no commit {sha!r}")
    known = {h["commit"] for h in history(project, 500)}
    if full not in known:
        raise SpecGitError(f"refused: {sha} is not a commit of the machine specs (see list_duties' history)")
    touched = _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", full).stdout.split()
    outside = [t for t in touched if not t.startswith(ROOT + "/")]
    if outside:
        raise SpecGitError(f"refused: {sha} also changed {', '.join(outside[:5])}; only spec commits are reverted here")
    subject = _git(root, "log", "-1", "--format=%s", full).stdout.strip()
    return files_at(project, _ref(root, f"{full}^")), files_at(project, full), touched, subject
