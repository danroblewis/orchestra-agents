"""The agents' host (``orchestra-agents run``): the process that runs the AI agents as machines.

It keeps the resident agents (the Supervisor, the Coordinator, any custom continuous ones) up,
wakes agents on the events they listen for (the task system's ``task.attention_needed`` to a
planner among them) and on their schedules; answers the task system's assignments for the roles
it serves (runner.py) by running a worker's or reviewer's session on the ticket; and serves the
Supervisor chat (control.py). Sessions, transcripts, compaction and crash handling are here;
which product runs a session is invisible past ``harness``. Tickets are the task system's: the
agents reach them only over the bus (protocol.py)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import psutil

from . import prompting as agents
from . import tooling
from .builtins import ROLES, resident_roles
from .config import Project
from .control import ControlServer
from .events import Events
from .harness import AgentSession, SessionSpec, get_harness
from .org import AgentDef, all_agents, all_teams, effective_tools, tools_path
from .probes import Prober, team_probes
from .protocol import ASSIGNED, ATTENTION, RELEASED, Follower
from .roles import all_roles, due_at, parse_interval
from .store import AgentStore

RESIDENT_AGENTS = tuple(r.name for r in resident_roles())
HOST_LOG: Path | None = None
HOST_LOG_MAX = 5_000_000


def log(msg: str) -> None:
    line = f"[agents {time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    if HOST_LOG is not None:
        try:
            if HOST_LOG.exists() and HOST_LOG.stat().st_size > HOST_LOG_MAX:
                HOST_LOG.replace(HOST_LOG.with_suffix(".log.1"))
            with HOST_LOG.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


class AgentHost:
    def __init__(self, project: Project, store: AgentStore, events: Events):
        global HOST_LOG
        HOST_LOG = project.logs_dir / "agents-host.log"
        self.project, self.store, self.events = project, store, events
        self.cfg = project.config
        self.harness = get_harness(self.cfg.harness)
        self.stop = threading.Event()
        self.agent_threads: dict[str, threading.Thread] = {}
        self.agent_procs: dict[str, AgentSession] = {}
        self.live_procs: dict[str, AgentSession] = {}   # session id -> session, while a turn runs
        self.agents: dict[str, AgentDef] = {}
        self.teams: dict = {}
        self.role_defs: dict = {}
        self.control: ControlServer | None = None
        self._pending_depth: dict[str, int] = {}
        self._started_at = time.time()
        self._last_scheduled: dict[str, float] = {}
        self._last_token_write: dict[str, float] = {}
        self._partial: dict[str, dict] = {}
        self._slots: dict[str, str] = {}               # session id -> the machine instance it runs as ("worker/1")
        self._wake_follow = Follower(events)           # events that wake agents: only those after startup
        self._digest = Follower(events)                # what the Supervisor is told happened since its last turn
        from .runner import AgentRunner
        self.runner = AgentRunner(self)                # answers the task system's assignments
        self.prober = Prober(project, store, events)   # the teams' probes

    # -- lifecycle -------------------------------------------------------------

    def recover(self) -> None:
        """Turns the previous host left running: their processes are stopped (no orphans) and
        their sessions marked idle, with an event so the transcript shows the cut."""
        for s in self.store.sessions():
            if s.get("status") == "running" and s.get("pid"):
                self._kill_orphan(int(s["pid"]), s["id"])
            if s.get("status") == "running":
                self.events.emit("turn.interrupted", session=s["id"], agent=s.get("kind"), ticket=s.get("ticket_id") or None,
                                 reason="the agents' host was not running (it crashed or was killed) during this turn")
            self.store.set_session(s["id"], status="idle", pid=None)

    def run(self, once: bool = False) -> None:
        import signal

        def _stop(_sig, _frame):
            self.stop.set()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _stop)
            except ValueError:  # not the main thread (tests)
                pass
        self.recover()
        self.events.publish("orchestra.machine.started", {"package": "agents", "mode": "continuous", "kind": "program",
                                                          "listens_for": [ASSIGNED, RELEASED, ATTENTION], "log": str(HOST_LOG),
                                                          "does": "runs the AI agents: their sessions, wake-ups, and the task system's "
                                                                  "assignments for the roles they fill"})
        log(f"project {self.project.key} on {self.project.repo_root}; harness {self.harness.name}; serving {', '.join(self.cfg.serves) or 'no role'}")
        self._start_project_agents()
        self._start_control()
        try:
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001 - keep the host alive
                    log(f"tick error: {e!r}")
                if once and not self._busy():
                    break
                time.sleep(1)
        finally:
            if self.control:
                self.control.stop()
            self._shutdown_markers()
            for p in list(self.live_procs.values()):       # a worker or reviewer mid-turn: no orphans
                try:
                    p.close()
                except Exception as e:  # noqa: BLE001 - shut the rest down regardless
                    log(f"closing a running session: {e!r}")
            for name, p in self.agent_procs.items():
                p.close()
                self.store.set_session(self.agents[name].session_id if name in self.agents else name.replace("_", "-"), status="idle", pid=None)
            self.store._mutate_runtime(lambda d: d.pop("control", None) if (d.get("control") or {}).get("pid") == os.getpid() else None)

    def _busy(self) -> bool:
        return self.runner.busy() or any(t.is_alive() for t in self.agent_threads.values())

    def tick(self) -> None:
        self.project.reload_config()
        self.cfg = self.project.config
        for k in [k for k, th in self.agent_threads.items() if not th.is_alive()]:
            self.agent_threads.pop(k)
        self._reap_residents()
        self._refresh_org()
        try:
            self.prober.tick(team_probes(self.project, self.teams))
        except Exception as e:  # noqa: BLE001 - a broken probe never stops the host
            log(f"probes: {e!r}")
        self._schedule_wakeups()
        self._wake_on_events()
        for target in list(self.agents):
            if target == "supervisor" and self.agent_procs.get("supervisor") is None:
                continue
            if self.agents[target].builtin and not self.agents[target].resident:
                continue  # workers and reviewers answer assignments, not wake-ups
            if target not in self.agent_threads:
                st = self.store.agent_state(target)
                if st.get("paused") or st.get("deleted"):
                    continue  # wake-ups stay queued until resumed
                reasons = list(dict.fromkeys(w["reason"] for w in self.store.drain_wakeups(target)))
                if reasons:
                    self._spawn(self.agent_threads, target, self._wake, target, reasons)
        self.runner.tick()

    CRASH_LOOP_COUNT, CRASH_LOOP_WINDOW = 3, 15 * 60

    @staticmethod
    def _kill_orphan(pid: int, session_id: str) -> None:
        """A turn's process outlived the host that started it (the host was killed): stop it, so
        it neither keeps working unseen nor holds its session against the retry."""
        import signal
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return
            os.killpg(pid, signal.SIGTERM)                # each turn runs in a process group of its own
            log(f"{session_id}: stopped its orphaned process {pid}")
        except (psutil.NoSuchProcess, ProcessLookupError, PermissionError, OSError):
            pass

    def _shutdown_markers(self) -> None:
        """Every turn still running is about to be cut off by this shutdown. Say so in the
        event log, so the transcript does not look like the agent stopped on its own."""
        for s in self.store.sessions():
            if s.get("status") == "running":
                self.events.emit("turn.interrupted", session=s["id"], agent=s.get("kind"), ticket=s.get("ticket_id") or None,
                                 reason="orchestra shut down (restart or stop) during this turn")

    def _refresh_org(self) -> None:
        """Reload roles, teams, and agents from their stores; start resident processes for
        new agents, close processes for agents that were deleted, paused, or asked to restart."""
        try:
            roles, teams, agents_ = all_roles(self.project), all_teams(self.project), all_agents(self.project)
        except Exception as e:  # noqa: BLE001 - a broken record is logged; the host keeps running
            log(f"org: could not load roles, teams, or agents: {e}")
            return
        for name in list(self.agent_procs):
            st = self.store.agent_state(name)
            gone = name not in agents_ or st.get("deleted")
            if gone or st.get("restart_requested") or st.get("reset_requested") or st.get("paused"):
                if name in self.agent_threads:
                    continue  # let the current turn finish first
                proc = self.agent_procs.pop(name)
                proc.close()
                sid = agents_[name].session_id if name in agents_ else name.replace("_", "-")
                self.store.set_session(sid, status="idle", pid=None)
                if st.get("reset_requested"):
                    archived = self.store.retire_session(sid)
                    self.store.set_agent_state(name, reset_requested=False, context_hash=None, turn_depth=0)
                    self.events.emit("agent.reset", session=sid, agent=name, archived=archived, by=st.get("reset_by") or "operator")
                    log(f"{name}: session reset by request; previous transcript archived as {archived}")
                elif st.get("restart_requested"):
                    self.store.set_agent_state(name, restart_requested=False)
                    log(f"{name}: session restarted by request")
        self.role_defs, self.teams, self.agents = roles, teams, agents_
        for name, agent in agents_.items():
            if name == "supervisor" or name in self.agent_procs or not agent.resident or agent.builtin:
                continue  # built-in residents are started up front by _start_project_agents
            st = self.store.agent_state(name)
            if st.get("paused") or st.get("deleted"):
                continue
            self._start_resident(agent)

    def _start_resident(self, agent: AgentDef) -> None:
        try:
            proc = self._session_for(agent, agent.session_id)
            proc.open()
            self.agent_procs[agent.name] = proc
            self.store.set_session(agent.session_id, kind=agent.name, status="idle", pid=self._pid(proc),
                                   session_file=proc.session_file, harness=proc.harness_name)
            log(f"{agent.session_id}: session resident ({self.harness.name})")
        except Exception as e:  # noqa: BLE001 - one agent that cannot start never stops the others
            log(f"{agent.session_id}: could not start: {e}")

    def _session_for(self, agent: AgentDef, session_id: str) -> AgentSession:
        """A session for a custom or implicit agent: its roles' prompts plus its team's
        responsibilities and access, its working directory in the team's space, and the
        team chain's tools/ directories on PATH."""
        team = self.teams.get(agent.team) if agent.team else None
        cwd = self.project.repo_root
        extra: dict[str, str] = {}
        if team is not None:
            cwd = team.space_dir(self.project)
            (cwd / "tools").mkdir(parents=True, exist_ok=True)
            dirs = [str(p) for p in tools_path(self.project, self.teams, agent)]
            extra["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
            extra["ORCHESTRA_TEAM"] = team.name
        return self._open_session(session_id, agent.name, agents.agent_prompt(self.project, agent, self.role_defs, self.teams),
                                  effective_tools(agent, self.role_defs, self.teams), cwd, extra_env=extra)

    def _open_session(self, session_id: str, role: str, system_prompt: str, tools: tuple[str, ...], cwd: Path,
                      ticket_id: str = "", extra_env: dict[str, str] | None = None) -> AgentSession:
        """Start a session on the configured harness. Which product runs the agent is
        invisible past this point: the host only sees the AgentSession interface. The
        prompt the session starts with is kept beside its stderr log, so a transcript can
        show what the agent was told, whatever the harness records."""
        prompt_file = self.project.logs_dir / "agents" / f"{session_id}.prompt.md"
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(system_prompt, encoding="utf-8")
        source = self._instance(session_id, role, ticket_id)
        extra_env = {**(extra_env or {}), "ORCHESTRA_SOURCE": source}
        self._announce(source, role)
        return self.harness.start(SessionSpec(
            session_id=session_id, session_dir=self.project.sessions_dir, cwd=cwd,
            system_prompt=system_prompt, role=role, tool_groups=tools,
            env={"ORCHESTRA_ROLE": role, "ORCHESTRA_REPO": str(self.project.repo_root), "ORCHESTRA_TICKET": ticket_id,
                 **(extra_env or {})},
            log_path=self.project.logs_dir / "agents" / f"{session_id}.stderr.log",
            model=self.cfg.model, provider=self.cfg.provider, thinking=self.cfg.thinking, ticket_id=ticket_id,
            compact_tokens=self.cfg.compact_tokens))

    def _instance(self, session_id: str, role: str, ticket_id: str = "") -> str:
        """The machine instance a session runs as, which is also the source of its events: a
        resident agent is its role ("supervisor"); a worker or reviewer for one ticket takes the
        lowest free slot of its role ("worker/1"), given back when its turn ends."""
        if session_id in self._slots:
            return self._slots[session_id]
        if not ticket_id:
            source = role
        else:
            taken = {s for s in self._slots.values() if s.startswith(role + "/")}
            source = next(f"{role}/{n}" for n in range(1, 1000) if f"{role}/{n}" not in taken)
        self._slots[session_id] = source
        self.events.bind(session_id, source)
        return source

    def _release(self, session_id: str) -> None:
        source = self._slots.pop(session_id, None)
        if source and "/" in source:
            self.events.unbind(session_id)

    def _announce(self, source: str, role: str) -> None:
        """An agent instance says what it is when it starts (AGENTS.md: a machine announces its
        mode and what it listens for): an AI agent, on which harness and model, engaged how."""
        agent = self.agents.get(role) or all_agents(self.project).get(role)
        mode = {"continuous": "continuous", "scheduled": "scheduled", "ad_hoc": "ad hoc"}.get(getattr(agent, "kind", ""), "ad hoc")
        listens = sorted({w.split(":")[0] for w in (getattr(agent, "wake_on", None) or [])})
        self.events.publish("orchestra.machine.started", {"package": "agents", "mode": mode, "listens_for": listens, "kind": "agent", "role": role,
                                                          "harness": self.harness.name, "model": self.cfg.model},
                            source=source)

    @staticmethod
    def _pid(proc: AgentSession) -> int | None:
        """The process that proves the session is alive: the harness's own when it keeps one
        (Pi), otherwise this loop, which is what holds the session open. A dead session has
        no pid at all, so the UI can never mistake a dead agent for a running one."""
        if not proc.alive():
            return None
        return proc.pid or os.getpid()

    def _wake_on_events(self) -> None:
        """Match new events against every agent's wake_on list and queue wake-ups. Workers and
        reviewers are not woken: their events are assignments, answered by the runner."""
        for ev in self._wake_follow.new():
            for name, agent in self.agents.items():
                if (agent.builtin and not agent.resident) or not agent.matches_event(ev):
                    continue
                bits = [f"{k}={ev[k]}" for k in ("ticket", "milestone", "from", "to", "reason", "by", "probe", "subject", "note", "output") if ev.get(k)]
                self.store.add_wakeup(name, f"event {ev['type']}" + (" " + " ".join(str(b)[:300] for b in bits) if bits else ""))
                self._pending_depth[name] = max(self._pending_depth.get(name, 0), int(ev.get("depth", 0)))

    def _schedule_wakeups(self) -> None:
        """Queue a wake-up for agents whose interval has elapsed or whose daily time has come."""
        now_t = time.time()
        for name, agent in self.agents.items():
            st = self.store.agent_state(name)
            if st.get("paused") or st.get("deleted") or agent.kind == "ad_hoc":
                continue
            if agent.every:
                secs = parse_interval(agent.every)
                last = self._last_scheduled.get(name, self._started_at)
                if now_t - last >= secs:
                    self._last_scheduled[name] = last = now_t
                    self.store.add_wakeup(name, f"scheduled check-in (every {agent.every})")
                if st.get("next_scheduled_at") != last + secs:
                    self.store.set_agent_state(name, next_scheduled_at=last + secs)
            if agent.at and due_at(agent.at, st.get("last_at_fire"), now_t):
                self.store.set_agent_state(name, last_at_fire=now_t)
                self.store.add_wakeup(name, f"scheduled daily session ({agent.at})")

    def _reap_residents(self) -> None:
        """The process manager for resident agents. A resident process that has exited
        between turns is recorded as a crash, its session marked dead, and it is dropped so
        the next tick starts it again. Three crashes in fifteen minutes is a crash loop: the
        agent is paused with the reason, and the Supervisor is woken to look at it."""
        now_t = time.time()
        for name in list(self.agent_procs):
            if name in self.agent_threads:
                continue  # mid-turn: the turn's own code reports how it ended
            proc = self.agent_procs[name]
            if proc.alive():
                continue
            self.agent_procs.pop(name)
            try:
                proc.close()
            except Exception as e:  # noqa: BLE001 - the process is gone either way
                log(f"{proc.session_id}: close after exit: {e!r}")
            sid = proc.session_id
            tail = ""
            try:
                tail = (self.project.logs_dir / "agents" / f"{sid}.stderr.log").read_text(encoding="utf-8")[-400:]
            except OSError:
                pass
            self.store.set_session(sid, status="idle", pid=None, phase="idle", tool_name=None)
            st = self.store.agent_state(name)
            crashes = [t for t in st.get("crashes", []) if now_t - t < self.CRASH_LOOP_WINDOW] + [now_t]
            self.store.set_agent_state(name, crashes=crashes)
            self.events.emit("agent.crashed", session=sid, agent=name, count=len(crashes), stderr=tail)
            log(f"{sid}: process exited between turns ({len(crashes)} in {self.CRASH_LOOP_WINDOW // 60}m); will restart")
            if len(crashes) >= self.CRASH_LOOP_COUNT:
                self.store.set_agent_state(name, paused=True, crash_loop=f"{len(crashes)} crashes in {self.CRASH_LOOP_WINDOW // 60} minutes")
                self.events.emit("agent.crash_loop", session=sid, agent=name, count=len(crashes), stderr=tail)
                self.store.add_wakeup("supervisor", f"agent {name} crashed {len(crashes)} times in {self.CRASH_LOOP_WINDOW // 60} minutes and has been "
                                      f"paused. Its last stderr: {tail.strip()[-200:] or '(empty)'}. Fix its definition or ask the human, then resume it.")
                log(f"{sid}: crash loop; paused and the supervisor woken")

    def _spawn(self, table: dict, key: str, fn, *args) -> None:
        th = threading.Thread(target=fn, args=args, daemon=True, name=key)
        table[key] = th
        th.start()

    def _session(self, session_id: str, role: str, cwd: Path, ticket_id: str = "") -> AgentSession:
        """A session for a built-in agent: worker, reviewer, or one of the residents."""
        spec = ROLES[role]
        tools = tuple(spec.tools) if role in ("worker", "reviewer") else effective_tools(
            self.agents.get(role) or all_agents(self.project)[role], self.role_defs or all_roles(self.project), self.teams or all_teams(self.project))
        return self._open_session(session_id, role, agents.role_prompt(self.project, spec.prompt), tools, cwd, ticket_id)

    def _session_running(self, sid: str, role: str, proc: AgentSession, ticket_id: str | None = None) -> None:
        self.live_procs[sid] = proc
        try:
            proc.open()
        except Exception as e:  # noqa: BLE001 - a session that will not open still gets its turn; say what happened
            log(f"{sid}: could not open the session yet: {e!r}")
        self.store.set_session(sid, kind=role, status="running", ticket_id=ticket_id, pid=self._pid(proc), instance=self._slots.get(sid, role),
                               session_file=proc.session_file, harness=proc.harness_name, started_at=time.time(), last_text="",
                               partial_text="", thinking_chars=0,
                               last_output_at=None, phase="prompting", phase_since=time.time(),
                               last_token_at=None, tool_name=None)
        self.events.emit("session.started", session=sid, kind=role, ticket=ticket_id)
        self.store.measure_turn(role, self._slots.get(sid, role), started=True)

    def _session_idle(self, sid: str, text: str, resident: bool = False) -> None:
        proc = self.live_procs.pop(sid, None) if not resident else self._proc_for(sid)
        fields = {} if resident else {"pid": None}
        if proc is not None and proc.session_file:
            fields["session_file"] = proc.session_file   # some harnesses only name the transcript after the first turn
        self.store.set_session(sid, status="idle", last_text=(text or "")[-500:], settled_at=time.time(), phase="idle", tool_name=None,
                               **fields)
        self.events.emit("session.settled", session=sid)
        s = self.store.find_session(sid) or {}
        self.store.measure_turn(s.get("kind") or sid, self._slots.get(sid, s.get("kind") or sid), started=False)
        if not resident:
            self._release(sid)

    def _note_turn(self, sid: str, res, kind: str = "") -> None:
        """A turn that Orchestra cut off, or that the harness failed, is recorded as an event
        with what the agent was doing at that moment, so the transcript can show the stop
        instead of just ending. Pi's session file is still the only transcript; this is
        the event log, which the transcript view overlays."""
        if not (res.timed_out or res.error):
            return
        s = self.store.find_session(sid) or {}
        now_t = time.time()
        if res.error and self.stop.is_set():
            # The harness "failed" because this loop is shutting down and closed it.
            self.events.emit("turn.interrupted", session=sid, agent=kind or s.get("kind"), ticket=s.get("ticket_id") or None,
                             reason="orchestra shut down (restart or stop) during this turn")
            return
        if res.timed_out:
            phase = s.get("phase") or "working"
            doing = f"running `{s['tool_name']}`" if phase == "tool" and s.get("tool_name") else \
                "generating a reply" if phase == "generating" else "waiting for the model to start replying" if phase == "prompting" else phase
            by = getattr(res, "stopped_by", None) or "limit"
            self.events.emit("turn.timed_out", session=sid, agent=kind or s.get("kind"), ticket=s.get("ticket_id") or None,
                             stopped_by=by, limit=self.cfg.silence_timeout if by == "silence" else self.cfg.turn_timeout, doing=doing,
                             phase_for=round(now_t - s["phase_since"]) if s.get("phase_since") else None,
                             silent_for=round(now_t - s["last_output_at"]) if s.get("last_output_at") else None,
                             last_text=(res.text or s.get("last_text") or "")[-300:])
        else:
            self.events.emit("turn.error", session=sid, agent=kind or s.get("kind"), ticket=s.get("ticket_id") or None, error=str(res.error)[:500])

    def _proc_for(self, sid: str) -> AgentSession | None:
        if sid in self.live_procs:
            return self.live_procs[sid]
        return next((p for p in self.agent_procs.values() if p.session_id == sid), None)

    def _prompt_with_retry(self, proc: AgentSession, ticket_id: str, task: str, nudge: str) -> tuple[dict | None, str, bool]:
        res = proc.prompt(task, self.cfg.turn_timeout or None, on_event=self._on_event(proc.session_id),
                          idle_timeout=self.cfg.silence_timeout)
        self._note_turn(proc.session_id, res)
        report = self.store.pop_report(ticket_id)
        if report or res.timed_out or res.error:
            return report, res.text, res.timed_out or bool(res.error)
        res = proc.prompt(nudge, 300, on_event=self._on_event(proc.session_id), idle_timeout=self.cfg.silence_timeout)
        self._note_turn(proc.session_id, res)
        return self.store.pop_report(ticket_id), res.text, res.timed_out or bool(res.error)

    def _on_event(self, sid: str):
        """Translate a session's normalized event stream into the phase the UI shows: waiting
        for the model, generating, or inside a tool. Nothing here is harness-specific."""
        def fn(ev: dict) -> None:
            t = ev.get("type")
            now_t = time.time()
            if t == "turn_start":
                self.store.set_session(sid, phase="prompting", phase_since=now_t, tool_name=None)
            elif t == "assistant_start":
                self.store.set_session(sid, phase="generating", phase_since=now_t, last_token_at=now_t)
            elif t == "token":
                # Many small events while tokens stream: accumulate the reply in progress here
                # and write it, with the timestamp, every few seconds. Thinking is counted, not kept.
                part = self._partial.setdefault(sid, {"text": "", "thinking": 0})
                if ev.get("kind") == "text" and ev.get("delta"):
                    part["text"] = (part["text"] + ev["delta"])[-4000:]
                elif ev.get("kind") == "thinking" and ev.get("delta"):
                    part["thinking"] += len(ev["delta"])
                st = self._last_token_write.get(sid, 0)
                if now_t - st > 3:
                    self._last_token_write[sid] = now_t
                    self.store.set_session(sid, phase="generating", last_token_at=now_t, last_output_at=now_t,
                                           partial_text=part["text"], thinking_chars=part["thinking"])
            elif t == "tool_start":
                name = ev.get("name", "?")
                self.store.set_session(sid, phase="tool", phase_since=now_t, tool_name=name, last_output_at=now_t,
                                       last_text=f"tool: {name}")
            elif t == "tool_end":
                self.store.set_session(sid, phase="prompting", phase_since=now_t, tool_name=None, last_output_at=now_t)
            elif t == "assistant_end":
                fields = {"last_output_at": now_t, "last_token_at": now_t, "partial_text": "", "thinking_chars": 0}
                self._partial.pop(sid, None)   # the message is in the transcript now
                txt = (ev.get("text") or "").strip()
                if txt:
                    fields["last_text"] = txt[-500:]
                self.store.set_session(sid, **fields)
        return fn

    def _start_project_agents(self) -> None:
        """Start the resident supervisor and coordinator sessions up
        front so they live for the life of `orchestra-agents run`."""
        for target in RESIDENT_AGENTS:
            sid = ROLES[target].session_id
            try:
                proc = self._session(sid, target, self.project.repo_root)
                proc.open()
                self.agent_procs[target] = proc
                self.store.set_session(sid, kind=target, status="idle", pid=self._pid(proc),
                                       session_file=proc.session_file, harness=proc.harness_name)
                log(f"{sid}: session resident ({self.harness.name})")
            except Exception as e:  # noqa: BLE001 - one agent that cannot start never stops the others
                log(f"{sid}: could not start: {e}")

    def _start_control(self) -> None:
        port = self.cfg.control_port                     # 0: any free port; it is recorded for the web
        try:
            self.control = ControlServer(port, self.prompt_supervisor)
            self.control.start()
            port = self.control.port
            self.store._mutate_runtime(lambda d: d.__setitem__("control", {"port": port, "pid": os.getpid()}))
            log(f"control endpoint on 127.0.0.1:{port} (orchestra-agents chat and the web attach here)")
        except OSError as e:
            log(f"control endpoint unavailable on port {port}: {e}")

    def prompt_supervisor(self, message: str, emit) -> dict:
        """One supervisor turn on the resident session, with the event digest prefix."""
        self._trim_if_large(self.agent_procs.get("supervisor"), "supervisor", "supervisor")
        proc = self.agent_procs.get("supervisor")
        if proc is None or not proc.alive():
            proc = self._session("supervisor", "supervisor", self.project.repo_root)
            proc.open()
            self.agent_procs["supervisor"] = proc
        new = [e for e in self._digest.new() if e["type"] not in ("session.started", "session.settled")]
        prefix = agents.supervisor_context(tooling.snapshot(self.project, self.store, "supervisor"), new, self.project, self.store)
        self._session_running("supervisor", "supervisor", proc)

        def on_event(ev: dict) -> None:
            self._on_event("supervisor")(ev)
            if ev.get("type") in ("tool_start", "assistant_end"):
                emit(ev)

        res = proc.prompt(prefix + message, self.cfg.turn_timeout or None, on_event=on_event, idle_timeout=self.cfg.silence_timeout)
        self._note_turn("supervisor", res, "supervisor")
        self._session_idle("supervisor", res.text, resident=True)
        return {"text": res.text, "error": res.error, "timed_out": res.timed_out}

    def _trim_if_large(self, proc, sid: str, agent: str) -> str | None:
        """Keep every session's context bounded. When the transcript has grown past
        `session_max_bytes` since it was last compacted, ask the harness to compact: older
        messages become a summary and the agent keeps what it learned, which is what
        matters 200k tokens into a task. Only when the harness cannot compact (or the
        attempt fails) is the session rotated: archived as a run and started fresh, with
        the wake-up or task carrying the current state. Returns "compacted", "rotated",
        or None when nothing was needed."""
        limit = self.cfg.session_max_bytes
        s = self.store.find_session(sid) or {}
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
                # The harness bounds its own context (Claude Code's --autocompact); nothing to do but note it.
                self.store.set_session(sid, compacted_at_bytes=Path(f).stat().st_size)
                self.events.emit("agent.compacted", session=sid, agent=agent, mode="auto", limit=res.get("limit"),
                                 reason=f"the harness compacts itself past {int(res.get('limit') or 0):,} tokens")
                return "compacted"
            if res:
                self.store.set_session(sid, compacted_at_bytes=Path(f).stat().st_size, compactions=int(s.get("compactions") or 0) + 1)
                self.events.emit("agent.compacted", session=sid, agent=agent, tokens_before=res.get("tokens_before"),
                                 tokens_after=res.get("tokens_after"), reason=f"{grown // 1000} KB of transcript since the last compaction")
                log(f"{sid}: compacted ({res.get('tokens_before')} -> {res.get('tokens_after')} tokens)")
                return "compacted"
        # no compaction on this harness, or it failed: rotate
        owned = self.agent_procs.get(agent)
        if owned is not None and owned.session_id == sid:
            self.agent_procs.pop(agent).close()
        elif proc is not None:
            proc.close()
        archived = self.store.retire_session(sid)
        self.store.set_agent_state(agent, context_hash=None)
        self.events.emit("agent.reset", session=sid, agent=agent, archived=archived, by="orchestra",
                         reason=f"transcript reached {size // 1000} KB and the harness could not compact it")
        log(f"{sid}: transcript {size // 1000} KB, no compaction; archived as {archived}, starting fresh")
        return "rotated"

    def _wake(self, target: str, reasons: list[str]) -> None:
        agent = self.agents.get(target)
        if agent is None:
            return
        sid = agent.session_id
        ephemeral = agent.ephemeral
        if ephemeral:
            sid = f"{agent.session_id}-{time.strftime('%Y%m%d-%H%M%S')}"
            proc = self._session_for(agent, sid)
        else:
            self._trim_if_large(self.agent_procs.get(target), sid, target)
            proc = self.agent_procs.get(target)
            if proc is None or not proc.alive():
                proc = self._session(sid, target, self.project.repo_root) if agent.builtin else self._session_for(agent, sid)
                self.agent_procs[target] = proc
        self.store.set_agent_state(target, last_woken=time.time(), turn_depth=self._pending_depth.pop(target, 0))
        log(f"{sid}: waking ({len(reasons)} reason(s))")
        self._session_running(sid, target, proc)
        snap = tooling.snapshot(self.project, self.store, target)
        task = agents.wake_task(target, reasons, snap)
        if target == "supervisor":
            task = ("[Orchestra: escalation from another agent; the human is not present for this turn. "
                    "Act with your tools if you can, otherwise leave a note the human will see.]\n\n"
                    + agents.supervisor_context(snap, [], self.project, self.store) + task)
        res = proc.prompt(task, self.cfg.turn_timeout or None, on_event=self._on_event(sid), idle_timeout=self.cfg.silence_timeout)
        self._note_turn(sid, res, target)
        self._session_idle(sid, res.text, resident=not ephemeral)
        if ephemeral:
            proc.close()
        self.events.emit("agent.said", session=sid, summary=(res.text or "")[:500])
        log(f"{sid}: {(res.text or '(no text)')[:160]!r}")
