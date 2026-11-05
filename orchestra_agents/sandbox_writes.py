"""A write the tool command makes under Orchestra's state directory for the project
(``~/.orchestrator/<key>/``: the event log, the agents' records), refused by Claude Code's sandbox.

Sandboxed commands write only to the working directory and the temporary one unless
``sandbox.filesystem.allowWrite`` lists a path (https://code.claude.com/docs/en/sandboxing,
"Configure sandboxing"); the Claude Code harness lists exactly that state directory in the
session's settings (``harness/sandbox.py``). When the write is refused anyway (an organisation's
managed settings deny it), the tool command answers with ``MARK`` and the path to ask IT for: macOS
says ``Operation not permitted``, Linux ``Read-only file system``."""

from __future__ import annotations

import errno
import json
from pathlib import Path

MARK = "Claude Code's sandbox blocked a write to Orchestra's state directory"
REFUSED = (errno.EPERM, errno.EACCES, errno.EROFS)


def setting(state_dir: str | Path) -> str:
    return f'"sandbox": {{"filesystem": {{"allowWrite": [{json.dumps(str(state_dir))}]}}}}'


def words(state_dir: str | Path, path: str | Path) -> str:
    return (f"{MARK} {state_dir} ({path}). Never rerun this command outside the sandbox: stop, and tell the owner that "
            f"Claude Code's sandbox needs {setting(state_dir)}. Orchestra's harness sets it for the session; when it is "
            f"blocked anyway, the organisation's managed settings refuse the path and only IT can allow it there.")


def _chain(e: BaseException | None):
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        yield e
        e = e.__cause__ or e.__context__


def blocked(e: BaseException, state_dir: str | Path | None) -> str | None:
    """The words for ``e`` when it is (or was caused by) a refused write under ``state_dir``; else None."""
    if state_dir is None:
        return None
    root = Path(state_dir).resolve()
    for x in _chain(e):
        if isinstance(x, OSError) and x.errno in REFUSED and x.filename:
            p = Path(str(x.filename)).resolve()
            if p == root or root in p.parents:
                return words(root, p)
    return None
