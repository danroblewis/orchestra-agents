"""How one turn of a session is followed and how its end is recorded, inside the host (host.py):
the session's normalized event stream becomes the phase the UI shows (waiting for the model,
generating, inside a tool, with the reply so far), and a turn that was cut off, failed, or ended
without the report it owed is written to the event log so the transcript can show the stop.
Nothing here is harness-specific."""

from __future__ import annotations

import time
from collections.abc import Callable

from .harness import AgentSession, TurnResult


class Turns:
    def __init__(self, host):
        self.host = host
        self._partial: dict[str, dict] = {}
        self._last_token_write: dict[str, float] = {}

    def on_event(self, sid: str) -> Callable[[dict], None]:
        """Translate a session's normalized event stream into the phase the UI shows."""
        store = self.host.store

        def fn(ev: dict) -> None:
            t = ev.get("type")
            now_t = time.time()
            if t == "turn_start":
                store.set_session(sid, phase="prompting", phase_since=now_t, tool_name=None)
            elif t == "assistant_start":
                store.set_session(sid, phase="generating", phase_since=now_t, last_token_at=now_t)
            elif t == "token":
                # Many small events while tokens stream: accumulate the reply in progress here
                # and write it, with the timestamp, every few seconds. Thinking is counted, not kept.
                part = self._partial.setdefault(sid, {"text": "", "thinking": 0})
                if ev.get("kind") == "text" and ev.get("delta"):
                    part["text"] = (part["text"] + ev["delta"])[-4000:]
                elif ev.get("kind") == "thinking" and ev.get("delta"):
                    part["thinking"] += len(ev["delta"])
                if now_t - self._last_token_write.get(sid, 0) > 3:
                    self._last_token_write[sid] = now_t
                    store.set_session(sid, phase="generating", last_token_at=now_t, last_output_at=now_t,
                                      partial_text=part["text"], thinking_chars=part["thinking"])
            elif t == "tool_start":
                name = ev.get("name", "?")
                store.set_session(sid, phase="tool", phase_since=now_t, tool_name=name, last_output_at=now_t, last_text=f"tool: {name}")
            elif t == "tool_end":
                store.set_session(sid, phase="prompting", phase_since=now_t, tool_name=None, last_output_at=now_t)
            elif t == "assistant_end":
                fields = {"last_output_at": now_t, "last_token_at": now_t, "partial_text": "", "thinking_chars": 0}
                self._partial.pop(sid, None)   # the message is in the transcript now
                txt = (ev.get("text") or "").strip()
                if txt:
                    fields["last_text"] = txt[-500:]
                store.set_session(sid, **fields)
        return fn

    def note(self, sid: str, res: TurnResult, kind: str = "", released: Callable[[], str | None] | None = None) -> None:
        """A turn that the host cut off, or that the harness failed, is recorded as an event with
        what the agent was doing at that moment, so the transcript can show the stop instead of
        just ending. The harness's session file is still the only transcript; this is the event
        log, which the transcript view overlays. ``released`` says why the task system took the
        assignment back, when the host killed the turn for that."""
        if not (res.timed_out or res.error):
            return
        host = self.host
        s = host.store.find_session(sid) or {}
        agent, ticket = kind or s.get("kind"), s.get("ticket_id") or None
        now_t = time.time()
        if res.error:
            reason = released() if released else None
            if reason:
                host._mark("turn.interrupted", session=sid, agent=agent, ticket=ticket, reason=f"released by the task system: {reason}")
                return
            if host.stop.is_set():
                # The harness "failed" because the host is shutting down and closed it.
                host._mark("turn.interrupted", session=sid, agent=agent, ticket=ticket, reason="orchestra shut down (restart or stop) during this turn")
                return
            host._mark("turn.error", session=sid, agent=agent, ticket=ticket, error=str(res.error)[:500])
            return
        phase = s.get("phase") or "working"
        doing = f"running `{s['tool_name']}`" if phase == "tool" and s.get("tool_name") else \
            "generating a reply" if phase == "generating" else "waiting for the model to start replying" if phase == "prompting" else phase
        by = res.stopped_by or "limit"
        host._mark("turn.timed_out", session=sid, agent=agent, ticket=ticket, stopped_by=by,
                   limit=host.cfg.silence_timeout if by == "silence" else host.cfg.turn_timeout, doing=doing,
                   phase_for=round(now_t - s["phase_since"]) if s.get("phase_since") else None,
                   silent_for=round(now_t - s["last_output_at"]) if s.get("last_output_at") else None,
                   last_text=(res.text or s.get("last_text") or "")[-300:])

    def prompt_with_retry(self, proc: AgentSession, ticket_id: str, task: str, nudge: str,
                          released: Callable[[], str | None] | None = None) -> tuple[dict | None, str, bool]:
        """A worker's or reviewer's turn: the task, and one nudge when it stopped without its
        report. Returns the report (or None), the agent's last text, and whether the turn failed."""
        cfg = self.host.cfg
        res = proc.prompt(task, cfg.turn_timeout or None, on_event=self.on_event(proc.session_id), idle_timeout=cfg.silence_timeout)
        self.note(proc.session_id, res, released=released)
        report = self.host.store.pop_report(ticket_id)
        if report or res.timed_out or res.error:
            return report, res.text, res.timed_out or bool(res.error)
        res = proc.prompt(nudge, 300, on_event=self.on_event(proc.session_id), idle_timeout=cfg.silence_timeout)
        self.note(proc.session_id, res, released=released)
        return self.host.store.pop_report(ticket_id), res.text, res.timed_out or bool(res.error)
