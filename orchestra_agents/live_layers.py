"""What keeps a Supervisor change live until its pull request is merged (the coordinator,
2026-11-02: "used live" is the private layer, "shared" is the pull request; the checkout is never
touched).

- A change to ``orchestra.toml`` is also made in the owner's overlay (``config_change``), which
  Orchestra reads at once. Each such overlay change is kept here, paired with the branch's commit
  (``<state>/agents/live_overlay.json``: the overlay's text before and after, as Orchestra's
  ``Change``), and **retired** once the repository's ``orchestra.toml`` in the checkout gives the
  same configuration without it (the pull request merged and pulled): the overlay is put back as it
  was before, newest first, only while it still holds what the change wrote (an owner's later edit
  keeps it, and is theirs to tidy).
- A spec change's live copy is in the state directory (``spec_git.retire_live`` retires it).
- A graduation's command in the owner's overlay waits here (``graduations``) until the moved program
  is in the checkout, then is changed through ``config_edit``.

``settle`` does all three; the review round, the digest and every proposal call it."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from orchestra.config import ConfigError
from orchestra.config_edit import Change
from orchestra.overlay import OVERLAY, REPO, OverlayError, merge

from . import spec_git
from .config import Project

FILE = "live_overlay.json"


def _path(project: Project) -> Path:
    return project.state_dir / FILE


def read(project: Project) -> dict:
    try:
        d = json.loads(_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"pairs": list(d.get("pairs") or []), "graduations": list(d.get("graduations") or [])}


def _write(project: Project, d: dict) -> None:
    p = _path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(p)


def keep(project: Project, c: Change, commit: str, what: str) -> None:
    """An overlay change made to keep a branch's change live."""
    d = read(project)
    d["pairs"].append({"change": c.to_dict(), "commit": commit, "what": what})
    _write(project, d)


def wait_graduation(project: Project, entry: dict, dest: str, commit: str) -> None:
    """An overlay entry's new command, to change once ``dest`` is in the checkout."""
    d = read(project)
    d["graduations"].append({"entry": entry, "dest": dest, "commit": commit})
    _write(project, d)


def _live(entries):
    """An array of tables without the entries a layer switched off: what runs."""
    return [e for e in entries if not (isinstance(e, dict) and e.get("enabled") is False)]


def _norm(v):
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, list) and v and all(isinstance(e, dict) for e in v):
        return [_norm(e) for e in _live(v)]
    return v


def redundant(project: Project, c: Change) -> bool:
    """Whether the checkout's orchestra.toml now gives the same configuration without the overlay change."""
    try:
        repo = tomllib.loads(project.orchestra.config_edit.text(REPO) or "")
        before = merge(repo, tomllib.loads(c.before or ""))[0]
        after = merge(repo, tomllib.loads(c.after or ""))[0]
    except (tomllib.TOMLDecodeError, OverlayError):
        return False
    return _norm(before) == _norm(after)


def settle(project: Project) -> list[str]:
    """Retire what the checkout now holds; what was retired or applied, one line each."""
    said = [f"the live copy of {n}'s spec retired: the checkout has it" for n in spec_git.retire_live(project)]
    d = read(project)
    ed = project.orchestra.config_edit
    while d["pairs"]:
        c = Change.from_dict(d["pairs"][-1]["change"])
        if ed.text(OVERLAY) != c.after or not redundant(project, c):
            break
        try:
            if not ed.restore(c):
                break
        except ConfigError:
            break
        said.append(f"the overlay's copy of {d['pairs'][-1]['what']} retired: orchestra.toml has it")
        d["pairs"].pop()
    waiting = []
    for g in d["graduations"]:
        if not (project.repo_root / g["dest"]).is_dir():
            waiting.append(g)
            continue
        try:
            ed.add_entry(g["entry"]["section"], {"name": g["entry"]["name"], "command": g["entry"]["command"]},
                         target=OVERLAY)
            said.append(f"[[{g['entry']['section']}]] {g['entry']['name']}'s command in the overlay is now "
                        f"{g['entry']['command']}: {g['dest']}/ is in the checkout")
        except ConfigError:
            waiting.append(g)
    d["graduations"] = waiting
    if said:
        _write(project, d)
        project.reload_config()
    return said
