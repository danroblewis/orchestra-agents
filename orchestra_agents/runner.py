"""The AI agents' side of assignment (protocol.py): answers the offers for the roles it serves
(worker, reviewer) by running an agent session on the ticket, says it is still working while the
turn runs, with what the agent is saying now, and passes the work on as the agent decided.

Work passes directly between the machines that do it (the owner's decision D17): a worker that
finished asks the reviewers (``review.requested``), a reviewer that rejects asks the worker
instance that did the work (``changes.requested``), one that approves tells the task system's gate
(``review.approved``). Rejected work comes back to the same instance and the same session: a
worker's slot (``worker/1``) stays its ticket's while the work is out for review, so an offer
addressed to it finds it. The runner runs in the agents' host (host.py) and uses its sessions; it
knows the task system only through the bus and the offers (their brief); the task system records
every step."""

from __future__ import annotations

import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import access, prompting
from .builtins import PER_TICKET_ROLES
from .builtins import ROLES as BUILTIN
from .harness import AgentSession
from .modeling import announce_landed
from .prompting import TicketView
from .protocol import (
    ACCEPTED,
    APPROVED,
    CHANGES_REQUESTED,
    HEARTBEAT_S,
    OFFERS,
    RELEASED,
    REPORTED,
    REVIEW_REQUESTED,
    WORKING,
    ended,
    holder,
)

SAY_S = 2.0            # a heartbeat goes out early when what the agent is saying has changed, at most this often
CONFIRM_S = 180.0      # how long a claim is read back for when the offer names no lease
CONFIRM_POLL_S = 0.2   # how often it looks
UNIT_S = 600.0         # the unit command a code worker runs for its reviewer, at most this long


