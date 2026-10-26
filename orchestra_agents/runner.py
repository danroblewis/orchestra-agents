"""The AI agents' side of assignment: answers ``task.assigned`` for the roles it serves (worker,
reviewer) by running an agent session on the ticket, says it is still working while the turn
runs, and reports what the agent reported (the task system's side: orchestra-tasking, tasking/assignments.py).

It runs in the agents' host (host.py) and uses its sessions; it knows the task system only
through the bus and the assignment (its brief); the task system applies every transition."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from . import prompting as agents
from .prompting import TicketView
from .protocol import ACCEPTED, ASSIGNED, HEARTBEAT_S, RELEASED, REPORTED, WORKING, Follower


class AgentRunner:
    ROLES = ("worker", "reviewer")

    def __init__(self, loop):
        self.loop = loop
        self.events = loop.events
        self.follow = Follower(loop.events)
        self.threads: dict[str, threading.Thread] = {}      # assignment -> the thread running it
        self.sessions: dict[str, str] = {}                   # assignment -> its session id
        self.instances: dict[str, str] = {}                  # assignment -> the instance it runs as

    def busy(self) -> bool:
        return any(th.is_alive() for th in self.threads.values())

    def tick(self) -> None:
        for aid in [a for a, th in self.threads.items() if not th.is_alive()]:
            self.threads.pop(aid)
        for ev in self.follow.new():
            serves = [r for r in self.ROLES if r in (self.loop.cfg.serves or [])]   # [agents] serves: another machine may take a role
            if ev.get("type") == ASSIGNED and ev.get("role") in serves and ev.get("assignment") not in self.threads:
                th = threading.Thread(target=self._run, args=(ev,), daemon=True, name=ev["assignment"])
                self.threads[ev["assignment"]] = th
                th.start()
            elif ev.get("type") == "ticket.transitioned" and ev.get("to") in ("DONE", "CANCELLED") and ev.get("ticket"):
                self.loop.store.purge_ticket_sessions(ev["ticket"])     # a finished ticket's sessions go to the archive
            elif ev.get("type") == RELEASED and ev.get("assignment") in self.sessions \
                    and ev.get("instance") in (None, self.instances.get(ev["assignment"])):     # a release for another machine is not ours
                proc = self.loop.live_procs.get(self.sessions[ev["assignment"]])
                if proc is not None:
                    try:
                        proc.kill()                           # the turn ends; its report says so
                    except OSError:
                        pass

    # -- one assignment --------------------------------------------------------

    def _run(self, a: dict) -> None:
        loop = self.loop
        aid, role, tid = a["assignment"], a["role"], a["ticket"]
        path = Path(a["workspace"])
        sid = f"{role}-{tid}"
        instance = loop._instance(sid, role, tid)
        self.sessions[aid], self.instances[aid] = sid, instance
        say = lambda kind, **data: self.events.publish(kind, {"assignment": aid, "ticket": tid, "role": role, **data},
                                                       source=instance, subject=tid)
        say(ACCEPTED, instance=instance, session=sid)
        done = threading.Event()

        def beat():
            while not done.wait(HEARTBEAT_S):
                say(WORKING)
        threading.Thread(target=beat, daemon=True, name=f"{aid}:beat").start()
        report, text, failed, commit = None, "", True, None
        try:
            t = TicketView({**(a.get("brief") or {}), "id": tid, "branch": a.get("branch")})
            if role == "worker":
                task, nudge = self._work_task(t, a)
            else:
                task, nudge, commit = self._review_task(t, a, path)
            proc = loop._session(sid, role, path, tid)
            try:
                loop._session_running(sid, role, proc, tid)
                loop.store.set_session(sid, ticket_title=t.title, ticket_state="IN_PROGRESS" if role == "worker" else "IN_REVIEW",
                                       worker_turns=(a.get("brief") or {}).get("worker_turns") or 0)   # for the cards: no query needed
                if loop._trim_if_large(proc, sid, role) == "rotated":
                    proc = loop._session(sid, role, path, tid)
                    loop._session_running(sid, role, proc, tid)
                report, text, failed = loop._prompt_with_retry(proc, tid, task, nudge)
            finally:
                proc.close()
                loop._session_idle(sid, text)
        except Exception as e:  # noqa: BLE001 - whatever happened, the scheduler hears how it ended
            text = f"{type(e).__name__}: {e}"
        finally:
            done.set()
            self.sessions.pop(aid, None)
            self.instances.pop(aid, None)
            say(REPORTED, report=report, failed=failed, text=(text or "")[-2000:], commit=commit)

    def _work_task(self, t, a: dict) -> tuple[str, str]:
        if a.get("branch") is None:                           # a task: a folder and a result
            return (agents.worker_task_tasks(t),
                    "You stopped without reporting. Call `report_ticket` now: READY_FOR_REVIEW if your result is written, "
                    "otherwise BLOCKED with the reason.")
        return (agents.worker_task(self.loop.project, t, a.get("unit_command")),
                "You stopped without reporting. Call `report_ticket` now: READY_FOR_REVIEW if your work is "
                "committed and tested, otherwise BLOCKED with the reason.")

    def _review_task(self, t, a: dict, path: Path) -> tuple[str, str, str | None]:
        nudge = "You stopped without a verdict. Call `report_review` now with APPROVED or REJECTED."
        review = a.get("review") or {}
        if a.get("branch") is None:                           # a task: the worker's result in its folder
            files = sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())[:200]
            result = (path / "RESULT.md").read_text(encoding="utf-8", errors="replace") if (path / "RESULT.md").exists() else ""
            return agents.reviewer_task_tasks(t, review.get("summary") or "", files, result), nudge, None
        # code: the task system ran the tests (verifying is its job) and names the commit; the diff is read here
        base, commit = self.loop.cfg.base_branch, review.get("commit") or "HEAD"
        git = lambda *args: subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=False).stdout
        task = agents.reviewer_task(self.loop.project, t, commit, git("diff", f"{base}...{commit}"),
                                    git("diff", "--name-only", f"{base}...{commit}").split(), git("log", "--oneline", f"{base}..{commit}"),
                                    review.get("test_output"))
        return task, nudge, commit
