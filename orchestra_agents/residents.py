"""The resident agents' process manager, inside the host (host.py): starts a resident session for
every agent that should have one, closes the ones the Supervisor asked to stop, restart or reset
(and the ones a crash loop paused), records a process that exited between turns as a crash (three
in fifteen minutes pause the agent), and keeps every session's context bounded by compaction or
rotation."""

from __future__ import annotations

import time
from pathlib import Path

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
        """At shutdown: every resident session closed, each one's failure logged and not fatal."""
        for name, proc in list(self.procs.items()):
            try:
                proc.close()
            except Exception as e:  # noqa: BLE001 - shut the rest down regardless
                self.host.log(f"{proc.session_id}: closing at shutdown: {e!r}")
            self.host.store.set_session(_session_id(name), status="idle", pid=None)
        self.procs.clear()

    def trim_if_large(self, proc: AgentSession | None, sid: str, agent: str) -> str | None:
        """Keep every session's context bounded. When the transcript has grown past
        `session_max_bytes` since it was last compacted, ask the harness to compact: older
        messages become a summary and the agent keeps what it learned, which is what
        matters 200k tokens into a task. Only when the harness cannot compact (or the
        attempt fails) is the session rotated: archived as a run and started fresh, with
        the wake-up or task carrying the current state. Returns "compacted", "rotated",
        or None when nothing was needed."""
        host = self.host
        limit = host.cfg.session_max_bytes
        s = host.store.find_session(sid) or {}
        f = s.get("session_file")
        if not limit or not f or not Path(f).exists():
            return None
        size = Path(f).stat().st_size
        grown = size - int(s.get("compacted_at_bytes") or 0)
        if grown <= limit:
            return None
        if proc is not None and proc.alive():
            res = proc.compact()
            if res and res.get("mode") == "auto":
                # A harness that bounds its own context; nothing to do but note it.
                host.store.set_session(sid, compacted_at_bytes=Path(f).stat().st_size)
                host._mark("agent.compacted", session=sid, agent=agent, mode="auto", limit=res.get("limit"),
                           reason=f"the harness compacts itself past {int(res.get('limit') or 0):,} tokens")
                return "compacted"
            if res:
                host.store.set_session(sid, compacted_at_bytes=Path(f).stat().st_size, compactions=int(s.get("compactions") or 0) + 1)
                host._mark("agent.compacted", session=sid, agent=agent, tokens_before=res.get("tokens_before"),
                           tokens_after=res.get("tokens_after"), reason=f"{grown // 1000} KB of transcript since the last compaction")
                host.log(f"{sid}: compacted ({res.get('tokens_before')} -> {res.get('tokens_after')} tokens)")
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
                   reason=f"transcript reached {size // 1000} KB and the harness could not compact it")
        host.log(f"{sid}: transcript {size // 1000} KB, no compaction; archived as {archived}, starting fresh")
        return "rotated"
