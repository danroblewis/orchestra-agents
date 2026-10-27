"""Closing sessions together at shutdown, so the host's stop takes the longest close rather than
the sum of them (and stays within the stop grace it declares to Orchestra, web.STOP_GRACE_S)."""

from __future__ import annotations

import threading
from collections.abc import Callable

CLOSE_S = 5.0     # the longest a harness's close waits for its process before killing it (harness/*.py)


def close_together(sessions: list, failed: Callable[[object, Exception], None]) -> None:
    """Close every session, each in a thread of its own; ``failed(session, error)`` hears of a
    close that raised, and the rest close regardless."""
    def close(s) -> None:
        try:
            s.close()
        except Exception as e:  # noqa: BLE001 - shut the rest down regardless
            failed(s, e)
    threads = [threading.Thread(target=close, args=(s,), daemon=True) for s in sessions]
    for th in threads:
        th.start()
    for th in threads:
        th.join(CLOSE_S + 5.0)
