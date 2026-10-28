"""Self-healing (group ``patch``, the Supervisor's alone; the owner, 2026-10-28: the Supervisor
carries the self-healing duties for now, code patches included).

Two ways a repair reaches the project, and neither edits the owner's checkout:

- ``propose_patch``: a bug in the project's own code, when ``[project] work = "code"``. The
  Supervisor's unified diff is applied in a **scratch worktree** in the agents' state directory
  (``scratch/patch-<name>``) on a branch of its own, ``supervisor/patch-<name>``, from the base
  branch, and committed there; the worktree is removed and the branch kept. The Coordinator is
  handed a request (``work.requested``) to land that commit: a worker takes it onto its ticket's
  branch, a reviewer reads it, and the merge gate tests and lands it, like any code.
- ``write_override``: a bug in an installed plugin, which the project does not own. An override
  under ``.orchestra/overrides/<name>.py`` in Orchestra's format (Orchestra's ``overrides.py``:
  ``TARGET``, ``PACKAGE`` pinned to the installed version, ``KIND`` wrap or replace, ``ISSUE``,
  and ``def override``), committed alone to the base branch when the checkout is clean there and
  the gate is not landing, else written and left for the owner to commit (the tool says which).
  Orchestra applies it in every process that loads plugins, only on that exact version, so it
  stops when the package changes. Announced as ``system.changed`` (subject ``override:<name>``),
  which begins an era. The upstream issue is ``draft_upstream_issue`` (``upstream.py``)."""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import re
import subprocess
from pathlib import Path

from . import handoff
from .config import Project
from .gate_busy import landing
from .modeling import claim_change
from .secret_shapes import secret_in
from .spec_git import is_git
from .store import AgentStore, StoreError

COMMANDS = frozenset({"propose_patch", "write_override"})
NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
TARGET = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
IDENT = {"GIT_AUTHOR_NAME": "Orchestra Supervisor", "GIT_AUTHOR_EMAIL": "supervisor@orchestra.invalid",
         "GIT_COMMITTER_NAME": "Orchestra Supervisor", "GIT_COMMITTER_EMAIL": "supervisor@orchestra.invalid"}


def _git(root: Path, *args: str, stdin: str | None = None, ident: bool = False) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, **(IDENT if ident else {})}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False, input=stdin, env=env)


def _reason(a: dict) -> str:
    reason = (a.get("reason") or "").strip()
    if len(reason) < 10 or len((a.get("why") or "").strip()) < 10:
        raise StoreError("say what broke and why (why), and why you repair it now (reason), each in a sentence")
    return reason


def handle(project: Project, store: AgentStore, command: str, a: dict) -> dict:
    name = (a.get("name") or "").strip()
    if not NAME.match(name):
        raise StoreError("name: lowercase letters, digits and underscores, 2 to 41 characters")
    reason = _reason(a)
    if not is_git(project):
        raise StoreError("refused: the repository is not a git checkout, so a repair could not be reviewed or reverted")
    return propose_patch(project, store, a, name, reason) if command == "propose_patch" else write_override(project, store, a, name, reason)


def propose_patch(project: Project, store: AgentStore, a: dict, name: str, reason: str) -> dict:
    if project.config.work != "code":
        raise StoreError("refused: the project's work is tasks, so nothing lands in the repository through the gate. A bug in an "
                         "installed plugin is write_override; for the project's own code, ask the owner to choose work = \"code\"")
    diff = a.get("diff") or ""
    if not diff.strip() or "@@" not in diff:
        raise StoreError("diff: a unified diff against the base branch, as git diff prints it")
    if why := secret_in(diff):
        raise StoreError(f"refused: the diff looks like it holds a secret ({why})")
    root, base = project.repo_root, project.config.base_branch
    branch = f"supervisor/patch-{name.replace('_', '-')}"
    if _git(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}").stdout.strip():
        raise StoreError(f"there is already a branch {branch}: name the patch differently, or ask the owner to drop it")
    where = project.state_dir / "scratch" / f"patch-{name}"
    if where.exists():
        _git(root, "worktree", "remove", "--force", str(where))
    added = _git(root, "worktree", "add", "-q", "-b", branch, str(where), base)
    if added.returncode != 0:
        raise StoreError(f"could not make the scratch worktree: {(added.stderr or added.stdout).strip()[:300]}")
    try:
        check = _git(where, "apply", "--check", "--whitespace=nowarn", "-", stdin=diff)
        if check.returncode != 0:
            raise StoreError(f"the diff does not apply to {base}: {(check.stderr or check.stdout).strip()[:500]}")
        _git(where, "apply", "--whitespace=nowarn", "-", stdin=diff)
        _git(where, "add", "-A")
        message = (f"Supervisor: patch {name} — {reason.splitlines()[0][:100]}\n\n{a['why'].strip()}\n\n"
                   + (f"Shown fixed by: {a['test'].strip()}\n\n" if (a.get("test") or "").strip() else "") + f"Reason: {reason}\n")
        has_ident = bool(_git(where, "config", "user.email").stdout.strip())
        done = _git(where, "commit", "-q", "-m", message, ident=not has_ident)
        if done.returncode != 0:
            raise StoreError(f"the commit failed: {(done.stderr or done.stdout).strip()[:300]}")
        sha = _git(where, "rev-parse", "HEAD").stdout.strip()
        files = _git(where, "show", "--name-only", "--format=", sha).stdout.split()
    except StoreError:
        _git(root, "worktree", "remove", "--force", str(where))
        _git(root, "branch", "-D", branch)
        raise
    _git(root, "worktree", "remove", "--force", str(where))
    requirements = (f"Land the Supervisor's repair `{name}`: commit {sha} on branch `{branch}` (it changes {', '.join(files)}). "
                    f"Take that commit onto the ticket's branch (git cherry-pick {sha}), run the unit tests, and change nothing "
                    f"else unless the review asks. What broke: {a['why'].strip()}"
                    + (f" It is shown fixed by: {a['test'].strip()}." if (a.get("test") or "").strip() else "")
                    + " Acceptance: the commit is on the base branch through the gate, and the tests pass.")
    handed = handoff.hand_to_coordinator(store, {"requirements": requirements,
                                                 "context": f"A repair by the Supervisor, not new work: {reason}"}, "supervisor")
    return {"ok": True, "commit": sha, "branch": branch, "files": files, "request": handed.get("request"),
            "next": "Tell the owner what broke, the patch's commit and that it lands through a ticket, a review and the gate."}


