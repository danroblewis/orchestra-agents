"""The AI agents' side of assignment (protocol.py): answers ``task.assigned`` for the roles it
serves (worker, reviewer) by running an agent session on the ticket, says it is still working
while the turn runs, with what the agent is saying now, and reports what the agent reported.

It runs in the agents' host (host.py) and uses its sessions; it knows the task system only
through the bus and the assignment (its brief); the task system applies every transition."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from . import prompting as agents
from .builtins import PER_TICKET_ROLES
from .builtins import ROLES as BUILTIN
from .harness import AgentSession
from .prompting import TicketView
from .protocol import (
    ACCEPTED,
    ASSIGNED,
    HEARTBEAT_S,
    RELEASED,
    REPORTED,
    TERMINAL,
    TRANSITIONED,
    WORKING,
    Follower,
)

SAY_S = 2.0            # a heartbeat goes out early when what the agent is saying has changed, at most this often


class AgentRunner:
    def __init__(self, host):
        self.host = host
        self.events = host.events
        self.follow = Follower(host.events)
        self.threads: dict[str, threading.Thread] = {}      # assignment -> the thread running it
        self.sessions: dict[str, str] = {}                   # assignment -> its session id
        self.instances: dict[str, str] = {}                  # assignment -> the instance it runs as
        self.procs: dict[str, AgentSession] = {}             # assignment -> its session, from the moment it is opened
        self.released: dict[str, str] = {}                   # assignment -> why the task system took it back

    def busy(self) -> bool:
        return any(th.is_alive() for th in self.threads.values())

    def tick(self) -> None:
        for aid in [a for a, th in self.threads.items() if not th.is_alive()]:
            self.threads.pop(aid)
        for ev in self.follow.new():
            serves = [r for r in PER_TICKET_ROLES if r in (self.host.cfg.serves or [])]   # [agents] serves: another machine may take a role
            if ev.get("type") == ASSIGNED and ev.get("role") in serves and ev.get("assignment") not in self.threads:
                aid, role, tid = ev["assignment"], ev["role"], ev["ticket"]
                sid = f"{role}-{tid}"
                instance = self.host._instance(sid, role, tid)       # the slot is taken here, on the one thread that allocates them
                self.sessions[aid], self.instances[aid] = sid, instance
                th = threading.Thread(target=self._run, args=(ev, sid, instance), daemon=True, name=aid)
                self.threads[aid] = th
                th.start()
            elif ev.get("type") == TRANSITIONED and ev.get("to") in TERMINAL and ev.get("ticket"):
                self.host.store.purge_ticket_sessions(ev["ticket"])     # a finished ticket's sessions go to the archive
            elif ev.get("type") == RELEASED and ev.get("assignment") in self.sessions \
                    and ev.get("instance") in (None, self.instances.get(ev["assignment"])):     # a release for another machine is not ours
                self.released[ev["assignment"]] = ev.get("reason") or "no reason given"
                proc = self.procs.get(ev["assignment"])
                if proc is not None:
                    try:
                        proc.kill()                           # the turn ends; turns.py records the interruption
                    except OSError:
                        pass

    def _run(self, a: dict, sid: str, instance: str) -> None:
        """One assignment: accept, run the session's turn with heartbeats, report. A release
        that arrives before the session is open skips the turn; one that arrives during it
        kills the turn (tick) or ends it before the nudge (turns.prompt_with_retry)."""
        host = self.host
        aid, role, tid = a["assignment"], a["role"], a["ticket"]
        path = Path(a["workspace"])
        say = lambda kind, **data: self.events.publish(kind, {"assignment": aid, "ticket": tid, "role": role, **data},
                                                       source=instance, subject=tid)
        say(ACCEPTED, instance=instance, session=sid)
        done = threading.Event()

        def beat():
            """``task.working`` every HEARTBEAT_S, and sooner when the agent's visible text changed:
            the lease goes on, and whoever shows the ticket can show what the agent is doing."""
            said, since = "", time.time()
            while not done.wait(SAY_S) and aid not in self.released:
                s = host.store.find_session(sid) or {}
                text = (s.get("partial_text") or s.get("last_text") or "")[-160:]
                if text != said or time.time() - since >= HEARTBEAT_S:
                    said, since = text, time.time()
                    say(WORKING, text=text)
        threading.Thread(target=beat, daemon=True, name=f"{aid}:beat").start()
        report, text, failed, commit = None, "", True, None
        spec = BUILTIN[role]
        open_session = lambda: host._open_session(sid, role, agents.role_prompt(host.project, spec.prompt), tuple(spec.tools), path, tid)
        try:
            t = TicketView({**(a.get("brief") or {}), "id": tid, "branch": a.get("branch")})
            if role == "worker":
                task, nudge = self._work_task(t, a)
            else:
                task, nudge, commit = self._review_task(t, a, path)
            if aid in self.released:
                return                                        # taken back before the turn began: no session, nothing to mark
            proc = self.procs[aid] = open_session()
            try:
                if host.residents.trim_if_large(proc, sid, role) == "rotated":
                    proc = self.procs[aid] = open_session()
                if aid in self.released:
                    proc.kill()                               # released while the session was opening: tick found nothing to kill
                host._session_running(sid, role, proc, tid)
                host.store.set_session(sid, ticket_title=t.title, ticket_state="IN_PROGRESS" if role == "worker" else "IN_REVIEW",
                                       worker_turns=(a.get("brief") or {}).get("worker_turns") or 0)   # for the cards: no query needed
                report, text, failed = host.turns.prompt_with_retry(proc, tid, task, nudge, released=lambda: self.released.get(aid))
            finally:
                proc.close()
                host._session_idle(sid, text)
        except Exception as e:  # noqa: BLE001 - whatever happened, the scheduler hears how it ended
            text = f"{type(e).__name__}: {e}"
        finally:
            done.set()
            self.sessions.pop(aid, None)
            self.instances.pop(aid, None)
            self.procs.pop(aid, None)
            if self.released.pop(aid, None) is None:      # a released assignment is no longer this instance's to report on
                say(REPORTED, report=report, failed=failed, text=(text or "")[-2000:], commit=commit)
            host._release(sid)                            # the slot, whether or not the session ever opened

    def _work_task(self, t: TicketView, a: dict) -> tuple[str, str]:
        if a.get("branch") is None:                           # a task: a folder and a result
            return (agents.worker_task_tasks(self.host.project, t),
                    ("You stopped without reporting. Call `report_ticket` now: READY_FOR_REVIEW if your result is written, "
                     "otherwise BLOCKED with the reason."))
        return (agents.worker_task(self.host.project, t, a.get("unit_command")),
                ("You stopped without reporting. Call `report_ticket` now: READY_FOR_REVIEW if your work is "
                 "committed and tested, otherwise BLOCKED with the reason."))

    def _review_task(self, t: TicketView, a: dict, path: Path) -> tuple[str, str, str | None]:
        nudge = "You stopped without a verdict. Call `report_review` now with APPROVED or REJECTED."
        review = a.get("review") or {}
        if a.get("branch") is None:                           # a task: the worker's result in its folder
            files = sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())[:200]
            result = (path / "RESULT.md").read_text(encoding="utf-8", errors="replace") if (path / "RESULT.md").exists() else ""
            return agents.reviewer_task_tasks(t, review.get("summary") or "", files, result), nudge, None
        # code: the task system ran the tests (verifying is its job) and names the commit; the diff is read here
        base, commit = self.host.cfg.base_branch, review.get("commit") or "HEAD"
        git = lambda *args: subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=False).stdout
        task = agents.reviewer_task(self.host.project, t, commit, git("diff", f"{base}...{commit}"),
                                    git("diff", "--name-only", f"{base}...{commit}").split(), git("log", "--oneline", f"{base}..{commit}"),
                                    review.get("test_output"))
        return task, nudge, commit
