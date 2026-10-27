"""The resident agents' process manager, inside the host (host.py): starts a resident session for
every agent that should have one, closes the ones the Supervisor asked to stop, restart or reset
(and the ones a crash loop paused), records a process that exited between turns as a crash (three
in fifteen minutes pause the agent), and keeps every session's context bounded by compaction or
rotation."""

from __future__ import annotations

import time
from pathlib import Path

from . import access
from .closing import close_together
from .harness import AgentSession
from .org import AgentDef

CRASH_LOOP_COUNT, CRASH_LOOP_WINDOW = 3, 15 * 60


def _session_id(name: str) -> str:
    """An agent's session id is its name with dashes (org.AgentDef.session_id); the agent's record
    may already be gone when its process is closed."""
    return name.replace("_", "-")


class Residents:
    def __init__(self, host):
        self.host = host
        self.procs: dict[str, AgentSession] = {}        # agent name -> its resident session

    def start(self, agent: AgentDef) -> None:
        host = self.host
        try:
            proc = host._session_for(agent, agent.session_id)
            proc.open()
            self.procs[agent.name] = proc
            host.store.set_session(agent.session_id, kind=agent.name, status="idle", pid=host._pid(proc),
                                   session_file=proc.session_file, harness=proc.harness_name)
            host.log(f"{agent.session_id}: session resident ({host.harness.name})")
        except Exception as e:  # noqa: BLE001 - one agent that cannot start never stops the others
            host.log(f"{agent.session_id}: could not start: {e}")

    def start_missing(self, states: dict) -> None:
        """A resident session for every resident agent that has none: at start-up, after a crash,
        after a restart or reset, and for an agent just created. Paused and deleted agents stay down."""
        for name, agent in self.host.agents.items():
            st = states.get(name) or {}
            if name in self.procs or not agent.resident or st.get("paused") or st.get("deleted"):
                continue
            self.start(agent)

    def stop_changed(self, agents: dict[str, AgentDef], states: dict) -> None:
        """Close the sessions of agents that were deleted, paused, or asked to restart or reset,
        once their current turn (if any) has finished; a reset archives the transcript as a run."""
        host = self.host
        for name in list(self.procs):
            st = states.get(name) or {}
            gone = name not in agents or st.get("deleted")
            if not (gone or st.get("restart_requested") or st.get("reset_requested") or st.get("paused")):
                continue
            if name in host.agent_threads:
                continue  # let the current turn finish first
            proc = self.procs.pop(name)
            try:
                proc.close()
            except Exception as e:  # noqa: BLE001 - the session is dropped either way
                host.log(f"{proc.session_id}: close: {e!r}")
            sid = _session_id(name)
            access.revoke(host.store, sid)                # a paused or deleted agent's token ends with its session
            host.store.set_session(sid, status="idle", pid=None)
            if st.get("reset_requested"):
                archived = host.store.retire_session(sid, proc.retire())
                host.store.set_agent_state(name, reset_requested=False, context_hash=None, turn_depth=0)
                host._mark("agent.reset", session=sid, agent=name, archived=archived, by=st.get("reset_by"))
                host.log(f"{name}: session reset by request; previous transcript archived as {archived}")
            elif st.get("restart_requested"):
                host.store.set_agent_state(name, restart_requested=False)
                host.log(f"{name}: session restarted by request")

    def reap(self) -> None:
        """A resident process that has exited between turns is recorded as a crash, its session
        marked dead, and it is dropped so the next tick starts it again. Three crashes in fifteen
        minutes is a crash loop: the agent is paused with the reason, and the Supervisor is woken."""
        host = self.host
        now_t = time.time()
        for name in list(self.procs):
            if name in host.agent_threads:
                continue  # mid-turn: the turn's own code reports how it ended
            proc = self.procs[name]
            if proc.alive():
                continue
            self.procs.pop(name)
            try:
                proc.close()
            except Exception as e:  # noqa: BLE001 - the process is gone either way
                host.log(f"{proc.session_id}: close after exit: {e!r}")
            sid = proc.session_id
            tail = ""
            try:
                tail = (host.project.logs_dir / "agents" / f"{sid}.stderr.log").read_text(encoding="utf-8")[-400:]
            except OSError:
                pass
            host.store.set_session(sid, status="idle", pid=None, phase="idle", tool_name=None)
            st = host.store.agent_state(name)
            crashes = [t for t in st.get("crashes", []) if now_t - t < CRASH_LOOP_WINDOW] + [now_t]
            host.store.set_agent_state(name, crashes=crashes)
            host._mark("agent.crashed", session=sid, agent=name, count=len(crashes), stderr=tail)
            host.log(f"{sid}: process exited between turns ({len(crashes)} in {CRASH_LOOP_WINDOW // 60}m); will restart")
            if len(crashes) >= CRASH_LOOP_COUNT:
                host.store.set_agent_state(name, paused=True, crash_loop=f"{len(crashes)} crashes in {CRASH_LOOP_WINDOW // 60} minutes")
                host._mark("agent.crash_loop", session=sid, agent=name, count=len(crashes), stderr=tail)
                host.store.add_wakeup("supervisor", f"agent {name} crashed {len(crashes)} times in {CRASH_LOOP_WINDOW // 60} minutes and has been "
                                      f"paused. Its last stderr: {tail.strip()[-200:] or '(empty)'}. Fix its definition or ask the human, then resume it.")
                host.log(f"{sid}: crash loop; paused and the supervisor woken")

    def close_all(self) -> None:
        """At shutdown: every resident session closed, together (closing.py), each one's failure
        logged and not fatal."""
        close_together(list(self.procs.values()), lambda p, e: self.host.log(f"{p.session_id}: closing at shutdown: {e!r}"))
        for name in list(self.procs):
            access.revoke(self.host.store, _session_id(name))
            self.host.store.set_session(_session_id(name), status="idle", pid=None)
        self.procs.clear()

    def trim_if_large(self, proc: AgentSession | None, sid: str, agent: str) -> str | None:
        """Keep every session's context bounded, measured in tokens as the harness records them
        (harness.context: the tokens of its last model call), never by the transcript's size,
        which holds far more than the context. First, every compaction the transcript records
        that was not reported yet is reported (``agent.compacted``), whoever did it: that is the
        only time the event is published, so it always means a real compaction. Then, when the
        context holds more than ``compact_tokens``: a harness that compacts itself is left to it;
        one that compacts on request is asked (older messages become a summary and the agent
        keeps what it learned); one that cannot is rotated: archived as a run and started fresh,
        with the wake-up or task carrying the current state. Returns "compacted", "rotated", or
        None when nothing happened."""
        host = self.host
        limit = host.cfg.compact_tokens
        s = host.store.find_session(sid) or {}
        f = s.get("session_file")
        if not f or not Path(f).exists():
            return None
        ctx = host.harness.context(f)
        seen = int(s.get("compactions_seen") or 0)
        for c in ctx.compactions[seen:]:
            self._compacted(sid, agent, c, "the harness compacted the context by itself")
        if len(ctx.compactions) != seen:
            host.store.set_session(sid, compactions_seen=len(ctx.compactions))
        if not limit or ctx.tokens is None or ctx.tokens <= limit:
            return "compacted" if len(ctx.compactions) > seen else None
        over = f"the context held {ctx.tokens:,} tokens, more than compact_tokens ({limit:,})"
        if proc is not None and proc.alive():
            res = proc.compact()
            if res and res.get("mode") == "auto":
                return None                                # its own compaction is reported when the transcript records it
            if res:
                # the harness records this compaction in its transcript (now or shortly): it is reported here, not again then
                host.store.set_session(sid, compactions_seen=len(ctx.compactions) + 1)
                self._compacted(sid, agent, {"tokens_before": res.get("tokens_before") or ctx.tokens, "tokens_after": res.get("tokens_after")},
                                f"{over}; Orchestra asked the harness to compact it")
                return "compacted"
        # no compaction on this harness, or it failed: rotate
        owned = self.procs.get(agent)
        victim = self.procs.pop(agent) if owned is not None and owned.session_id == sid else proc
        archived_file = None
        if victim is not None:
            victim.close()
            archived_file = victim.retire()
        archived = host.store.retire_session(sid, archived_file)
        host.store.set_agent_state(agent, context_hash=None)
        host._mark("agent.reset", session=sid, agent=agent, archived=archived, by="orchestra",
                   reason=f"{over}, and the harness could not compact it")
        host.log(f"{sid}: {over}, no compaction; archived as {archived}, starting fresh")
        return "rotated"

    def _compacted(self, sid: str, agent: str, c: dict, reason: str) -> None:
        self.host._mark("agent.compacted", session=sid, agent=agent, tokens_before=c.get("tokens_before"),
                        tokens_after=c.get("tokens_after"), reason=reason)
        self.host.log(f"{sid}: compacted ({c.get('tokens_before')} -> {c.get('tokens_after')} tokens): {reason}")
