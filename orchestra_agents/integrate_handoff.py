"""Where a scaffolded collector lives while ``integrate_system`` (``design_integrate.py``) hands it to
the Coordinator, so that the hand-off can finish:

- **Scaffolded into the owner's repository** (``root: repository``), the files are untracked in
  the owner's checkout. A worker's worktree is cut from the base branch and lacks them, and once a
  copy landed there git would refuse to overwrite the untracked one. So before handing it on,
  ``move_out`` moves it to ``<state>/machines/<name>/`` (the draft, the record's ``dir``) and
  records where it lands (``lands``: ``.orchestra/machines/<name>/`` in the checkout). The worker
  copies the draft into the same relative path in its own worktree, adapts and commits it there;
  the ticket's review and gate land it on the base branch. Once the checkout holds the landed copy,
  ``landed`` moves the record to it (and removes the draft); until then the integration is not
  approved (``approve_integration``), since what would run is the landed script.
- **In the state directory** (``root: state``, the owner keeps the repository clean), it is
  outside any repository: the worker adapts it in place and commits nothing.

``leftover`` removes what a killed call left (a scaffold with no integration record), so that the
same call again does not stop at the kit's "exists"."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import integrate_kits as kits
from .config import Project
from .design import MACHINE
from .handoff import hand_to_coordinator
from .store import AgentStore, StoreError

SCAFFOLD_FILES = ("poller.toml", "{name}.py", "test_{name}.py", "AGENTS.md")
CACHES = (".pytest_cache", "__pycache__")


def relative(name: str) -> str:
    return f".orchestra/machines/{name}"


def repo_dir(project: Project, name: str) -> Path:
    return project.repo_root / relative(name)


def draft_dir(project: Project, name: str) -> Path:
    return project.orchestra.state_dir / "machines" / name


def tracked(project: Project, d: Path) -> bool:
    """Whether git tracks anything in ``d`` (True when git cannot say: never removed then)."""
    p = subprocess.run(["git", "ls-files", "--", str(d)], cwd=project.repo_root, capture_output=True, text=True, check=False)
    return p.returncode != 0 or bool(p.stdout.strip())


def leftover(project: Project, name: str) -> list[str]:
    """The directories of a scaffold whose call was killed before its record was saved, removed:
    only one that holds nothing but the scaffold's own files, none of them tracked by git."""
    own = {f.format(name=name) for f in SCAFFOLD_FILES} | set(CACHES)
    gone = []
    for d in (repo_dir(project, name), draft_dir(project, name)):
        if not d.is_dir() or not {p.name for p in d.iterdir()} <= own:
            continue
        if d == repo_dir(project, name) and tracked(project, d):
            continue
        shutil.rmtree(d)
        gone.append(str(d))
    return gone


def move_out(project: Project, rec: dict) -> None:
    """A repository scaffold moved out of the owner's checkout to the draft before it is handed on."""
    if rec.get("root") != "repository" or rec.get("tracked"):
        return                                      # a committed collector: the worker's worktree has it already
    src, dest = Path(rec["dir"]), draft_dir(project, rec["name"])
    if dest.exists():
        raise StoreError(f"{dest} exists, so the scaffold in {src} cannot be moved out of the checkout for the "
                         "Coordinator: remove it (or choose another name)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    rec.update(dir=str(dest), lands=str(src))


def landed(rec: dict) -> bool:
    """Whether the script the Coordinator has is in the checkout now (its ticket landed); the record
    then reads it there, and the draft goes."""
    lands = rec.get("lands")
    if not lands:
        return True
    where = Path(lands)
    if not (kits.settings_file(where).is_file() and (where / f"{rec['name']}.py").is_file()):
        return False
    if rec.get("dir") != lands:
        shutil.rmtree(rec["dir"], ignore_errors=True)
        rec["dir"] = lands
    return True


def who_lands(rec: dict) -> str:
    """Where the scaffold is and who lands it, for the answer."""
    if rec.get("adopted"):
        where = ("committed in the owner's repository" if rec.get("tracked") else "in the owner's repository, uncommitted"
                 if rec.get("root") == "repository" else f"in the {rec.get('root')} directory, outside the repository")
        return (f"it was already in the project ({where}, at {rec['dir']}) and is configured where it is: a coding tool may "
                "edit it there; never ask the owner to commit, discard or land it")
    if rec.get("root") != "repository":
        return "in Orchestra's state directory, outside the repository as the owner asked: edit it there and commit nothing"
    if rec.get("lands"):
        return (f"moved out of the owner's checkout to {rec['dir']} while the Coordinator has it: its worker commits it at "
                f"{relative(rec['name'])}/ on a ticket, and the ticket's review and gate land it on the base branch")
    return (f"in the owner's repository ({relative(rec['name'])}/, uncommitted, and live from there): a coding tool may edit "
            "it there; never ask the owner to commit, discard or land it (INTEGRATING.md)")


def hand_on(store: AgentStore, rec: dict, why: str, prov: dict) -> tuple[str, list]:
    """The scaffolded script handed to the Coordinator as work (the way the Supervisor gives work):
    a worker adapts it to the real system until its test passes; never a stop."""
    name, t, src = rec["name"], kits.title(rec["system"]), rec["dir"]
    rel = relative(name)
    if rec.get("lands"):
        where = (f"Copy that directory into your own worktree at the same relative path ({rel}/: `mkdir -p .orchestra/machines "
                 f"&& cp -R {src} {rel}`), adapt {name}.py there (and its fake's world in the test), and commit {rel}/ on "
                 f"your branch.")
        test, lands = f"{rel}/test_{name}.py", (f"{rel}/ is committed on your branch; the ticket's review and gate land it on "
                                               "the base branch.")
    elif rec.get("tracked"):
        where = (f"It is committed in the repository at {rel}/: adapt {name}.py there in your own worktree (and its fake's "
                 f"world in the test), and commit {rel}/ on your branch.")
        test, lands = f"{rel}/test_{name}.py", f"{rel}/ is committed on your branch; the ticket's review and gate land it."
    else:
        where = (f"Adapt {name}.py there, in place (and its fake's world in the test); it is outside any repository, so "
                 "commit nothing.")
        test, lands = f"{src}/test_{name}.py", "the files stay in that directory (outside the repository, as the owner asked)."
    req = hand_to_coordinator(store, {"requirements": (
        f"Make the collector {name} for {t} work: {why}. It is a script scaffolded from the bridges plugin's {rec['kit']} kit "
        f"at {src} ({name}.py, its poller.toml, test_{name}.py against the kit's fake, and AGENTS.md, which holds the kit's "
        f"rules). The owner wants: {rec['wants']}. {where} Make it work against {t}'s real API. Keep AGENTS.md's rules: read "
        "only, metadata only, the token by reference (a variable's name or a file outside the project's repository that no repository tracks), never "
        "clearance or credentials in the code (poller.toml's clearance and credentials are the owner's, written by the "
        "Supervisor's approve_integration), and writes to the system only through the Outbox. Acceptance: "
        f"`python -m pytest {test} -q` passes, and {lands}"),
        "context": prov["reason"]}, MACHINE)
    return req["request"], [(f"handed the script to the Coordinator as work ({req['request']}): {why}; a worker adapts it "
                            "until its test passes, and you are woken with the plan")]
