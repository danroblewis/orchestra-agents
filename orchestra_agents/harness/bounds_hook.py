"""Claude Code's PreToolUse hook for a bounded session (``bounds.py``): a read by Read, Grep, Glob or
NotebookRead outside the session's allowed directories is denied, with the reason.

Run as ``python -I bounds_hook.py <roots.json>`` (standard library only, so it starts fast and
loads nothing from the session's working directory). ``roots.json`` lists the directories the
session may read in: the project's tree, Orchestra's state directory for it, the installed
packages (to read a plugin before overriding it), Claude Code's own directory for the session's
saved tool output, and the temporary directory. The hook reads the tool's input on stdin and prints
a deny decision, or nothing (the call goes on). Bash is not checked here: its commands are bounded
by the deny rules the settings carry, and by the prompt."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PATH_KEYS = {"Read": ("file_path",), "NotebookRead": ("notebook_path",), "Grep": ("path",), "Glob": ("path", "pattern")}


def _inside(p: Path, roots: list[Path]) -> bool:
    for r in roots:
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def _paths(tool: str, args: dict, cwd: Path) -> list[Path]:
    out = []
    for k in PATH_KEYS.get(tool, ()):
        v = args.get(k)
        if not isinstance(v, str) or not v.strip():
            continue
        if k == "pattern" and not v.startswith(("/", "~", "..")):
            continue                                  # a relative glob is under the search path
        if k == "pattern":
            v = v.split("*", 1)[0] or "/"            # the fixed part of an absolute glob
        p = Path(os.path.expanduser(v))
        out.append((p if p.is_absolute() else cwd / p).resolve())
    return out


def decide(event: dict, roots: list[str]) -> dict | None:
    """The hook's answer for one tool call: a deny decision, or None to let it go on."""
    tool = str(event.get("tool_name") or "")
    if tool not in PATH_KEYS:
        return None
    allowed = [Path(r).resolve() for r in roots]
    cwd = Path(str(event.get("cwd") or os.getcwd()))
    for p in _paths(tool, event.get("tool_input") or {}, cwd):
        if not _inside(p, allowed):
            why = (f"{p} is outside what this session may read: the project's tree and Orchestra's state directory "
                   "for it. Read nothing outside them (no home directory, no other repository, no credential store); "
                   "ask the owner instead.")
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": why}}
    return None


def main(argv: list[str]) -> int:
    try:
        roots = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        event = json.loads(sys.stdin.read() or "{}")
    except (IndexError, OSError, ValueError) as e:
        print(f"the session's bounds could not be read ({e}): the read is refused", file=sys.stderr)
        return 2                                      # fail closed
    got = decide(event, roots)
    if got:
        print(json.dumps(got))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
