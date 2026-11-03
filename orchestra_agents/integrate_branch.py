"""A scaffolded collector's files in version control (bootstrap run 3: the 7 pollers'
scaffolds, 28 files under ``.orchestra/machines/<name>/``, were left untracked on the base branch,
so the checkout was dirty and the code reached no pull request).

A collector scaffolded into the owner's repository (``root: repository``) stays live where it is
(the owner, 2026-11-01: a script in the target repository, a coding tool may edit it), and is also
**one commit on the Supervisor's branch** ``orchestra/supervisor`` (``side_branch``: its own files
only, ``poller.toml``, ``<name>.py``, ``test_<name>.py`` and ``AGENTS.md``, never a cache), proposed
with that branch's pull request (``supervisor_pr``), again whenever its ``poller.toml`` changes (the
owner's settings, the approval). The live copy is **ignored in this checkout only**
(``.git/info/exclude``, never a committed ``.gitignore``): ``git status`` stays what the owner left,
and pulling the merged pull request overwrites the ignored copy with the same files (git refuses to
overwrite an untracked file, but not an ignored one), which are tracked from then on. Nothing a
collector writes at run time is in the tree: its cursor and cache are the state directory's (the
kits' runner, ``--state-dir``).

Not carried: a collector the project already had (``adopted``), one handed to the Coordinator
(``lands``: its ticket commits it), one in the state directory, and a checkout git cannot commit
to (no commit yet): the answer says it is left uncommitted."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import side_branch, supervisor_pr
from .config import Project
from .integrate_handoff import SCAFFOLD_FILES, relative, repo_dir
from .store import AgentStore


def _exclude_file(project: Project) -> Path | None:
    p = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=project.repo_root, capture_output=True,
                       text=True, check=False)
    if p.returncode != 0 or not p.stdout.strip():
        return None
    f = Path(p.stdout.strip())
    return f if f.is_absolute() else project.repo_root / f


def ignore_here(project: Project, name: str) -> bool:
    """The live copy ignored in this checkout (``.git/info/exclude``); True once it is."""
    f = _exclude_file(project)
    if f is None:
        return False
    line = f"/{relative(name)}/"
    text = f.read_text(encoding="utf-8") if f.is_file() else ""
    if line not in text.splitlines():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text + ("" if not text or text.endswith("\n") else "\n")
                     + f"# orchestra-agents: {name}'s live copy; its files are on {side_branch.SIDE}\n{line}\n",
                     encoding="utf-8")
    return True


def carries(rec: dict) -> bool:
    return rec.get("root") == "repository" and not rec.get("adopted") and not rec.get("lands") and bool(rec.get("dir"))


def carry(project: Project, store: AgentStore, rec: dict, what: str, reason: str, quote: str | None = None) -> list[str]:
    """The scaffold's own files as one commit on the branch, its live copy ignored here; the lines
    the answer says (none when it is not carried)."""
    if not carries(rec):
        return []
    name, d = rec["name"], Path(rec["dir"])
    if d != repo_dir(project, name):
        return []
    files = {f"{relative(name)}/{f.format(name=name)}": d / f.format(name=name) for f in SCAFFOLD_FILES}
    files = {k: v for k, v in files.items() if v.is_file()}
    title = f"Supervisor: {what}"[:120]
    try:
        sha = side_branch.commit(project, supervisor_pr.message(f"the collector {name}", what, reason), files=files)
    except side_branch.SideBranchError as e:
        return [f"{relative(name)}/ is left uncommitted in the checkout: it could not be committed on {side_branch.SIDE} ({e})"]
    ignored = ignore_here(project, name)
    pr = supervisor_pr.carried(project, store, sha, title=title, reason=reason, quote=quote,
                               verified="its test against the kit's fake passed before it was committed")
    rec["branch"] = {"branch": side_branch.SIDE, "commit": sha or (rec.get("branch") or {}).get("commit")}
    head = (f"its files ({', '.join(Path(k).name for k in files)}) are one commit on {side_branch.SIDE}"
            + (f" ({sha[:9]})" if sha else ", unchanged") + ", for its pull request; the copy that runs is ignored in this "
            "checkout only (.git/info/exclude)" + ("" if ignored else ", or would be: git could not say where") +
            ", so git status stays as you left it, and pulling the merged pull request puts the same files there, tracked")
    return [head, pr["said"]]