def _installed(target: str) -> tuple[str, str]:
    """The distribution (and its version) that provides the module of ``target``, which must exist."""
    mod_name, _, attr = target.partition(":")
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:  # noqa: BLE001 - say why it cannot be found
        raise StoreError(f"cannot import {mod_name}: {type(e).__name__}: {e}") from None
    if not hasattr(mod, attr):
        raise StoreError(f"{mod_name} has no attribute {attr!r}")
    dists = importlib.metadata.packages_distributions().get(mod_name.split(".")[0]) or []
    if not dists:
        raise StoreError(f"{mod_name} is not from an installed distribution: an override is for an installed package")
    dist = dists[0]
    return dist, importlib.metadata.version(dist)


def _check_code(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise StoreError(f"the code does not parse: {e}") from None
    if not any(isinstance(n, ast.FunctionDef) and n.name == "override" for n in tree.body):
        raise StoreError("the code must define `def override(...)` at the top level")
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("TARGET", "PACKAGE", "KIND", "ISSUE") for t in n.targets):
            raise StoreError("leave TARGET, PACKAGE, KIND and ISSUE out of the code: the tool writes them from your arguments")


def write_override(project: Project, store: AgentStore, a: dict, name: str, reason: str) -> dict:
    target, kind, code = (a.get("target") or "").strip(), (a.get("kind") or "").strip(), a.get("code") or ""
    if not TARGET.match(target):
        raise StoreError("target is module:attribute, e.g. tasking.gate:run_specs")
    if kind not in ("wrap", "replace"):
        raise StoreError("kind is wrap (override(original) returns the replacement) or replace (override is the replacement)")
    if target.split(".")[0].split(":")[0] in ("orchestra", "orchestra_agents"):
        raise StoreError("refused: Orchestra's core and the agents themselves are never overridden from here; hand the bug on")
    _check_code(code)
    if why := secret_in(code):
        raise StoreError(f"refused: the code looks like it holds a secret ({why})")
    if a.get("change"):
        from .modeling import own_prediction
        own_prediction(store, "supervisor", a["change"])
    dist, version = _installed(target)
    issue = (a.get("issue") or "").strip()
    text = (f'"""Override {name}: {a["why"].strip()}\n\nWritten by the Supervisor: {reason}\nApplied by Orchestra only on '
            f'{dist}=={version}; it stops applying when the package changes."""\n\n'
            f"TARGET = {target!r}\nPACKAGE = {f'{dist}=={version}'!r}\nKIND = {kind!r}\nISSUE = {issue!r}\n\n{code.strip()}\n")
    rel = f".orchestra/overrides/{name}.py"
    path = project.repo_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    sha, note = _commit_one(project, store, rel, f"Supervisor: override {name} of {target} — {reason.splitlines()[0][:100]}\n\n"
                                                 f"{a['why'].strip()}\n\nPinned to {dist}=={version}.\n")
    data = {"override": name, "target": target, "package": f"{dist}=={version}", "kind": kind, "commit": sha, "reason": reason,
            "by": "supervisor"}
    if claim_change(store, "supervisor", a.get("change")):
        store.events.publish("system.changed", data, id=a["change"], subject=f"override:{name}")
    else:
        store.events.publish("system.changed", data, subject=f"override:{name}")
    return {"ok": True, "file": rel, "package": f"{dist}=={version}", "commit": sha, "note": note,
            "applies": ("Orchestra applies it the next time a process loads its plugins: orchestra run's host when the "
                        "configuration changes, and each plugin process (the agents' host, the task scheduler) and "
                        "orchestra web when they start again. Read the Core page's local overrides to see it applied."),
            "next": "Draft the upstream issue with draft_upstream_issue (override: this name), then tell the owner."}


def _commit_one(project: Project, store: AgentStore, rel: str, message: str) -> tuple[str | None, str]:
    root, base = project.repo_root, project.config.base_branch
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    dirty = [ln for ln in _git(root, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
             if ln.strip() and ln[3:].strip().strip('"') != rel]
    busy = landing(store, "supervisor")
    if branch != base or dirty or busy:
        why = busy or (f"the checkout is on {branch!r}" if branch != base else "the checkout has other uncommitted changes")
        return None, f"written, not committed ({why}): ask the owner to commit {rel}"
    _git(root, "add", "--", rel)
    has_ident = bool(_git(root, "config", "user.email").stdout.strip())
    p = _git(root, "commit", "-q", "-m", message, "--", rel, ident=not has_ident)
    if p.returncode != 0:
        return None, f"written, the commit failed: {(p.stderr or p.stdout).strip()[:200]}"
    return _git(root, "rev-parse", "HEAD").stdout.strip(), f"committed to {base}"
