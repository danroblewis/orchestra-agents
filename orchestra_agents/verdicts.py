"""Waking the agent that made a prediction when Orchestra judges it (``orchestra.prediction.judged``,
whose ``correlationid`` is the change's id, the one ``modeling.commit`` recorded with the agent).

- A resident or custom agent (the Supervisor, a curator agent) gets a wake-up like any other,
  saying the verdict and where to read it.
- A worker's prediction was made on a ticket whose session is over by then, so the host runs one
  more short turn as that worker, on a session of its own (``<role>-<ticket>-verdict-<time>``),
  with only the ``model`` tools and a scratch directory, not the ticket's workspace: it reads the
  verdict and says what it means. The reply is ``agent.said``, like any turn.

Each prediction wakes its agent once; the record keeps the verdict (``AgentStore.update_prediction``)."""

from __future__ import annotations

import time

from . import access, prompting
from .builtins import ROLES as BUILTIN
from .org import JUDGED
from .run_text import VERDICTS

VERDICT_TOOLS = ("model",)


def reason(ev: dict, rec: dict) -> str:
    v = ev.get("verdict") or "?"
    where = f"ticket {rec['ticket']}, " if rec.get("ticket") else ""
    return (f"event {JUDGED}: your prediction {rec.get('id')} ({where}experiment {rec.get('experiment')}) of change "
            f"{rec.get('change')} was judged: {v} ({VERDICTS.get(v, 'see the numbers')}). read_predictions with id "
            f"{rec.get('id')} gives the numbers it was judged by.")


class Verdicts:
    def __init__(self, host):
        self.host = host
        self.waiting: list[tuple[str, dict, str]] = []       # (role, prediction record, reason): worker turns to run

    def see(self, ev: dict) -> None:
        """A verdict on a prediction an agent here committed: wake that agent."""
        if ev.get("type") != JUDGED:
            return
        found = self.host.store.find_prediction(ev.get("correlationid"))
        if found is None or found[1].get("judged"):
            return
        role, rec = found
        self.host.store.update_prediction(rec["change"], judged=ev.get("verdict") or "?", judged_at=time.time())
        why = reason(ev, rec)
        agent = self.host.agents.get(role)
        if agent is not None and agent.builtin and not agent.resident:
            self.waiting.append((role, rec, why))
        elif agent is not None:
            self.host.store.add_wakeup(role, why)

    def tick(self) -> None:
        """Start the waiting workers' verdict turns (on the main thread: a numbered slot is taken
        only here, as the runner takes them)."""
        while self.waiting:
            role, rec, why = self.waiting.pop(0)
            ticket = rec.get("ticket") or ""
            sid = f"{role}-{ticket or 'none'}-verdict-{time.strftime('%Y%m%d-%H%M%S')}"
            self.host._instance(sid, role, ticket)
            self.host._spawn(self.host.agent_threads, sid, self._turn, sid, role, ticket, why)

    def _turn(self, sid: str, role: str, ticket: str, why: str) -> None:
        host = self.host
        cwd = host.project.state_dir / "verdicts"
        cwd.mkdir(parents=True, exist_ok=True)
        try:
            proc = host._open_session(sid, role, prompting.role_prompt(host.project, BUILTIN[role].prompt), VERDICT_TOOLS, cwd, ticket)
        except Exception as e:  # noqa: BLE001 - a turn that cannot start is said in the log; the verdict stays readable
            host.log(f"{sid}: could not start the verdict turn: {e!r}")
            host._release(sid)
            return
        text = ""
        try:
            host._session_running(sid, role, proc, ticket or None)
            res = proc.prompt(prompting.verdict_task(why), host.cfg.turn_timeout or None, on_event=host.turns.on_event(sid),
                              idle_timeout=host.cfg.silence_timeout)
            text = res.text
            host.turns.note(sid, res, role)
        finally:
            proc.close()
            access.revoke(host.store, sid)
            host._session_idle(sid, text)
        host._say("agent.said", session=sid, summary=(text or "")[:500])
        host.log(f"{sid}: {(text or '(no text)')[:160]!r}")
