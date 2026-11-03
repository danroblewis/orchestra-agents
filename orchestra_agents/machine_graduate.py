"""``graduate_machine``: a program proven in ``.orchestra/machines/<name>/`` moves into the project's
own tree (the guide's "A machine's three ages", the owner, 2026-11-02: "it should be replaced by a
simple `npm run` or `cargo run` or `python3 -m` command"), beside the integrate tools in the
``design`` group.

**A dry run first** (``dry_run``, the default): where it goes and what runs it there, in the
project's language (``graduate_wiring``), each ``[[machines]]`` and ``[[collect]]`` entry whose
command runs it with its new command, the one manifest entry the language needs, what stays in
``.orchestra/`` (nothing of it: every other directory there is left as it is), and the lines of its
code that name its old path. The plan is kept in the Supervisor's state.

**The move is a change to the system**, so it is the owner's: ``dry_run: false`` with their quoted
yes from this turn (``author: owner``, as ``approve_integration``), and only the plan the dry run
showed (worked out again, it must be the same). Then: ``git mv`` of the directory (a plain move when
git does not track it, as a scaffolded collector), the language's wiring, and each entry's
``command`` changed through Orchestra's configuration objects (``config_edit``, never the file as
text): in ``orchestra.toml`` written in place, and in the owner's overlay as any overlay change. All
of it is live at once, and the move, the wiring, the manifest and ``orchestra.toml`` are **one
commit on the Supervisor's branch** ``orchestra/supervisor`` (``side_branch``; a commit of
``orchestra.toml`` alone would name code its commit does not hold), never on the base branch, which
becomes a pull request for the owner or the merge gate to merge (``supervisor_pr``; since
2026-11-02, the owner's self-improvement 5). The program's code is not changed, so its announcement and
its events stay the same: the answer says which source to hear again (the launcher restarts a
process whose command changed within a second of reading the configuration)."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import time
import tomllib
from pathlib import Path

from orchestra.config import ConfigError
from orchestra.overlay import OVERLAY, REPO

from . import graduate_wiring as wiring
from . import machine_ages, side_branch, supervisor_pr
from .config import Project
from .config_change import change, no_secret, someone_editing
from .design import MACHINE, OWNER
from .design_machines import ID
from .setup_tools import CHANGED, INSTRUMENT_SECTIONS, INSTRUMENTS_CHANGED
from .store import AgentStore, StoreError

KEY = "graduations"
SECTIONS = ("machines", "collect")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


def _rel(project: Project, p: Path) -> str:
    return str(p.resolve().relative_to(project.repo_root.resolve()))


def runs_it(project: Project, mdir: Path, entry: str) -> list[dict]:
    """Every ``[[machines]]`` and ``[[collect]]`` entry whose command runs a file in ``mdir``: its
    section, name, the file it holds the configuration in, its command and the arguments after the
    script."""
    raw = project.orchestra.config.raw or {}
    ed = project.orchestra.config_edit
    over = tomllib.loads(ed.text(OVERLAY) or "")
    out = []
    root = mdir.resolve()
    for section in SECTIONS:
        for e in raw.get(section) or []:
            if not isinstance(e, dict) or e.get("enabled") is False or not e.get("command"):
                continue
            words = shlex.split(str(e["command"]))
            for i, w in enumerate(words):
                p = Path(w).expanduser()
                p = (p if p.is_absolute() else project.repo_root / p).resolve()
                if p == root or root in p.parents:
                    if p != (root / entry).resolve():
                        raise StoreError(f"[[{section}]] {e['name']} runs {_rel(project, p)}, not {name_of(mdir)}'s entry "
                                         f"{entry}: which file it graduates as is the Coordinator's to say; nothing was moved")
                    own = next((x for x in over.get(section) or [] if x.get("name") == e["name"] and "command" in x), None)
                    out.append({"section": section, "name": e["name"], "target": OVERLAY if own else REPO,
                                "command": str(e["command"]), "args": words[i + 1:]})
                    break
    return out


def name_of(mdir: Path) -> str:
    return mdir.name


def other_mentions(project: Project, mdir: Path, handled: list[dict]) -> list[str]:
    """Anything else in the configuration that names the directory: moving it would break that."""
    rel, absol, found = f".orchestra/machines/{mdir.name}", str(mdir.resolve()), []
    done = {(h["section"], h["name"]) for h in handled}

    def walk(v, where):
        if isinstance(v, dict):
            if where in SECTIONS and (where, v.get("name")) in done:
                return
            for k, x in v.items():
                walk(x, where or k)
        elif isinstance(v, list):
            for x in v:
                walk(x, where)
        elif isinstance(v, str) and (rel in v or absol in v):
            found.append(f"{where}: {v}")
    walk(project.orchestra.config.raw or {}, "")
    return found


def code_mentions(mdir: Path) -> list[str]:
    out = []
    for p in wiring.files_of(mdir):
        try:
            for n, text in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if ".orchestra/machines" in text:
                    out.append(f"{p.relative_to(mdir)}:{n}: {text.strip()[:120]}")
        except (UnicodeDecodeError, OSError):
            continue
    return out


def dirty(project: Project, path: str) -> bool:
    """Whether git has an uncommitted change to a tracked file under ``path``: someone's edit in progress."""
    lines = _git(project.repo_root, "status", "--porcelain", "--", path).stdout.splitlines()
    return any(x and not x.startswith("??") for x in lines)


