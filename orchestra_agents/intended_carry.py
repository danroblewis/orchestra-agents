"""Carrying the stand-in's declarations into Orchestra's declared layer, once (``intended_api``).

On the first use of Orchestra's layer, a stand-in journal left from before
(``<state>/agents/intended/journal.jsonl``) is replayed into it, call by call, each with its
provenance (less ``via`` and ``at``, which Orchestra's store sets), and the stand-in's directory is
then renamed ``intended.replayed.<time>``. The check and the replay hold one file lock
(``intended.carry.lock`` in the agents' state directory), so the MCP server's process and the
host's tick never both replay. The last call replayed is recorded (``carried.json`` in the
stand-in's directory), so a replay that stopped part way resumes from the next call instead of
refusing.

When Orchestra's layer already holds declarations that did not come from this replay, every call
is refused until the owner says which to keep: the Supervisor's ``carry_over`` tool, on the owner's
quoted words, either **finishes** it (replays the rest on top of what Orchestra holds) or
**abandons** it (the stand-in is archived as ``intended.abandoned.<time>``, replayed nowhere). The
owner never touches a file. Whether a call that answered a request on the stand-in can be carried
(Orchestra checks requests against its own notes) is the coordinator's open question (c): such a
call stops the replay with Orchestra's refusal, said as it is."""

from __future__ import annotations

import fcntl
import json
import time
from contextlib import contextmanager

from .config import Project
from .store import StoreError

FINISH, ABANDON = "finish", "abandon"


def _dir(project: Project):
    return project.state_dir / "intended"


@contextmanager
def _locked(project: Project):
    project.state_dir.mkdir(parents=True, exist_ok=True)
    with (project.state_dir / "intended.carry.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _journal(project: Project) -> list[dict]:
    try:
        text = (_dir(project) / "journal.jsonl").read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def _carried(project: Project) -> int | None:
    try:
        return int(json.loads((_dir(project) / "carried.json").read_text(encoding="utf-8"))["seq"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _calls(n: int) -> str:
    return f"{n} call{'' if n == 1 else 's'}"


def _replay(project: Project, core, lines: list[dict], after: int) -> None:
    for line in lines:
        if line.get("seq", 0) <= after:
            continue
        prov = {k: v for k, v in (line.get("provenance") or {}).items() if k not in ("via", "at")}
        try:
            core.apply(line.get("sent") or line.get("ops") or [], prov)
        except StoreError as e:
            raise StoreError(f"carrying the stand-in's declarations into Orchestra's layer stopped at call {line.get('seq')}: "
                             f"{e}. Orchestra's layer now holds the calls before it; the next call resumes from this one, or "
                             "the owner says to abandon the rest (carry_over)") from None
        (_dir(project) / "carried.json").write_text(json.dumps({"seq": line["seq"], "at": time.time()}), encoding="utf-8")
    _dir(project).rename(_dir(project).with_name(f"intended.replayed.{int(time.time())}"))


def carry_over(project: Project, core, how: str | None = None) -> str | None:
    """Replay what is left of the stand-in into Orchestra's layer (``core``); ``how`` the owner's
    answer when both hold declarations (``finish`` or ``abandon``). What was done, or None."""
    with _locked(project):
        lines = _journal(project)
        if not lines:
            return None
        after = _carried(project)
        if how == ABANDON:
            _dir(project).rename(_dir(project).with_name(f"intended.abandoned.{int(time.time())}"))
            return f"abandoned: the stand-in's {_calls(len(lines))} archived, none replayed"
        if after is None and core.read().get("version") and how != FINISH:
            raise StoreError(f"both Orchestra's declared layer and the agents' stand-in ({_dir(project)}) hold declarations, so "
                             "neither is written: ask the owner whether to carry the stand-in's into Orchestra's on top of what "
                             "it holds or to drop them, and then call carry_over with their words")
        _replay(project, core, lines, after or 0)
        return f"carried: {_calls(sum(1 for x in lines if x.get('seq', 0) > (after or 0)))} replayed into Orchestra's layer"


def carry_over_tool(project: Project, store, a: dict, prov: dict) -> dict:
    """The Supervisor's ``carry_over`` (the owner's answer when both layers hold declarations)."""
    from . import intended_api
    how = a.get("how")
    if how not in (FINISH, ABANDON):
        raise StoreError("how is finish (replay the stand-in's calls into Orchestra's layer) or abandon (archive them)")
    if prov.get("author") != "owner":
        raise StoreError("which declarations to keep is the owner's to say: quote their words")
    if not intended_api.has_core(project):
        raise StoreError("this Orchestra has no declared layer of its own: there is nothing to carry the stand-in into")
    said = carry_over(project, intended_api.Core(project), how) or "nothing to carry: the stand-in holds no call"
    return {"ok": True, "said": [said], "kept_in": intended_api.where(project)}


COMMANDS = {"carry_over": carry_over_tool}
