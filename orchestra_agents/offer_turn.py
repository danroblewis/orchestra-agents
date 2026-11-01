"""One turn on an offer of work, whoever made the offer: the task system's assignment (runner.py)
or the Workflows floor's (floor_runner.py). Both claim and end the turn in their own words; what
they share is here: the heartbeat that carries what the agent is saying now, and the session's
turn itself (opened, prompted with one nudge when it stopped without its report, closed, its token
revoked and its record set idle)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from . import access
from .harness import AgentSession

SAY_S = 2.0            # a heartbeat goes out early when what the agent is saying has changed, at most this often


def heartbeat(host, sid: str, done: threading.Event, stopped: Callable[[], bool], every_s: float,
              say: Callable[[str], None]) -> threading.Thread:
    """``say(text)`` at least every ``every_s`` (polled at most ``SAY_S`` apart, so a lease is never
    missed by more), and sooner when the agent's visible text changed, until ``done`` is set or
    ``stopped()``: the lease goes on, and whoever shows the work can show what the agent is doing."""
    poll = max(min(SAY_S, every_s / 2), 0.05)

    def beat():
        said, since = "", time.time()
        while not done.wait(poll) and not stopped():
            s = host.store.find_session(sid) or {}
            said_lines = [x for x in (s.get("partial_text") or s.get("last_text") or "").splitlines() if x.strip()]
            text = (said_lines[-1].strip() if said_lines else "")[-160:]          # offer.alive's text is one line
            if text != said or time.time() - since >= every_s - poll:
                said, since = text, time.time()
                say(text)
    th = threading.Thread(target=beat, daemon=True, name=f"{sid}:beat")
    th.start()
    return th


def run_turn(host, sid: str, role: str, key: str, open_session: Callable[[], AgentSession], procs: dict, slot: str,
             task: str, nudge: str, released: Callable[[], str | None], fields: dict,
             taken_by: str | None = None) -> tuple[dict | None, str, bool]:
    """The session's turn on the offer: (the report its exit tool left under ``key``, its last text,
    whether the turn failed). ``procs[slot]`` is the open session while it runs, for whoever must
    kill it (a release, a withdrawal); ``released()`` says why it was taken back, if it was, and
    then no prompt is sent, and ``taken_by`` names who took it back in the transcript (by default the
    task system). ``fields`` go on the session's record for the cards."""
    text = ""
    proc = procs[slot] = open_session()
    try:
        if host.residents.trim_if_large(proc, sid, role) == "rotated":
            proc = procs[slot] = open_session()
        if released():
            proc.kill()                               # taken back while the session was opening: nothing was there to kill
        host._session_running(sid, role, proc, key)
        host.store.set_session(sid, **fields)
        report, text, failed = host.turns.prompt_with_retry(proc, key, task, nudge, released=released,
                                                            **({"taken_by": taken_by} if taken_by else {}))
        return report, text, failed
    finally:
        proc.close()
        access.revoke(host.store, sid)                # its token ends with it
        host._session_idle(sid, text, release=False)  # the slot is the caller's to give back, or keep
