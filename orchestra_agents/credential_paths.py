"""Where a credential's file may live (``integrate_kits.credentials``): the rule ``integrate_system``
checks before anything is scaffolded and ``approve_integration`` checks again.

A token file is refused only when a commit could carry it:

- it lies inside the project's own repository tree (ignored or not: the owner's repository is the
  one a coding tool, the Coordinator's worker and the Supervisor's own branch all commit to), or
- the repository that encloses it (the nearest one git finds from its directory) tracks it.

A file merely under a directory with a ``.git`` somewhere above it is allowed when that repository
ignores it or does not track it: a home directory that is itself a repository (dotfiles), or a
harness's run directory that ignores its ``creds/``. One that is untracked but not ignored there is
allowed with a caution (a ``git add -A`` in that repository would take it), said in the answer.
The file's content is never read here; git is asked only about its path.

No answer here names a place as an example (bootstrap run 6: the Supervisor passed on this module's
hint, ``~/.config/orchestra-bridges/jira-token``, a file that existed on that machine, as where the
owner might keep the token): a refusal describes the rule and shows ``PLACEHOLDER``, never a path."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .store import StoreError

GIT_TIMEOUT_S = 10
PLACEHOLDER = "<path to the token file>"   # what an answer shows for where a token is: never a real path


def _existing(d: Path) -> Path | None:
    """The nearest directory at or above ``d`` that exists (a file's directory may not yet)."""
    for p in [d, *d.parents]:
        if p.is_dir():
            return p
    return None


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        return subprocess.run([git, "-C", str(cwd), *args], capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
                              check=False, env={**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                                                "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return None


def _has_git_above(d: Path) -> bool:
    return any((p / ".git").exists() for p in [d, *d.parents])


def _inside(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def where(path: Path, repo_root: Path | None) -> tuple[str | None, str | None]:
    """``(refusal, caution)`` for a token file at ``path`` (absolute): a refusal when a commit could
    carry it, else a caution when a repository encloses it untracked but not ignored, else neither."""
    p = path.resolve()
    if repo_root is not None and _inside(p, Path(repo_root).resolve()):
        return f"it is inside the project's own repository ({Path(repo_root).resolve()})", None
    d = _existing(p.parent)
    if d is None or not _has_git_above(d):
        return None, None
    top = _git(d, "rev-parse", "--show-toplevel")
    if top is None:
        return f"a repository encloses it ({d} has a .git above it) and git could not be asked whether it tracks the file", None
    if top.returncode != 0:            # a .git that is no repository git knows (a stray file): nothing tracks it
        return None, None
    repo = Path(top.stdout.strip()).resolve()
    if not _inside(p, repo):           # the repository's tree ends above the file (a .git inside a parent that is ignored)
        return None, None
    rel = str(p.relative_to(repo))
    tracked = _git(repo, "ls-files", "--error-unmatch", "--", rel)
    if tracked is None:
        return f"git could not be asked whether the repository at {repo} tracks it", None
    if tracked.returncode == 0:
        return f"the repository at {repo} tracks it (git ls-files)", None
    ignored = _git(repo, "check-ignore", "-q", "--no-index", "--", rel)
    if ignored is not None and ignored.returncode == 0:
        return None, None
    return None, (f"{path} is not tracked by the repository at {repo}, nor ignored there: a `git add -A` in that "
                  f"repository would take it, so the owner may add its directory to that repository's .gitignore")


def hint() -> str:
    """Where a credential's file may live, said in a refusal: the rule, with a placeholder, never a path
    (a path named as an example is taken as advice, and may be one that exists on this machine)."""
    return f"{PLACEHOLDER}: a chmod 600 file outside the project's repository that no repository tracks"


def check(value: str, what: str, repo_root: Path | None) -> tuple[str, str | None]:
    """The path, unchanged, and a caution or None; refused (StoreError) with the reason naming the path."""
    p = Path(value).expanduser()
    if not p.is_absolute():
        raise StoreError(f"{what} is a file's full path ({PLACEHOLDER}, as the owner says it), not {value!r}")
    refused, caution = where(p, repo_root)
    if refused:
        raise StoreError(f"{what}: {value} is refused: {refused}. A credential's file lives where no commit can carry it: "
                         f"{hint()}. Nothing was changed")
    return value, caution


NAMED_KEYS = ("token_file", "bearer_file", "password_file", "value_file", "user_file", "client_key", "client_cert",
              "ca_file")   # the keys whose value is a credential's file: [[sources]] (and their auth), [scm], the kits


def _walk(x, out: list[str]) -> None:
    if isinstance(x, dict):
        for k, v in x.items():
            if k in NAMED_KEYS and isinstance(v, str) and v.strip():
                out.append(v.strip())
            else:
                _walk(v, out)
    elif isinstance(x, list):
        for v in x:
            _walk(v, out)


def named(project) -> list[str]:
    """The exact credential files the project's configuration names (the merged configuration, overlay
    included: ``[[sources]]``, ``[scm]``, any section's ``token_file``; and each scaffolded kit's own
    settings), as absolute paths. Each is a path the owner gave; only the tool command opens it (a
    source's probe, a kit's check), so Claude Code's sandbox lets exactly these be read
    (``harness/sandbox.py``). Never listed to the owner or offered: only passed to the sandbox."""
    found: list[str] = []
    _walk(project.orchestra.config.raw, found)
    try:
        from .integrate_kits import read, shipped
        kits = shipped(project)
    except Exception:  # noqa: BLE001 - no bridges installed: the configuration's alone
        kits = []
    for kit in kits:
        try:
            _walk(read(Path(kit["dir"])), found)
        except (OSError, ValueError):
            continue
    out: list[str] = []
    for v in found:
        p = Path(v).expanduser()
        if not p.is_absolute():
            continue
        for q in (str(p), str(p.resolve())):         # and where a link points: the sandbox denies that too (macOS)
            if q not in out:
                out.append(q)
    return out


UNSEEN = re.compile(r"^token_file (.+?): no such file")   # Orchestra's refusal of a [[sources]] token_file it cannot see


def unseen(refused: dict[str, str]) -> dict[str, tuple[str, str]]:
    """The sources refused only because their ``token_file`` is not seen from this process (Orchestra's
    "token_file <path>: no such file"): name -> (the path, why it was not seen). A path that is there
    (a directory) stays refused. Never the file's content."""
    out: dict[str, tuple[str, str]] = {}
    for name, why in refused.items():
        if (m := UNSEEN.match(why or "")) is None:
            continue
        try:
            Path(m[1]).expanduser().stat()
            continue                         # there, and not a file Orchestra takes: refused as Orchestra says
        except PermissionError:
            seen = "read (Claude Code's sandbox, or the file's permissions, refused it)"
        except OSError:
            seen = "found (not there, or hidden by Claude Code's sandbox)"
        out[name] = (m[1], seen)
    return out


AFTER = ("Write the [[sources]] entry all the same (set_setting, key sources): Orchestra's host and the Core page read "
         "the token from outside any sandbox, and the Core page shows whether the store answers. Tell the owner so")
