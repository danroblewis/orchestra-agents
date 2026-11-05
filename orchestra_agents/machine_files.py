"""``edit_machine_file``: the Supervisor changes Orchestra's own files itself (the owner, 2026-11-04,
T54: "It's just trying to set up the Jira machine and it is refusing to make code changes").

A machine's script and definition, a workflow's definition and a resource kind are setup, not the
project's code: without the task system nothing else could change them, so the Supervisor could not
finish its own setup. It writes only inside these places, each path resolved (symlinks followed)
and refused if it lands anywhere else:

- in the repository: ``.orchestra/machines/**``, ``.orchestra/workflows/**``, ``.orchestra/resources/**``
  (never ``.orchestra/plugins`` or ``.orchestra/overrides``, which are ``write_override``'s, and
  never the Supervisor's own spec, ``.orchestra/machines/supervisor/``, which its duty tools change);
- in Orchestra's state directory: ``machines/**``, ``workflows/<name>/workflow.json`` (a definition,
  never the floor's state beside it) and ``resources/**``.

A repository file is one commit on the Supervisor's branch (``side_branch``, ``supervisor_pr``), as
``set_setting``'s repository changes are; the checkout is not touched, except a scaffolded collector's
live copy that only ``.git/info/exclude`` ignores (``integrate_branch``): that copy is what runs, so it
is written too. A state-directory file is written in place. Either way the previous text is kept
(``<state>/agents/machine_edits/<id>.json``) and ``revert_change`` with that id puts it back. Nothing
that looks like a secret is written (``config_change.no_secret``). A machine's script that came from a
bridges kit (an ``integrate_system`` record whose directory holds the file) has the kit's test run
on the file as written, then the machine is restarted (``restart_machine``) when its test passed and
its live copy changed; the answer carries both results."""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import side_branch, supervisor_pr
from .config import Project
from .config_change import no_secret
from .store import AgentStore, StoreError

REPO_ROOTS = (".orchestra/machines", ".orchestra/workflows", ".orchestra/resources")
STATE_ROOTS = ("machines", "resources")
OWN_SPEC = "supervisor"                    # .orchestra/machines/supervisor/: the duty tools' alone
EDITS = "machine_edits"
MAX_CHARS = 200_000
ID_PREFIX = "edit-"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def place(project: Project, raw: str) -> tuple[str, Path, str]:
    """``(where, file, rel)``: ``repo`` or ``state``, the resolved file, and its path relative to that root.
    Refused for any path outside the allowed places."""
    raw = (raw or "").strip()
    if not raw:
        raise StoreError("path is the file to write: .orchestra/machines/<name>/<file> in the repository, or machines/<name>/"
                         "<file>, workflows/<name>/workflow.json or resources/<kind>/<file> in the state directory")
    given = Path(raw).expanduser()
    if ".." in given.parts:
        raise StoreError(f"refused: {raw} climbs out with '..'; give the file's own path")
    repo, state = project.repo_root.resolve(), project.orchestra.state_dir.resolve()
    if given.is_absolute():
        target = given
    elif given.parts and given.parts[0] == ".orchestra":
        target = project.repo_root / given
    else:
        target = project.orchestra.state_dir / given
    resolved = target.resolve()
    for where, root, allowed in (("repo", repo, REPO_ROOTS), ("state", state, STATE_ROOTS + ("workflows",))):
        if not _inside(resolved, root):
            continue
        rel = resolved.relative_to(root).as_posix()
        if where == "repo" and _inside(resolved, state):        # a state directory inside the repository is the state's
            continue
        for prefix in allowed:
            if rel.startswith(prefix + "/"):
                rest = rel[len(prefix) + 1:].split("/")
                if prefix.endswith("machines") and rest[0] == OWN_SPEC:
                    raise StoreError("refused: your own spec (machines/supervisor/) changes only with your duty tools")
                if where == "state" and prefix == "workflows" and (len(rest) != 2 or rest[1] != "workflow.json"):
                    raise StoreError("refused: in the state directory's workflows/ only a definition, workflows/<name>/"
                                     "workflow.json, is yours to write; the floor's state beside it is the plugin's")
                if not rest[-1]:
                    break
                return where, resolved, rel
        break
    raise StoreError(f"refused: {raw} is not one of Orchestra's own files. edit_machine_file writes only .orchestra/machines/, "
                     ".orchestra/workflows/ and .orchestra/resources/ in the repository, and machines/, workflows/<name>/"
                     "workflow.json and resources/ in the state directory (symlinks followed). The project's code is a "
                     "worker's; .orchestra/plugins and .orchestra/overrides are write_override's")