def make_plan(project: Project, store: AgentStore, a: dict) -> dict:
    name = (a.get("name") or "").strip()
    if not ID.match(name):
        raise StoreError("name the machine's directory in .orchestra/machines/ (relator): lower case letters, digits and _")
    mdir = project.repo_root / ".orchestra" / "machines" / name
    if not mdir.is_dir():
        have = sorted(p.name for p in (project.repo_root / ".orchestra" / "machines").glob("*") if p.is_dir())
        raise StoreError(f"there is no .orchestra/machines/{name}/; the directories there: {', '.join(have) or 'none'}")
    if (mdir / "role.md").exists() or name == "supervisor":
        raise StoreError(f".orchestra/machines/{name}/ is an agent's spec (role.md), not a program: an agent graduates to a "
                         "program first (its steps written as code, the agent retired), then the program graduates")
    if _git(project.repo_root, "rev-parse", "--git-dir").returncode != 0:
        raise StoreError(f"{project.repo_root} is not a git checkout: the move is a change the owner commits; nothing was moved")
    w = wiring.plan(project.repo_root, mdir, name, a.get("language"), a.get("destination"))
    runs = runs_it(project, mdir, w["entry"])
    if not any(r["section"] == "machines" for r in runs):
        raise StoreError(f"no [[machines]] entry runs .orchestra/machines/{name}/ (a workflow's program, or a script nothing "
                         "starts): graduating it would change what runs it, so hand it to the Coordinator; nothing was moved")
    if others := other_mentions(project, mdir, runs):
        raise StoreError(f"the configuration names .orchestra/machines/{name}/ elsewhere too ({'; '.join(others[:3])}): "
                         "the move would break it; nothing was moved")
    busy = [p for p in (f".orchestra/machines/{name}", w["manifest"]["file"] if w["manifest"] else None) if p and dirty(project, p)]
    if any(r["target"] == REPO for r in runs) and someone_editing(project):
        busy.append(project.orchestra.config_file.name)
    if busy:
        raise StoreError(f"{', '.join(busy)} has an uncommitted change (someone's edit in progress): the owner commits or "
                         "drops it first; nothing was moved")
    tracked = bool(_git(project.repo_root, "ls-files", "--", f".orchestra/machines/{name}").stdout.strip())
    entries = [{**r, "new_command": wiring.with_args(w["command"], r["args"], w["language"])} for r in runs]
    ids = sorted({m["id"] for m in _heard(project, store) if m.get("entry") in {r["name"] for r in runs if r["section"] == "machines"}})
    return {"name": name, "from": f".orchestra/machines/{name}", "to": w["dest"], "language": w["language"],
            "entry": w["entry"], "command": w["command"], "how": "git mv" if tracked else "move (git does not track it yet)",
            "files": [str(p.relative_to(mdir)) for p in wiring.files_of(mdir)],
            "entries": [{k: e[k] for k in ("section", "name", "target", "command", "new_command")} for e in entries],
            "writes": w["writes"], "manifest": w["manifest"], "notes": w["notes"],
            "code_names_old_path": code_mentions(mdir), "sources": ids}


