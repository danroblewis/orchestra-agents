"""Where the machine specs live in git, and how a change to one is committed (the owner,
2026-10-28: every change to a machine's spec is a commit with a reason, and can be reverted; since
2026-11-02, self-improvement 5, on the Supervisor's own branch, never on the base branch).

The specs are the files under ``.orchestra/machines/`` in the project's repository
(``machine_spec.py`` reads them). A change is one commit on the branch ``orchestra/supervisor``
(``side_branch.py``: git's plumbing on a private index; the checkout is never touched), which
``supervisor_pr`` proposes as a pull request, and **its live copy**: the machine's whole spec as
changed, written to the state directory (``<state>/agents/machines/<name>/`` with a ``.pending.json``
marker), which ``machine_spec.load_all`` reads before the repository's. The live copy is retired
(``retire_live``) once the checkout's files are the same, that is, once the pull request is merged
and pulled.

A checkout that is not a git repository has no history to revert to: changes are refused there
(``SpecGitError``), except the first spec written by the plugin, which is only written.

This module, ``side_branch`` and ``roles.commit_orchestra_files`` are the agents' git writes to the
project; the other is the scratch worktree a code patch is written in (``patching.py``)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import side_branch
from .config import Project

ROOT = ".orchestra/machines"
SIDE = side_branch.SIDE


class SpecGitError(ValueError):
    pass


def _git(root: Path, *args: str, env: dict | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False,
                          env={**os.environ, **(env or {})}, input=stdin)


def is_git(project: Project) -> bool:
    return _git(project.repo_root, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def side_pending(project: Project) -> str | None:
    """The side branch's tip when it holds commits the checkout's HEAD does not have; else None."""
    return side_branch.pending(project)


def read_files(project: Project) -> dict[str, str]:
    """Every file under ``.orchestra/machines/`` in the working tree (the live copy of a change not
    merged yet is the state directory's, ``live_copies``). Paths are relative to the repository."""
    root = project.repo_root
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


def commit(project: Project, changes: dict[str, str | None], message: str, *, allow_plain: bool = False,
           not_base: str | None = None) -> dict:
    """Write ``changes`` (path under ``.orchestra/machines/`` -> text, or None to delete) as one
    commit with ``message`` on the Supervisor's branch, and as the machine's live copy. Returns
    ``{commit, where, note}``: ``where`` is ``side``, or ``files`` (written without git, only with
    ``allow_plain``). ``not_base`` (why the base branch must not move now) is kept for its callers:
    the base branch never moves here."""
    for path in changes:
        if not path.startswith(ROOT + "/") or ".." in Path(path).parts:
            raise SpecGitError(f"refused: {path} is not under {ROOT}/")
    root = project.repo_root
    if not is_git(project):
        if not allow_plain:
            raise SpecGitError("refused: the repository is not a git checkout, so the change could not be committed or reverted")
        _write_tree(root, changes)
        return {"commit": None, "where": "files", "note": "written; the repository is not a git checkout, so nothing was committed"}
    try:
        sha = side_branch.commit(project, message, texts=changes)
    except side_branch.SideBranchError as e:
        raise SpecGitError(str(e)) from None
    if sha is None:
        return {"commit": None, "where": "side", "note": "nothing changed"}
    names = _write_live(project, changes, sha)
    return {"commit": sha, "where": "side", "branch": SIDE,
            "note": (f"committed on the Supervisor's branch {SIDE}, never on {project.config.base_branch}, and live at once "
                     f"from the state directory ({', '.join(str(live_root(project) / n) for n in names)}) until its pull "
                     "request is merged and pulled")}


PENDING = ".pending.json"


def live_root(project: Project) -> Path:
    return project.state_dir / "machines"


def _dir_files(d: Path) -> dict[str, str]:
    out = {}
    for p in sorted(d.rglob("*")) if d.is_dir() else []:
        if p.is_file() and p.name != PENDING:
            try:
                out[str(p.relative_to(d))] = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    return out


def live_copies(project: Project) -> dict[str, dict[str, str]]:
    """The live copies of spec changes not merged yet: machine name -> its files (relative to its directory)."""
    base = live_root(project)
    if not base.is_dir():
        return {}
    return {d.name: _dir_files(d) for d in sorted(base.iterdir()) if (d / PENDING).is_file()}


def _write_live(project: Project, changes: dict[str, str | None], sha: str) -> list[str]:
    """Each changed machine's whole spec, as changed, to its live copy."""
    by: dict[str, dict[str, str | None]] = {}
    for path, text in changes.items():
        name, _, inner = path[len(ROOT) + 1:].partition("/")
        by.setdefault(name, {})[inner] = text
    for name, inner in by.items():
        d = live_root(project) / name
        files = _dir_files(d) if (d / PENDING).is_file() else _dir_files(project.repo_root / ROOT / name)
        for k, v in inner.items():
            if v is None:
                files.pop(k, None)
            else:
                files[k] = v
        shutil.rmtree(d, ignore_errors=True)
        for k, v in files.items():
            (d / k).parent.mkdir(parents=True, exist_ok=True)
            (d / k).write_text(v, encoding="utf-8")
        d.mkdir(parents=True, exist_ok=True)
        (d / PENDING).write_text(json.dumps({"branch": SIDE, "commit": sha}), encoding="utf-8")
    return list(by)


def retire_live(project: Project) -> list[str]:
    """The live copies whose files the checkout now holds the same (the pull request merged and
    pulled), removed; their names."""
    gone = []
    for name, files in live_copies(project).items():
        if _dir_files(project.repo_root / ROOT / name) == files:
            shutil.rmtree(live_root(project) / name, ignore_errors=True)
            gone.append(name)
    return gone


def _write_tree(root: Path, changes: dict[str, str | None]) -> None:
    for path, text in changes.items():
        p = root / path
        if text is None:
            if p.exists():
                p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")


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
    full = side_branch.ref(root, sha)
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
    return files_at(project, side_branch.ref(root, f"{full}^")), files_at(project, full), touched, subject