def _new_text(a: dict, before: str | None) -> str:
    content, old, new = a.get("content"), a.get("old"), a.get("new")
    if content is not None:
        if old is not None or new is not None:
            raise StoreError("give content (the whole file) or old and new (an exact replacement), not both")
        text = str(content)
    else:
        if old is None or new is None:
            raise StoreError("give content (the whole file), or old and new: old is text the file holds exactly once")
        if before is None:
            raise StoreError("the file does not exist yet: give its whole content")
        n = before.count(str(old))
        if n != 1:
            raise StoreError(f"old occurs {n} times in the file; it must occur exactly once (copy it exactly, with more "
                             "lines around it)")
        text = before.replace(str(old), str(new))
    if len(text) > MAX_CHARS:
        raise StoreError(f"the file would be {len(text):,} characters; at most {MAX_CHARS:,}")
    return text


def _ignored_live_copy(project: Project, file: Path) -> bool:
    """A file in the checkout that git ignores and does not track: a scaffold's live copy (integrate_branch)."""
    root = project.repo_root
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", str(file)], cwd=root, capture_output=True,
                             check=False).returncode == 0
    ignored = subprocess.run(["git", "check-ignore", "-q", "--", str(file)], cwd=root, capture_output=True,
                             check=False).returncode == 0
    return ignored and not tracked


def _write(file: Path, text: str | None) -> None:
    if text is None:
        file.unlink(missing_ok=True)
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(f".{file.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    if file.exists():
        shutil.copymode(file, tmp)
    tmp.replace(file)


def _keep(project: Project, rec: dict) -> str:
    rid = ID_PREFIX + secrets.token_hex(4)
    d = project.state_dir / EDITS
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rid}.json").write_text(json.dumps({**rec, "id": rid}, indent=1), encoding="utf-8")
    return rid


def _kit_machine(store: AgentStore, file: Path) -> dict | None:
    """The integration whose directory holds ``file``: a kit's poller, with its name and test."""
    from .design_integrate import integrations
    for name, rec in integrations(store).items():
        d = rec.get("dir")
        if d and _inside(file, Path(d).resolve()):
            return {**rec, "name": rec.get("name") or name}
    return None