def _heard(project: Project, store: AgentStore) -> list[dict]:
    try:
        return machine_ages.machine_ages(project, store).get("machines") or []
    except Exception:  # noqa: BLE001 - the bus unreadable: the plan still stands, the sources said unknown
        return []


def said(p: dict, done: bool) -> list[str]:
    verb = "moved" if done else "would move"
    lines = [f"{p['from']}/ {verb} to {p['to']}/ ({p['how']}, {len(p['files'])} files, its code unchanged)",
             *[f"[[{e['section']}]] {e['name']} ({'orchestra.toml' if e['target'] == REPO else 'the owner overlay'}): "
               f"{e['command']} -> {e['new_command']}" for e in p["entries"]]]
    if p["manifest"]:
        lines.append(f"{p['manifest']['file']}: {p['manifest']['what']}")
    lines += [f"{w['path']}: written (the module's runner)" for w in p["writes"]]
    lines.append("everything else in .orchestra/ stays where it is")
    if p["code_names_old_path"]:
        lines.append("its code names the old path (check after the move): " + "; ".join(p["code_names_old_path"][:3]))
    lines += p["notes"]
    return lines


def verify(p: dict) -> str:
    who = ", ".join(p["sources"]) if p["sources"] else f"the machine {p['name']} runs as (not heard on the bus yet)"
    return (f"Verify: after the launcher restarts it on its new command (within a second of reading the configuration), "
            f"{who} is heard again under the same source with the same events (machine_ages: graduated); if not, "
            f"read_machine_log {p['entries'][0]['name']}.")


