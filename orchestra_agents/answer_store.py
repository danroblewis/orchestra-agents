"""Large tool answers kept out of the session's context (the owner, 2026-11-04: cut the per-call
context; a live run on Pi carried about 140,000 tokens a model call). Every model call carries every
earlier answer of the session, so an answer over ``TRIM`` characters is not given whole: the session
reads its start, a summary of its shape (each top-level field and its size) and a pointer to the
whole text, kept in the state directory, which ``read_answer`` reads part by part, one ``TRIM`` piece
at a time (a read tool's own ``page`` gives the same parts). A 20 KB survey then costs its first
piece in every later call, not all of it.

Only a session that holds ``read_answer`` (``caller.Caller``) is trimmed; any other keeps the bound of
``answers.cut``. Kept answers are pruned to the newest ``KEEP``."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .answers import NOTE_ROOM, pages
from .store import StoreError

TRIM = 6_000             # characters: the most of an answer a session that can page reads at once
KEEP = 200               # kept answers, newest first
ID = re.compile(r"a-[0-9a-f]{12}")
SUMMARY_KEYS = 24        # top-level fields named in a summary


def _dir(project) -> Path:
    return project.state_dir / "answers"


def keep(project, text: str) -> str:
    """Keep the whole answer; its id."""
    aid = "a-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    d = _dir(project)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{aid}.txt"
    if not p.exists():
        p.write_text(text, encoding="utf-8")
    old = sorted(d.glob("a-*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)[KEEP:]
    for f in old:
        f.unlink(missing_ok=True)
    return aid


def summary(text: str) -> str:
    """The answer's shape in a line: each top-level field of a JSON object and how big it is."""
    try:
        v = json.loads(text)
    except ValueError:
        return f"{len(text.splitlines()):,} lines"
    if isinstance(v, list):
        return f"a list of {len(v):,} items"
    if not isinstance(v, dict):
        return type(v).__name__

    def size(x) -> str:
        if isinstance(x, list):
            return f"{len(x):,} items"
        if isinstance(x, dict):
            return f"{len(x):,} fields"
        if isinstance(x, str):
            return repr(x) if len(x) <= 40 else f"{len(x):,} chars"
        return json.dumps(x)
    keys = list(v)
    shown = ", ".join(f"{k}: {size(v[k])}" for k in keys[:SUMMARY_KEYS])
    return "fields " + shown + (f", and {len(keys) - SUMMARY_KEYS} more" if len(keys) > SUMMARY_KEYS else "")


def piece(text: str, aid: str, tool: str, page: int = 1) -> str:
    """Page ``page`` of the kept answer, with the line that says what was left out and how to read on."""
    parts = pages(text, TRIM - NOTE_ROOM)
    if page < 1 or page > len(parts):
        raise StoreError(f"the answer {aid} has {len(parts)} part{'s' if len(parts) != 1 else ''}; there is no part {page}")
    head = f"[{tool}'s answer, {len(text):,} characters, trimmed to part {page} of {len(parts)}; {summary(text)}]\n" if page == 1 else ""
    if page == len(parts):
        return head + parts[-1] + f"\n[answer {aid}: part {page} of {len(parts)}, the last.]"
    left = len(text) - sum(len(p) for p in parts[:page])
    return (head + parts[page - 1] + f"\n[answer {aid}: part {page} of {len(parts)}; {left:,} more characters kept. "
            f"Read on only for what you need: read_answer with id {aid} and part {page + 1}, or a narrower call.]")


def read(project, a: dict) -> str:
    """``read_answer``: a part of an answer kept earlier."""
    aid = (a.get("id") or "").strip()
    if not ID.fullmatch(aid):
        raise StoreError(f"{aid!r} is not an answer's id; a trimmed answer's last line names it (a-…)")
    p = _dir(project) / f"{aid}.txt"
    if not p.exists():
        raise StoreError(f"no answer {aid} is kept (the newest {KEEP} are): call the tool again")
    try:
        page = int(a.get("part") or 1)
    except (TypeError, ValueError):
        raise StoreError("part is a number, 2 for the second") from None
    return piece(p.read_text(encoding="utf-8"), aid, "the kept", page)
