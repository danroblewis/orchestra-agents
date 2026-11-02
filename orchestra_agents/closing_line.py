"""The line a "what changed since" reply ends on (T2f s16: the model line came second to last, after
a note on the tally). ``read_events`` with ``since`` answers ``closing_line`` (its ``model`` line)
and the prompt says the reply's last line is that line verbatim; the host, after a Supervisor turn
that read it, appends it when the reply does not end on it (``host._closed``), as Orchestra's, not
the model's. The tool records the line it gave in the Supervisor's state (``record``), with when."""

from __future__ import annotations

import re
import time

from .protocol import SUPERVISOR

_MARKS = re.compile(r"[*_`>#]+|^\s*[-•]\s*")


def _plain(text: str) -> str:
    return " ".join(_MARKS.sub("", text).split()).rstrip(".").lower()


def record(store, line: str) -> None:
    store.set_agent_state(SUPERVISOR, closing_line=line, closing_line_at=time.time())


def due(store, began: float) -> str | None:
    """The closing line a read in the turn that began at ``began`` gave, or None."""
    st = store.agent_state(SUPERVISOR)
    line = st.get("closing_line")
    return line if line and float(st.get("closing_line_at") or 0) >= began else None


def ends_on(reply: str, line: str) -> bool:
    """Whether the reply's last line carries the closing line (markdown and a final stop aside)."""
    last = next((x for x in reversed((reply or "").splitlines()) if x.strip()), "")
    return bool(_plain(line)) and _plain(line) in _plain(last)