def _test(store: AgentStore, file: Path, text: str) -> tuple[dict | None, dict | None]:
    """The kit's test on the file as written, in a copy of its directory; ``(test, machine)``."""
    rec = _kit_machine(store, file)
    if rec is None:
        return None, None
    from . import integrate_kits as kits
    d = Path(rec["dir"]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / d.name
        shutil.copytree(d, copy, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        (copy / file.relative_to(d)).write_text(text, encoding="utf-8")
        return kits.run_test(copy, rec["name"]), rec


def _restart(project: Project, store: AgentStore, name: str, reason: str) -> dict:
    from .restart import restart_machine
    try:
        return {"ok": True, **restart_machine(project, store, {"name": name, "reason": reason}, "supervisor", "supervisor")}
    except StoreError as e:
        return {"ok": False, "error": str(e)}


def _apply(project: Project, store: AgentStore, where: str, file: Path, rel: str, text: str | None, reason: str,
           what: str, quote: str | None) -> dict:
    """Write the text where it lives (a commit on the branch for the repository); what it did."""
    out: dict = {"where": where, "path": rel if where == "repo" else str(file)}
    if where == "state":
        _write(file, text)
        out.update(written=True, live=True)
        return out
    try:
        sha = side_branch.commit(project, supervisor_pr.message(f"the file {rel}", what, reason), texts={rel: text})
    except side_branch.SideBranchError as e:
        raise StoreError(f"refused, nothing written: {rel} could not be committed on {side_branch.SIDE} ({e})") from e
    pr = supervisor_pr.carried(project, store, sha, title=f"Supervisor: {what}"[:120], reason=reason, quote=quote,
                               verified="its kit's test, where it has one, ran on it before it was committed")
    out.update(commit=sha, branch=side_branch.SIDE, pull_request=pr.get("pull_request"), said=pr.get("said"))
    live = _ignored_live_copy(project, file)
    if live:
        _write(file, text)
    out["live"] = bool(live)
    out["checkout"] = ("its live copy (ignored in this checkout only) was written too, so what runs is the change"
                       if live else "the checkout is not touched: it applies once the branch's pull request is merged and pulled")
    return out


def edit_machine_file(project: Project, store: AgentStore, a: dict, role: str) -> dict:
    if role != "supervisor":
        raise StoreError("only the Supervisor changes Orchestra's own files (its setup)")
    reason = str(a.get("reason") or "").strip()
    if len(reason) < 10:
        raise StoreError("give the reason in a sentence: it goes in the commit and the ledger")
    where, file, rel = place(project, str(a.get("path") or ""))
    if file.is_dir():
        raise StoreError(f"{rel} is a directory; give a file")
    before = file.read_text(encoding="utf-8") if file.is_file() else None
    text = _new_text(a, before)
    no_secret(before or "", text)
    if text == before:
        return {"ok": True, "changed": False, "path": rel, "said": f"{rel} already holds exactly that; nothing written"}
    test, machine = _test(store, file, text)
    what = f"{'change' if before is not None else 'add'} {rel}"
    out = _apply(project, store, where, file, rel, text, reason, what, supervisor_pr.owner_words(store, a))
    out["revert"] = _keep(project, {"where": where, "file": str(file), "rel": rel, "before": before, "after": text,
                                    "reason": reason, "at": time.time()})
    out.update(ok=True, changed=True)
    if test is not None:
        out["test"] = test
    if machine is not None:
        name = machine["name"]
        if test is not None and not test.get("passed"):
            out["restart"] = {"ok": False, "skipped": f"not restarted: its kit's test failed ({test.get('summary')}); fix "
                                                      "the file and call again"}
        elif not out.get("live"):
            out["restart"] = {"ok": False, "skipped": "not restarted: what runs is unchanged until the pull request is merged"}
        else:
            out["restart"] = _restart(project, store, name, f"its file {rel} changed: {reason}")
    out["say"] = (f"Tell the owner what you changed in {rel} and why" + (", the test's result" if test else "")
                  + (", the restart's" if "restart" in out else "") + f", and that revert_change {out['revert']} undoes it.")
    return out


def revert(project: Project, store: AgentStore, rid: str, reason: str) -> dict:
    """Put back what an ``edit_machine_file`` replaced, while the file still holds what it wrote."""
    f = project.state_dir / EDITS / f"{rid}.json"
    if not rid.startswith(ID_PREFIX) or not f.is_file() or "/" in rid:
        raise StoreError(f"no file change {rid!r}: the id is the one edit_machine_file answered (revert)")
    rec = json.loads(f.read_text(encoding="utf-8"))
    where, rel = rec["where"], rec["rel"]
    where_now, file, rel_now = place(project, rec["file"])
    now = file.read_text(encoding="utf-8") if file.is_file() else None
    if where == "repo":
        now = side_branch.version(project, rel, side_branch.parent(project))
    if now != rec["after"]:
        raise StoreError(f"{rel} has changed since {rid}; read it and change it with edit_machine_file instead")
    out = _apply(project, store, where_now, file, rel_now, rec["before"], reason, f"revert {rid} on {rel}", None)
    f.unlink()
    return {"ok": True, "reverted": rid, **out}
