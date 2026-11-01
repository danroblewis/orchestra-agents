"""The host stops with the ``orchestra run`` that launched it. A stop sent to it (SIGTERM) is
handled in ``host.run``; this is for the launcher that never sent one, because it was killed
(SIGKILL, a crash, an OOM): the host is then reparented (to init, pid 1, or to a subreaper), and
left alone it would keep its sessions running unseen until the next ``orchestra run`` reaps it.

``ParentWatch`` remembers the parent the host started under and says the parent is gone when the
current one differs (the host was reparented) or that pid is no longer alive. ``watch`` polls it
every ``PARENT_POLL_S`` on a daemon thread and calls back once."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable

from orchestra.runtime import alive

PARENT_POLL_S = 2.0


class ParentWatch:
    def __init__(self, getppid: Callable[[], int] = os.getppid, is_alive: Callable[[int], bool] = alive):
        self._getppid, self._alive = getppid, is_alive
        self.parent = getppid()

    def watching(self) -> bool:
        """Whether there is a parent to watch: one started under init (pid 1) already has none."""
        return self.parent > 1

    def gone(self) -> bool:
        if not self.watching():
            return False
        return self._getppid() != self.parent or not self._alive(self.parent)


def watch(on_gone: Callable[[int], None], stop: threading.Event, every_s: float = PARENT_POLL_S,
          parent: ParentWatch | None = None) -> threading.Thread | None:
    """Poll the parent until ``stop`` is set; when it is gone, call ``on_gone(its pid)`` once and end."""
    pw = parent or ParentWatch()
    if not pw.watching():
        return None

    def loop() -> None:
        while not stop.wait(every_s):
            if pw.gone():
                on_gone(pw.parent)
                return
    th = threading.Thread(target=loop, name="parent-watch", daemon=True)
    th.start()
    return th