class AgentRunner:
    def __init__(self, host):
        self.host = host
        self.events = host.events
        self.offset = host.events.end()                      # offers made from now on
        self.threads: dict[str, threading.Thread] = {}      # assignment -> the thread running it
        self.sessions: dict[str, str] = {}                   # assignment -> its session id
        self.instances: dict[str, str] = {}                  # assignment -> the instance it runs as
        self.procs: dict[str, AgentSession] = {}             # assignment -> its session, from the moment it is opened
        self.released: dict[str, str] = {}                   # assignment -> why the task system took it back
        self.kept: dict[str, str] = {}                       # ticket -> the worker session whose slot is kept while its work is out
        self.returns: dict[str, str] = {}                    # an offer addressed to a kept slot -> the ticket

    def busy(self) -> bool:
        return any(th.is_alive() for th in self.threads.values())

    def tick(self) -> None:
        for aid in [a for a, th in self.threads.items() if not th.is_alive()]:
            self.threads.pop(aid)
        start = self.offset                                   # every offer below is after this: a claim is read back from here
        evs, self.offset = self.events.after(self.offset)
        self.host.fillers.see(evs)
        serves = [r for r in PER_TICKET_ROLES if r in (self.host.cfg.serves or [])]   # [agents] serves: another machine may take a role
        for ev in evs:
            kind = ev.get("type")
            if kind in OFFERS and ev.get("role") in serves and ev.get("assignment") not in self.threads:
                self._offered(ev, start)
            elif end := ended(ev, self.host.cfg.work):                  # the gate landed it, its reviewer approved it (tasks), it was cancelled
                for line in announce_landed(self.host.store, ev, self.host.cfg.work):   # a predicted change that takes effect now
                    self.host.log(line)
                self._let_go(end[0])
                self.host.store.purge_ticket_sessions(end[0])           # a finished ticket's sessions go to the archive
            elif kind == RELEASED and ev.get("assignment") in self.sessions \
                    and ev.get("instance") in (None, self.instances.get(ev["assignment"])):     # a release for another machine is not ours
                self.released[ev["assignment"]] = ev.get("reason") or "no reason given"
                proc = self.procs.get(ev["assignment"])
                if proc is not None:
                    try:
                        proc.kill()                           # the turn ends; turns.py records the interruption
                    except OSError:
                        pass
            elif kind == RELEASED and ev.get("assignment") in self.returns:   # rejected work it never took went to the queue
                self._let_go(self.returns.pop(ev["assignment"]))
            elif kind == ACCEPTED and ev.get("role") == "worker" and ev.get("ticket") in self.kept \
                    and ev.get("source") != self.host._slots.get(self.kept[ev["ticket"]]):
                self._let_go(ev["ticket"])                    # another worker took the ticket from the queue

    def _offered(self, ev: dict, start: int) -> None:
        """An offer for a role served here. One addressed ``to`` an instance is taken only by
        the slot that instance is (rejected work coming back to the worker that did it); any
        other takes the session's slot, or the lowest free one."""
        aid, role, tid = ev["assignment"], ev["role"], ev["ticket"]
        sid = f"{role}-{tid}"
        if ev.get("to"):
            if self.host._slots.get(sid) != ev["to"]:
                return                                        # for another machine, or an instance this host no longer is
            self.returns[aid] = tid
        instance = self.host._instance(sid, role, tid)        # the slot is taken here, on the one thread that allocates them
        self.sessions[aid], self.instances[aid] = sid, instance
        th = threading.Thread(target=self._run, args=(ev, sid, instance, start), daemon=True, name=aid)
        self.threads[aid] = th
        th.start()

    def _let_go(self, tid: str) -> None:
        """The worker slot kept for a ticket is free again (its work came back to the queue, or the
        ticket ended), unless a turn runs in it."""
        sid = self.kept.pop(tid, None)
        if sid is not None and sid not in self.sessions.values():
            self.host._release(sid)

    def _run(self, a: dict, sid: str, instance: str, start: int) -> None:
        """One turn: claim the offer, read the claim back from the bus (``_held``: an instance
        whose claim was not the first starts no session), run the session's turn with heartbeats,
        and end the turn as the agent decided. A release that arrives before the session is open
        skips the turn; one that arrives during it kills the turn (tick) or ends it before the
        nudge (turns.prompt_with_retry)."""
        host = self.host
        aid, role, tid = a["assignment"], a["role"], a["ticket"]
        path = Path(a["workspace"])
        cause = {"causationid": a["id"]} if a.get("id") else {}
        say = lambda kind, **data: self.events.publish(kind, {"assignment": aid, "ticket": tid, "role": role, **data},
                                                       source=instance, subject=tid, **cause)
        claim = say(ACCEPTED, instance=instance)
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
        report, text, failed, commit, lost, passed = None, "", True, None, False, False
        spec = BUILTIN[role]
        open_session = lambda: host._open_session(sid, role, prompting.role_prompt(host.project, spec.prompt), tuple(spec.tools), path, tid)
        try:
            t = TicketView({**(a.get("brief") or {}), "id": tid, "branch": a.get("branch")})
            if role == "worker":
                task, nudge = self._work_task(t, a)
            else:
                task, nudge, commit = self._review_task(t, a, path)
            if not self._held(a, instance, claim, start):
                lost = True
                return                                        # another instance's claim came first: no session, nothing spent
            threading.Thread(target=beat, daemon=True, name=f"{aid}:beat").start()
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
                access.revoke(host.store, sid)                # its token ends with it
                host._session_idle(sid, text, release=False)  # the slot is given back, or kept, below
        except Exception as e:  # noqa: BLE001 - whatever happened, the task system hears how it ended
            text = f"{type(e).__name__}: {e}"
        finally:
            done.set()
            self.sessions.pop(aid, None)
            self.instances.pop(aid, None)
            self.procs.pop(aid, None)
            self.returns.pop(aid, None)
            if self.released.pop(aid, None) is None and not lost:    # a released turn is no longer this instance's to end
                try:
                    passed = self._end(a, instance, report, text, failed, commit, path, say)
                except Exception as e:  # noqa: BLE001 - the turn still ends on the bus
                    say(REPORTED, report=None, failed=True, text=f"{type(e).__name__}: {e}"[-2000:], commit=None)
            if passed and role == "worker":
                self.kept[tid] = sid                          # rejected work comes back to this slot and session
            else:
                if self.kept.get(tid) == sid:
                    self.kept.pop(tid)
                host._release(sid)                            # the slot, whether or not the session ever opened

    def _end(self, a: dict, instance: str, report: dict | None, text: str, failed: bool, commit: str | None,
             path: Path, say) -> bool:
        """End the turn as the agent decided: pass the work on directly (a review requested,
        changes requested of the worker that did it, an approval for the gate), or report when it
        passes to no machine (BLOCKED, or no report). True when a worker passed its work on."""
        tid, role = a["ticket"], a["role"]
        offer = {k: a.get(k) for k in ("workspace", "branch", "base", "lease_s", "unit_command") if k in a}
        if role == "worker" and report and report.get("state") == "READY_FOR_REVIEW":
            facts = self._verified(a, path) if a.get("branch") else {"commit": None, "test_output": None}
            if "blocked" not in facts:
                self.events.publish(REVIEW_REQUESTED, {
                    **offer, "assignment": f"{tid}.reviewer.{uuid.uuid4().hex[:8]}", "ticket": tid, "role": "reviewer", "ends": a["assignment"],
                    "brief": a.get("brief") or {}, "review": {"summary": report.get("summary") or "", "worker": instance, **facts}},
                    source=instance, subject=tid, **({"causationid": a["id"]} if a.get("id") else {}))
                return True
            report = {"state": "BLOCKED", "summary": report.get("summary") or "", "blocked_reason": facts["blocked"]}
        if role == "reviewer" and report and report.get("verdict") in ("APPROVED", "REJECTED"):
            review = a.get("review") or {}
            if report["verdict"] == "APPROVED":
                self.events.publish(APPROVED, {"ends": a["assignment"], "ticket": tid, "role": role, "summary": report.get("summary") or "",
                                               "commit": commit}, source=instance, subject=tid, **({"causationid": a["id"]} if a.get("id") else {}))
                return False
            if a.get("branch"):
                self._tidy(path)
            brief = dict(a.get("brief") or {})                # as the ticket will read once the worker takes it back
            brief.update(feedback=report.get("feedback"), rejected_by="reviewer", worker_turns=(brief.get("worker_turns") or 0) + 1,
                         review_rounds=(brief.get("review_rounds") or 0) + 1)
            self.events.publish(CHANGES_REQUESTED, {
                **offer, "assignment": f"{tid}.worker.{uuid.uuid4().hex[:8]}", "ticket": tid, "role": "worker", "to": review.get("worker"),
                "ends": a["assignment"], "brief": brief, "summary": report.get("summary") or "", "feedback": report.get("feedback") or "",
                "commit": commit}, source=instance, subject=tid, **({"causationid": a["id"]} if a.get("id") else {}))
            return False
        say(REPORTED, report=report if role == "worker" else None, failed=failed, text=(text or "")[-2000:], commit=commit)
        return False

    def _verified(self, a: dict, path: Path) -> dict:
        """Code: what the worker left is committed, and the commit to review is the head, beyond
        the base, with the unit command's output on it for the reviewer; or why there is nothing
        to review."""
        git = lambda *args: subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=False)
        if git("status", "--porcelain").stdout.strip():
            git("add", "-A")
            git("commit", "-q", "-m", f"{a['ticket']}: commit remaining worker changes")
        head, base = git("rev-parse", "HEAD").stdout.strip(), a.get("base") or self.host.cfg.base_branch
        if not head or git("merge-base", "--is-ancestor", head, base).returncode == 0:
            return {"blocked": f"the worker reported READY_FOR_REVIEW but the branch has no commits beyond {base}"}
        out = None
        if a.get("unit_command"):
            try:
                r = subprocess.run(["bash", "-c", a["unit_command"]], cwd=path, capture_output=True, text=True, timeout=UNIT_S, check=False)
                out = f"$ {a['unit_command']}\n{(r.stdout + r.stderr)[-6000:]}\n[{'passed' if r.returncode == 0 else 'failed'}]"
            except subprocess.TimeoutExpired:
                out = f"$ {a['unit_command']}\n(stopped after {int(UNIT_S)} s)\n[failed]"
        return {"commit": head, "test_output": out}

    @staticmethod
    def _tidy(path: Path) -> None:
        """A reviewer passes the workspace back as it found it: what its review changed or left
        untracked is removed (ignored files, such as caches, stay)."""
        subprocess.run(["git", "checkout", "-q", "--", "."], cwd=path, capture_output=True, check=False)
        subprocess.run(["git", "clean", "-q", "-fd"], cwd=path, capture_output=True, check=False)

    @staticmethod
    def _lease(a: dict) -> float:
        return float(a.get("lease_s") or CONFIRM_S)

    def _held(self, a: dict, instance: str, claim: dict, start: int) -> bool:
        """Whether this instance holds the turn, before a session costs anything: its claim, read
        back from the bus from where the offer was read, is the first valid claim of the offer
        (the bus's order decides a race, and every reader sees the same), and no release of the
        assignment came meanwhile. No word comes from the task system to a loser: it reads this.
        A claim not seen on the bus within the offer's lease holds nothing."""
        aid = a["assignment"]
        end = time.time() + self._lease(a)
        while time.time() < end:
            if aid in self.released:
                return False
            evs, _ = self.events.after(start)
            ids = [e.get("id") for e in evs]
            if claim.get("id") in ids:
                upto = evs[:ids.index(claim["id"]) + 1]
                for ev in upto:
                    if ev.get("type") == RELEASED and ev.get("assignment") == aid and ev.get("instance") in (None, instance):
                        return False
                return holder(upto, a) == instance
            time.sleep(CONFIRM_POLL_S)
        return False

    def _work_task(self, t: TicketView, a: dict) -> tuple[str, str]:
        if a.get("branch") is None:                           # a task: a folder and a result
            return (prompting.worker_task_tasks(self.host.project, t),
                    ("You stopped without reporting. Call `report_ticket` now: READY_FOR_REVIEW if your result is written, "
                     "otherwise BLOCKED with the reason."))
        return (prompting.worker_task(self.host.project, t, a.get("unit_command")),
                ("You stopped without reporting. Call `report_ticket` now: READY_FOR_REVIEW if your work is "
                 "committed and tested, otherwise BLOCKED with the reason."))

    def _review_task(self, t: TicketView, a: dict, path: Path) -> tuple[str, str, str | None]:
        nudge = "You stopped without a verdict. Call `report_review` now with APPROVED or REJECTED."
        review = a.get("review") or {}
        if a.get("branch") is None:                           # a task: the worker's result in its folder
            files = sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())[:200]
            result = (path / "RESULT.md").read_text(encoding="utf-8", errors="replace") if (path / "RESULT.md").exists() else ""
            return prompting.reviewer_task_tasks(t, review.get("summary") or "", files, result), nudge, None
        # code: the worker ran the unit tests on the commit it asked to have reviewed and names it; the diff is read here
        # (an offer that names no commit is reviewed at the workspace's head, and the verdict names none)
        base, commit = a.get("base") or self.host.cfg.base_branch, review.get("commit")
        head = commit or "HEAD"
        git = lambda *args: subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=False).stdout
        task = prompting.reviewer_task(self.host.project, t, head, git("diff", f"{base}...{head}"),
                                    git("diff", "--name-only", f"{base}...{head}").split(), git("log", "--oneline", f"{base}..{head}"),
                                    review.get("test_output"))
        return task, nudge, commit