def _move(project: Project, p: dict) -> None:
    src, dst = project.repo_root / p["from"], project.repo_root / p["to"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    if p["how"] == "git mv":
        r = _git(project.repo_root, "mv", p["from"], p["to"])
        if r.returncode != 0:
            raise StoreError(f"git mv failed: {(r.stderr or r.stdout).strip()}; nothing was moved")
    else:
        shutil.move(str(src), str(dst))


def _unmove(project: Project, p: dict) -> None:
    if p["how"] == "git mv":
        _git(project.repo_root, "mv", p["to"], p["from"])
    else:
        shutil.move(str(project.repo_root / p["to"]), str(project.repo_root / p["from"]))


def _set_command(project: Project, store: AgentStore, e: dict, reason: str) -> str:
    entry = {"name": e["name"], "command": e["new_command"]}
    if e["target"] == OVERLAY:
        done = change(project, lambda ed, kw: ed.add_entry(e["section"], entry, **kw),
                      lambda c: f"changed [[{e['section']}]] {e['name']}'s command (overlay)", reason, OVERLAY)
        what = done["changed"]
    else:                     # orchestra.toml: written, carried with the move (the module's docstring)
        ed = project.orchestra.config_edit
        try:
            c = ed.add_entry(e["section"], entry, target=REPO, dry_run=True)
            no_secret(c.before or "", c.after)
            ed.apply(c)
        except ConfigError as err:
            raise StoreError(str(err)) from err
        project.reload_config()
        what = f"changed [[{e['section']}]] {e['name']}'s command in orchestra.toml (carried with the move on orchestra/supervisor)"
    store.events.emit(INSTRUMENTS_CHANGED if e["section"] in INSTRUMENT_SECTIONS else CHANGED, section=e["section"],
                      change=what, reason=reason, commit=None, target=e["target"],
                      applies="orchestra run reads it within a second", by=MACHINE, src=MACHINE)
    return what


def _same(p: dict) -> str:
    """The plan as the owner saw it, but who is heard (that may change between the dry run and the yes)."""
    return json.dumps({k: v for k, v in p.items() if k != "sources"}, sort_keys=True)


def graduate_machine(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    p = make_plan(project, store, a)
    dry = a.get("dry_run") is not False
    if dry:
        store.update_agent_state(MACHINE, KEY, lambda d: {**(d or {}), p["name"]: {"plan": p, "at": time.time()}})
        return {"ok": True, "dry_run": True, "plan": p, "said": said(p, False), "verify": verify(p),
                "next": ("Put the plan to the owner in a sentence or two (where it goes, its new command, that the "
                         "move and orchestra.toml become one commit on orchestra/supervisor and its pull request). On their "
                         "yes, quoted, call graduate_machine again with dry_run false.")}
    if prov["author"] != OWNER:
        raise StoreError("moving a machine is a change to the system and the owner's: quote their yes from this turn "
                         "(quote), after a dry run they saw; nothing was moved")
    shown = ((store.agent_state(MACHINE).get(KEY) or {}).get(p["name"]) or {}).get("plan")
    if shown is None:
        raise StoreError(f"no dry run of {p['name']}'s graduation to put to the owner: call graduate_machine with dry_run "
                         "first; nothing was moved")
    if _same(shown) != _same(p):
        raise StoreError("the plan changed since the dry run the owner saw (the configuration or the tree changed): "
                         "dry-run it again and put it to them; nothing was moved")
    _move(project, p)
    try:
        for w in p["writes"]:
            f = project.repo_root / w["path"]
            if not (w.get("if_missing") and f.exists()):
                f.write_text(w["text"], encoding="utf-8")
        wrote = wiring.apply_manifest(project.repo_root, p["manifest"])
        changed = [_set_command(project, store, e, prov["reason"]) for e in p["entries"]]
    except (StoreError, OSError) as err:
        _unmove(project, p)
        raise StoreError(f"{err}; the directory was moved back to {p['from']}/") from err
    carried = _carry(project, store, p, prov)
    rec = {"plan": p, "at": time.time(), "graduated": True, "quote": prov.get("quote"), "turn": prov.get("turn"),
           "commit": carried.get("commit")}
    store.update_agent_state(MACHINE, KEY, lambda d: {**(d or {}), p["name"]: rec})
    told = [*said(p, True), *([wrote] if wrote else []), *changed, carried["said"]]
    return {"ok": True, "dry_run": False, "plan": p, "said": told, "branch": carried["branch"],
            "commit": carried.get("commit"), "pull_request": carried["pull_request"],
            "verify": verify(p), "next": "Tell the owner where it is now, its command, and the branch and pull request; then check it is heard again "
                                         "(machine_ages) and say so."}


def _carry(project: Project, store: AgentStore, p: dict, prov: dict) -> dict:
    """The graduation as one commit on the Supervisor's branch, and its pull request."""
    paths = [p["from"], p["to"], *([p["manifest"]["file"]] if p["manifest"] else []), *[w["path"] for w in p["writes"]],
             *([project.orchestra.config_file.name] if any(e["target"] == REPO for e in p["entries"]) else [])]
    title = f"Supervisor: graduate {p['name']} to {p['to']}/"
    try:
        sha = side_branch.commit(project, f"{title}\n\n{prov['reason']}\n\nThe owner: {prov.get('quote') or ''}\n",
                                 paths=list(dict.fromkeys(paths)))
    except side_branch.SideBranchError as e:
        return {"branch": side_branch.SIDE, "commit": None, "pull_request": None,
                "said": f"the move is made and live, but its commit on {side_branch.SIDE} failed ({e}): tell the owner"}
    return {"commit": sha, **supervisor_pr.carried(
        project, store, sha, title=title, reason=prov["reason"], quote=prov.get("quote"),
        verified=(f"the owner's yes on the dry run, worked out again unchanged; {len(p['files'])} files moved unchanged; "
                  "Orchestra's configuration checks passed on each command"))}


COMMANDS = {"graduate_machine": graduate_machine}
