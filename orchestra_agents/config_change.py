"""The project's configuration as the Supervisor changes it: one change at a time, through
Orchestra's own objects (``project.orchestra.config_edit``, Orchestra's SPEC §8.2), never by
opening or editing ``orchestra.toml`` here.

A change goes to one of two places, the Supervisor's choice (``target``):

- ``repo``: the repository's ``orchestra.toml``, which the team shares. The checkout is never
  touched: the change is committed alone with the reason on the Supervisor's own branch
  ``orchestra/supervisor`` (``side_branch``; never on the base branch), worked out by Orchestra's
  editor on the branch's own ``orchestra.toml``, and proposed as a pull request (``supervisor_pr``).
  It is live at once through the owner's overlay: the same change made there (``live_layers``
  keeps the pairing, and retires the overlay's copy once the checkout's ``orchestra.toml`` has it).
  When the overlay already sets the key itself (the owner's own value, which wins), or cannot say
  the change (removing a key), it is not touched: the change applies once merged, and the answer
  says so.
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
from types import SimpleNamespace

from orchestra.config_edit import Change, ConfigEdit
from orchestra.overlay import OVERLAY, REPO

from . import live_layers, side_branch, supervisor_pr
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
    """Why a change to orchestra.toml cannot be a commit on the Supervisor's branch now, or None.
    The checkout's branch and its uncommitted changes do not matter: it is never touched."""
    if git(project, "rev-parse", "--verify", "-q", "HEAD").returncode != 0:
        return (f"{project.repo_root} is not a git checkout with a commit, so a change to {file_name(project)} cannot be "
                "committed on a branch")
    return None


def someone_editing(project: Project) -> bool:
    """Whether orchestra.toml has an uncommitted change in the checkout (someone's edit in progress)."""
    status = git(project, "status", "--porcelain", "--", file_name(project)).stdout.strip()
    return bool(status) and not status.startswith("??")


def branch_editor(project: Project, text: str) -> ConfigEdit:
    """Orchestra's editor over ``text``, the branch's orchestra.toml, alone (no overlay, no plugin
    checks: the same change is checked against the merged configuration first), in a scratch file of
    the state directory: the checkout's file is never written."""
    scratch = project.state_dir / "scratch" / "branch-config"
    scratch.mkdir(parents=True, exist_ok=True)
    f = scratch / "branch.toml"
    f.write_text(text, encoding="utf-8")
    shim = SimpleNamespace(config_file=f, overlay_file=scratch / "no-overlay.toml", state_dir=scratch,
                           repo_root=project.repo_root, key=project.key)
    return ConfigEdit(shim, plugins=[])


def no_secret(before: str, after: str) -> None:
    """Refuse a change whose new lines hold what looks like a secret."""
    for line in (Counter(after.splitlines()) - Counter(before.splitlines())):
        if what := secret_in(line):
            raise StoreError(f"the change holds what looks like a secret ({what}); configuration is announced on the bus "
                             "and never holds a credential, so pass it through an environment variable or a file the "
                             "program reads")


def target_of(a: dict, default: str | None = REPO) -> str:
    t = a.get("target") or default
    if t not in TARGETS:
        raise StoreError("target is repo (orchestra.toml, the project's settings, committed and shared with the team) "
                         "or overlay (the owner's private file, never committed: their identities, channels, templates)")
    return t


def change(project: Project, edit: Edit, describe: Callable[[Change], str], reason: str, target: str, *,
           store: AgentStore | None = None, quote: str | None = None) -> dict:
    """Make the change ``edit`` asks Orchestra for, in ``target``. A change to the repository is a
    commit on the Supervisor's branch, live through the overlay, and proposed as its pull request
    (``supervisor_pr``, with ``store`` to keep what was proposed and ``quote``, the owner's words, for
    the Jira ID). Returns ``{changed, announce, commit, target, file, written}`` and, for the
    repository, ``live``, ``branch``, ``pull_request`` and ``said`` (``announce``: what the bus is
    told, without the overlay's values; ``note``: when the overlay's own value still wins)."""
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise StoreError("give the reason for the change in a sentence: it goes in the commit and on the bus")
    if target == REPO:
        return _to_branch(project, edit, describe, reason, store=store, quote=quote)
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
    project.reload_config()
    return {**out, "changed": what, "announce": c.what}


def _to_branch(project: Project, edit: Edit, describe: Callable[[Change], str], reason: str, *,
               store: AgentStore | None, quote: str | None) -> dict:
    if why := why_not_committable(project):
        raise StoreError(why)
    name, ed = file_name(project), project.orchestra.config_edit
    parent = side_branch.parent(project)
    text = side_branch.version(project, name, parent) or ed.text(REPO) or ""
    refused: list[ConfigError] = []
    try:            # on the checkout's file with the overlay over it: every reader's and plugin's check, and the note
        checked = edit(ed, {"target": REPO, "dry_run": True})
    except ConfigError as e:        # e.g. an entry only the branch and the overlay have yet
        checked = None
        refused.append(e)
    try:
        c = edit(branch_editor(project, text), {"target": REPO, "dry_run": True})
    except ConfigError as e:
        raise StoreError(str(e)) from e
    what = describe(c)
    out = {"target": REPO, "file": name, "commit": None, "written": c.changed}
    if not c.changed:
        return {**out, "changed": f"nothing: {what} already", "announce": f"nothing: {c.what} already"}
    no_secret(c.before or "", c.after)
    live = None
    if checked is not None and checked.note:
        out["note"] = checked.note
    else:
        try:        # the same change in the overlay, checked against the merged configuration
            live = edit(ed, {"target": OVERLAY, "dry_run": True})
        except ConfigError as e:
            refused.append(e)
    if len(refused) == 2:           # neither the checkout's file nor the overlay can take it: Orchestra's words
        raise StoreError(str(refused[-1]))
    try:
        sha = side_branch.commit(project, f"Supervisor: {what}\n\n{reason}\n", texts={name: c.after})
    except side_branch.SideBranchError as e:
        raise StoreError(f"could not commit {name} on {side_branch.SIDE}: {e}") from None
    short = git(project, "rev-parse", "--short", sha).stdout.strip() if sha else None
    if live is not None and live.changed and sha:
        try:
            ed.apply(live)
            live_layers.keep(project, live, sha, what)
        except ConfigError:
            live = None
    project.reload_config()
    out.update(commit=short, written=bool(sha), changed=what, announce=what,
               live=("live now through the owner's overlay, until the pull request is merged and pulled"
                     if live is not None and live.changed else
                     "applies once the pull request is merged and pulled (the overlay " +
                     ("sets this itself" if out.get("note") else "cannot say this change") + ")"))
    out.update(supervisor_pr.carried(project, store, sha, title=f"Supervisor: {what}"[:120], reason=reason,
                                     verified=(f"Orchestra's configuration checks (every reader's and plugin's) passed on "
                                               f"the change; {out['live']}"), quote=quote))
    return out
