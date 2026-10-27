"""The project's ``orchestra.toml`` as the Supervisor changes it: one edit at a time, checked by
every reader that matters, written whole, and committed alone to the base branch with the reason.

The edit is made on a ``tomlkit`` document, so the file's comments and layout are kept and only
what changed differs. Before anything is written the new text must parse, and Orchestra's own
readers must accept it at least as well as the old text: its settings (``orchestra.config``), its
``[[machines]]`` (``orchestra.launcher``), its ``[[collect]]`` and ``[[scrape]]``
(``orchestra.collect.spec``), and the agents' ``[agents]`` (``config.Config``). The task system's
sections are written as its SPEC names them; it reads them itself.

A change is made only when the checkout can take it as a commit of its own: the repository root
is a git checkout on the base branch, and ``orchestra.toml`` has no uncommitted change (someone's
edit in progress is never mixed into the Supervisor's). The commit holds ``orchestra.toml`` and
nothing else, whatever else is changed in the checkout. When the commit fails the file is put
back as it was, so a refused change leaves nothing behind.

Nothing that looks like a secret is ever added (``secret_shapes``): the file is committed and
every change is announced on the bus, so a change whose new lines hold a password, a token or a
key is refused before anything is written, and the Supervisor is told to pass it through an
environment variable or a file the program reads."""

from __future__ import annotations

import os
import subprocess
import tomllib
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import tomlkit
from orchestra.collect import spec as collect_spec
from orchestra.config import Config as OrchestraConfig
from orchestra.launcher import check as check_process
from orchestra.launcher import from_toml as processes_from_toml
from tomlkit.toml_document import TOMLDocument

from .config import Config, ConfigError, Project
from .secret_shapes import secret_in
from .store import StoreError

FILE = "orchestra.toml"
AUTHOR = ("Orchestra Supervisor", "supervisor@orchestra.invalid")   # only when the checkout names no committer


def path(project: Project) -> Path:
    return project.repo_root / FILE


def git(project: Project, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(project.repo_root), *args], capture_output=True, text=True, check=False)


def why_not_committable(project: Project) -> str | None:
    """Why a change to orchestra.toml cannot be its own commit on the base branch now, or None."""
    base = project.config.base_branch
    branch = git(project, "rev-parse", "--abbrev-ref", "HEAD")
    if branch.returncode != 0:
        return f"{project.repo_root} is not a git checkout, so a change to {FILE} cannot be committed"
    if branch.stdout.strip() != base:
        return (f"the repository root has {branch.stdout.strip()!r} checked out, not {base}: a change committed there "
                f"would not be on {base}; the human checks out {base} in the root first")
    if git(project, "status", "--porcelain", "--", FILE).stdout.strip():
        return (f"{FILE} has an uncommitted change in the repository root (someone is editing it); the human commits or "
                "drops it first")
    return None


def _problems(text: str) -> list[str]:
    """What Orchestra's and the agents' readers say against a text of orchestra.toml."""
    raw = tomllib.loads(text)
    out: list[str] = []
    try:
        OrchestraConfig.from_toml(text)
    except (ConfigError, ValueError, TypeError) as e:
        out.append(f"Orchestra's settings: {e}")
    try:
        out += [f"[[machines]]: {why}" for p in processes_from_toml(raw) if (why := check_process(p))]
    except TypeError as e:
        out.append(f"[[machines]]: {e}")
    out += collect_spec.from_toml(raw)[1]
    try:
        Config.from_toml(text)
    except (ConfigError, ValueError, TypeError) as e:
        out.append(f"[agents]: {e}")
    return out


def check(before: str, after: str) -> None:
    """Refuse a new text that does not parse, or that Orchestra's or the agents' readers accept
    less well than the old one."""
    try:
        tomllib.loads(after)
    except tomllib.TOMLDecodeError as e:
        raise StoreError(f"the change would not parse as TOML: {e}") from e
    old = set(_problems(before))
    new = [p for p in _problems(after) if p not in old]
    if new:
        raise StoreError("the change was not made, because " + "; ".join(new))


def no_secret(before: str, after: str) -> None:
    """Refuse a change whose new lines hold what looks like a secret."""
    for line in (Counter(after.splitlines()) - Counter(before.splitlines())):
        if what := secret_in(line):
            raise StoreError(f"the change holds what looks like a secret ({what}); {FILE} is committed and announced, so "
                             "pass it through an environment variable or a file the program reads")


def write(project: Project, text: str) -> None:
    """The whole file at once (a reader never sees half of it), keeping its mode."""
    p = path(project)
    tmp = p.with_name(f".{FILE}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, p.stat().st_mode & 0o777)
    except OSError:
        pass
    os.replace(tmp, p)


def commit(project: Project, message: str) -> str:
    """Commit orchestra.toml alone; returns the commit's short id. Raises with git's words."""
    who = [] if git(project, "config", "user.email").stdout.strip() else ["-c", f"user.name={AUTHOR[0]}", "-c", f"user.email={AUTHOR[1]}"]
    p = subprocess.run(["git", "-C", str(project.repo_root), *who, "commit", "-q", "-m", message, "--", FILE],
                       capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise StoreError(f"git could not commit {FILE}: {(p.stderr or p.stdout).strip()}")
    return git(project, "rev-parse", "--short", "HEAD").stdout.strip()


def change(project: Project, edit: Callable[[str], tuple[str, str]], reason: str) -> dict:
    """Apply ``edit`` (given the file's text, it returns one line saying what it changed and the
    new text, or raises StoreError) and commit the result with the reason. Returns ``{changed,
    commit}``; ``commit`` is None when the file came out the same."""
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise StoreError("give the reason for the change in a sentence: it goes in the commit and on the bus")
    if why := why_not_committable(project):
        raise StoreError(why)
    before = path(project).read_text(encoding="utf-8")
    what, after = edit(before)
    if after == before:
        return {"changed": f"nothing: {what} already", "commit": None}
    no_secret(before, after)
    check(before, after)
    write(project, after)
    try:
        sha = commit(project, f"Supervisor: {what}\n\n{reason}\n")
    except StoreError:
        write(project, before)
        raise
    return {"changed": what, "commit": sha}


def on_document(fn: Callable[[TOMLDocument], str]) -> Callable[[str], tuple[str, str]]:
    """An edit made on the parsed document (``tomlkit``, which keeps comments and layout)."""
    def run(text: str) -> tuple[str, str]:
        doc = tomlkit.parse(text)
        what = fn(doc)
        return what, tomlkit.dumps(doc)
    return run
