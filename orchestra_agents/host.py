"""The agents' host (``orchestra-agents run``): the process that runs the AI agents as machines.

Each second it matches new events on the bus against every agent's wake rules and queues wake-ups,
queues the scheduled ones, runs the teams' probes, answers the task system's assignments for the
roles it serves (runner.py) and runs one turn per woken agent. The resident sessions are kept up
by residents.py, a turn's progress and its end are recorded by turns.py, and the Supervisor chat
is served here through the control endpoint (control.py). Which product runs a session is
invisible past ``harness``. Tickets are the task system's: the agents reach them only over the
bus (protocol.py)."""

from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path

import psutil
from orchestra.runtime import alive

from . import promises, prompting, tooling
from .clock import due_at, parse_interval
from .closing import CLOSE_S, close_together
from .config import Project
from .control import ControlServer
from .events import GAVE_UP, Events
from .fillers import Fillers
from .harness import AgentSession, SessionSpec, TurnResult, get_harness
from .landings import Landings
from .org import AgentDef, built_in_agents, effective_tools, event_matches, team_chain, tools_path
from .org_store import all_agents, all_teams
from .probes import Prober, team_probes
from .protocol import TRANSITIONED
from .residents import Residents
from .restart import recover as recover_restart
from .roles import all_roles
from .runner import AgentRunner
from .store import AgentStore, StoreError
from .turns import Turns

HOST_LOG: Path | None = None
HOST_LOG_MAX = 5_000_000
SHUTDOWN_REPORTS_S = 10.0    # how long shutdown waits for the open assignments' reports to go out
# shutdown closes the running turns together, waits for the reports, then closes the residents together
SHUTDOWN_WORST_S = CLOSE_S + SHUTDOWN_REPORTS_S + CLOSE_S
WAIT_RETRY_S = 2.0           # --wait: how often to look again for the previous host to be gone


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
    log = staticmethod(log)

    def __init__(self, project: Project, store: AgentStore, events: Events):
        global HOST_LOG
        HOST_LOG = project.logs_dir / "agents-host.log"
        self.project, self.store, self.events = project, store, events
        self.cfg = project.config
        self.harness = get_harness(self.cfg.harness)
        self.stop = threading.Event()
        self.agent_threads: dict[str, threading.Thread] = {}   # agent name -> the thread running its turn (a wake-up, or the Supervisor chat)
        self._turn_locks: dict[str, threading.Lock] = {}        # agent name -> one turn at a time on its session
        self.live_procs: dict[str, AgentSession] = {}   # session id -> session, while a per-ticket turn runs
        self.agents: dict[str, AgentDef] = {}
        self.teams: dict = {}
        self.role_defs: dict = {}
        self.control: ControlServer | None = None
        self._pending_depth: dict[str, int] = {}
        self._started_at = time.time()
        self._last_scheduled: dict[str, float] = {}
        self._slots: dict[str, str] = {}               # session id -> the machine instance it runs as ("worker/1")
        self._announced: set[tuple[str, str, str]] = set()   # (source, harness, model) announced in this host's lifetime
        self._wake_at = events.end()                   # events that wake agents: only those after startup
        self._digest_at = events.end()                 # what the Supervisor is told happened since its last turn
        self.fillers = Fillers(events)                 # who is seen filling the task system's roles, for the planners
        self.landings = Landings(store)                # the human's tickets, followed until they are done
        self.runner = AgentRunner(self)                # answers the task system's assignments
        self.residents = Residents(self)               # the resident sessions
        self.turns = Turns(self)                       # a turn's progress and how it ended
        self.prober = Prober(project, store, events)   # the teams' probes

    def recover(self) -> None:
        """Turns the previous host left running: their processes are stopped (no orphans) and
        their sessions marked idle, with an event so the transcript shows the cut."""
        for s in self.store.sessions():
            if s.get("status") == "running" and s.get("pid"):
                self._kill_orphan(int(s["pid"]), s["id"])
            if s.get("status") == "running":
                self._mark("session.interrupted", session=s["id"], agent=s.get("kind"), ticket=s.get("ticket_id") or None,
                           reason="the agents' host was not running (it crashed or was killed) during this turn")
            self.store.set_session(s["id"], status="idle", pid=None)

    def run(self, once: bool = False, wait: bool = False) -> int:
        """The host until it is stopped. Returns the exit status: 1 when another host already
        holds this project (two would kill each other's turns and both answer every assignment),
        or when the control endpoint cannot be served (without it the Supervisor cannot be
        reached and the web cannot see the host: it must not run as a ghost). With ``wait`` (as
        Orchestra's launcher starts it) it waits for either to clear instead: a host restarted
        while the previous one still shuts down takes over when that one is gone."""
        def _stop(_sig, _frame):
            self.stop.set()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _stop)
            except ValueError:  # not the main thread (tests)
                pass
        if not self._take_project(wait):
            return 0 if self.stop.is_set() else 1
        self.recover()
        if put_back := recover_restart(self.project):      # a restart_machine the last host was killed in
            log(put_back)
        # the host announces what it acts on as itself: a finished ticket's sessions go to the
        # archive. Assignments, releases and attention are the agents', which announce their own
        self.events.publish("orchestra.machine.started", {"package": "agents", "mode": "continuous", "kind": "program",
                                                          "listens_for": [TRANSITIONED], "log": str(HOST_LOG)})
        log(f"project {self.project.key} on {self.project.repo_root}; harness {self.harness.name}; serving {', '.join(self.cfg.serves) or 'no role'}")
        self._refresh_org(self.store.runtime()["agent_state"])
        self.store.measure_idle(self.measured_roles())           # no agent is in a turn at the start: the level is live from here
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
            try:
                self._shutdown_markers()
                close_together(list(self.live_procs.values()),                    # mid-turn: no orphans
                               lambda _s, e: log(f"closing a running session: {e!r}"))
                deadline = time.time() + SHUTDOWN_REPORTS_S     # each open assignment reports that it failed
                for th in list(self.runner.threads.values()):
                    th.join(max(0.1, deadline - time.time()))
                self.residents.close_all()
            finally:
                if self.control:
                    self.control.stop()
                self.store.clear_control(os.getpid())
        return 0

    def measured_roles(self) -> list[str]:
        """The roles whose sessions are measured (``agents_working{role}``): the resident agents
        and the task system's roles this host serves."""
        return sorted({name for name, a in self.agents.items() if a.resident} | set(self.cfg.serves))

    def _busy(self) -> bool:
        return self.runner.busy() or any(t.is_alive() for t in self.agent_threads.values())

    def idle(self, up_to: int) -> bool:
        """Nothing left to do for the bus as it stood at ``up_to``: both readers of the bus have passed it,
        no turn or assignment thread is alive, and no wake-up is queued."""
        return (self._wake_at >= up_to and self.runner.offset >= up_to and not self._busy()
                and not self.store.runtime()["wakeups"])

    def tick(self) -> None:
        self.project.reload_config()
        self.cfg = self.project.config
        for k in [k for k, th in self.agent_threads.items() if not th.is_alive()]:
            self.agent_threads.pop(k)
        self.residents.reap()
        states = self.store.runtime()["agent_state"]      # one read per tick: the agents' paused, deleted, requested flags
        self._refresh_org(states)
        try:
            self.prober.tick(team_probes(self.project, self.teams))
        except Exception as e:  # noqa: BLE001 - a broken probe never stops the host
            log(f"probes: {e!r}")
        self._schedule_wakeups(states)
        self._wake_on_events()
        for target in list(self.agents):
            if target == "supervisor" and self.residents.procs.get("supervisor") is None:
                continue
            if self.agents[target].builtin and not self.agents[target].resident:
                continue  # workers and reviewers answer assignments, not wake-ups
            if target not in self.agent_threads:
                st = states.get(target) or {}
                if st.get("paused") or st.get("deleted"):
                    continue  # wake-ups stay queued until resumed
                reasons = list(dict.fromkeys(w["reason"] for w in self.store.drain_wakeups(target)))
                if reasons:
                    self._spawn(self.agent_threads, target, self._wake, target, reasons)
        self.runner.tick()

    @staticmethod
    def _kill_orphan(pid: int, session_id: str) -> None:
        """A turn's process outlived the host that started it (the host was killed): stop it, so
        it neither keeps working unseen nor holds its session against the retry."""
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return
            try:
                os.killpg(pid, signal.SIGTERM)            # each turn runs in a process group of its own
            except ProcessLookupError:
                os.kill(pid, signal.SIGTERM)              # a pid that is not a group leader: the process itself
            log(f"{session_id}: stopped its orphaned process {pid}")
        except (psutil.NoSuchProcess, ProcessLookupError, PermissionError, OSError):
            pass

    def _mark(self, type: str, **data) -> dict:
        """An event about a session that its transcript should show (web.py overlays it)."""
        ev = self.events.emit(type, **data)
        self.store.add_marker(data["session"], ev)
        return ev

    def _shutdown_markers(self) -> None:
        """Every turn still running is about to be cut off by this shutdown. Say so in the
        event log, so the transcript does not look like the agent stopped on its own."""
        for s in self.store.sessions():
            if s.get("status") == "running":
                self._mark("session.interrupted", session=s["id"], agent=s.get("kind"), ticket=s.get("ticket_id") or None,
                           reason="orchestra shut down (restart or stop) during this turn")

    def _refresh_org(self, states: dict) -> None:
        """Reload roles, teams and agents from their stores; close the resident sessions of agents
        that were deleted, paused or asked to restart or reset, and start the ones missing."""
        try:
            roles, teams, agents_ = all_roles(self.project), all_teams(self.project), all_agents(self.project)
        except Exception as e:  # noqa: BLE001 - a broken record is logged; the host keeps running
            log(f"org: could not load roles, teams, or agents: {e}")
            return
        self.residents.stop_changed(agents_, states)
        self.role_defs, self.teams, self.agents = roles, teams, agents_
        self.residents.start_missing(states)

    def _session_for(self, agent: AgentDef, session_id: str) -> AgentSession:
        """A session for an agent: its roles' prompts plus its team's responsibilities and access,
        its working directory in the team's space, and the team chain's tools/ directories on
        PATH. A built-in agent in the built-in organisation gets its role prompt and the
        repository root (prompting.agent_prompt makes the same distinction)."""
        chain = team_chain(self.teams, agent.team)
        team = chain[0] if chain and not (agent.builtin and all(t.builtin for t in chain)) else None
        cwd = self.project.repo_root
        extra: dict[str, str] = {}
        if team is not None:
            cwd = team.space_dir(self.project)
            (cwd / "tools").mkdir(parents=True, exist_ok=True)
            dirs = [str(p) for p in tools_path(self.project, self.teams, agent)]
            extra["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
            extra["ORCHESTRA_TEAM"] = team.name
        return self._open_session(session_id, agent.name, prompting.agent_prompt(self.project, agent, self.role_defs, self.teams),
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
        proc = self.harness.start(SessionSpec(
            session_id=session_id, session_dir=self.project.sessions_dir, cwd=cwd,
            system_prompt=system_prompt, role=role, tool_groups=tools,
            env={"ORCHESTRA_ROLE": role, "ORCHESTRA_REPO": str(self.project.repo_root), "ORCHESTRA_TICKET": ticket_id,
                 "ORCHESTRA_SOURCE": source, **(extra_env or {})},
            log_path=self.project.logs_dir / "agents" / f"{session_id}.stderr.log",
            model=self.cfg.model, provider=self.cfg.provider, thinking=self.cfg.thinking, compact_tokens=self.cfg.compact_tokens))
        self._announce(source, role)               # once it exists: a session that failed to start is no machine
        return proc

    def _instance(self, session_id: str, role: str, ticket_id: str = "") -> str:
        """The machine instance a session runs as, which is also the source of its events. A
        worker or reviewer for one ticket takes the lowest free slot of its role ("worker/1"),
        given back when its turn ends; numbered slots are taken only on the runner's tick (the
        main thread), so two offers in one tick cannot take the same slot. A resident or an
        ephemeral session is bound to its role name, from whichever thread opens it."""
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
        """The slot and the binding go together; releasing a session that never took one is nothing."""
        if self._slots.pop(session_id, None) is not None:
            self.events.unbind(session_id)

    def _announce(self, source: str, role: str) -> None:
        """An agent instance says what it is when it starts (AGENTS.md: a machine announces its
        mode and what it listens for): an AI agent, on which harness and model, engaged how. A
        resident agent is continuous (its session is always up; a schedule is one of its wake
        rules), a per-ticket or ephemeral one ad hoc. Once per source in the host's lifetime
        (``reviewer/1`` is one machine instance however many tickets it reviews), again only when
        the harness or the model changed."""
        key = (source, self.harness.name, self.cfg.model)
        if key in self._announced:
            return
        self._announced.add(key)
        agent = self.agents.get(role) or all_agents(self.project).get(role)
        mode = "continuous" if agent is not None and agent.resident else "ad hoc"
        listens = sorted({w.split(":")[0] for w in (agent.wake_on if agent is not None else [])})
        self.events.publish("orchestra.machine.started", {"package": "agents", "mode": mode, "listens_for": listens, "kind": "agent", "role": role,
                                                          "harness": self.harness.name, "model": self.cfg.model},
                            source=source)

    @staticmethod
    def _pid(proc: AgentSession) -> int | None:
        """The harness's own process, when it keeps one between turns; None otherwise (a harness
        that starts a process per turn names it in ``turn_start``, turns.py). Never this host's
        pid: a recorded pid is what a later host stops as an orphan."""
        return proc.pid if proc.alive() else None

    def _wake_on_events(self) -> None:
        """Match new events against every agent's wake_on list and queue wake-ups. Workers and
        reviewers are not woken: their events are assignments, answered by the runner. A ticket
        the Supervisor created for the human that is done wakes it to tell the human (landings.py)."""
        evs, self._wake_at = self.events.after(self._wake_at)
        for ev in evs:
            if landed := self.landings.see(ev, self.cfg):
                self.store.add_wakeup("supervisor", landed)
            for name, agent in self.agents.items():
                if (agent.builtin and not agent.resident) or not event_matches(agent.wake_on, ev):
                    continue
                self.store.add_wakeup(name, self._wake_reason(ev))
                self._pending_depth[name] = max(self._pending_depth.get(name, 0), int(ev.get("depth", 0)))

    def _wake_reason(self, ev: dict) -> str:
        """What a wake-up says about the event that caused it: its type and the fields an agent
        acts on; for a process Orchestra gave up on, which one, how it exited and where its log is."""
        if ev["type"] == GAVE_UP:
            name = ev.get("name")
            log = self.project.orchestra.logs_dir / "machines" / f"{name}.log"
            return (f"event {GAVE_UP}: Orchestra's launcher gave up on the process {name} after {ev.get('failures')} "
                    f"failures in a row, each soon after it started (last exit code {ev.get('code')}); it is not restarted. "
                    f"Its log: {log} (also on the Core page)")
        bits = [f"{k}={ev[k]}" for k in ("ticket", "milestone", "from", "to", "reason", "by", "probe", "subject", "note", "output") if ev.get(k)]
        return f"event {ev['type']}" + (" " + " ".join(str(b)[:300] for b in bits) if bits else "")

    def _schedule_wakeups(self, states: dict) -> None:
        """Queue a wake-up for agents whose interval has elapsed or whose daily time has come."""
        now_t = time.time()
        for name, agent in self.agents.items():
            st = states.get(name) or {}
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

    def _spawn(self, table: dict, key: str, fn, *args) -> None:
        th = threading.Thread(target=fn, args=args, daemon=True, name=key)
        table[key] = th
        th.start()

    def _session_running(self, sid: str, role: str, proc: AgentSession, ticket_id: str | None = None) -> None:
        if ticket_id:
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
        self._mark("session.started", session=sid, kind=role, ticket=ticket_id)
        self.store.measure_turn(role, self._slots.get(sid, role), started=True)

    def _session_idle(self, sid: str, text: str, resident: bool = False) -> None:
        proc = self.live_procs.pop(sid, None) if not resident else self._proc_for(sid)
        fields = {"pid": self._pid(proc) if resident and proc is not None else None}   # the turn's process is gone; a resident's stays
        if proc is not None and proc.session_file:
            fields["session_file"] = proc.session_file   # some harnesses only name the transcript after the first turn
        self.store.set_session(sid, status="idle", last_text=(text or "")[-500:], settled_at=time.time(), phase="idle", tool_name=None,
                               **fields)
        self.events.emit("session.settled", session=sid)
        s = self.store.find_session(sid) or {}
        self.store.measure_turn(s.get("kind") or sid, self._slots.get(sid, s.get("kind") or sid), started=False)
        if not resident:
            self._release(sid)

    def _proc_for(self, sid: str) -> AgentSession | None:
        if sid in self.live_procs:
            return self.live_procs[sid]
        return next((p for p in self.residents.procs.values() if p.session_id == sid), None)

    def _take_project(self, wait: bool) -> bool:
        """Hold the project and serve the control endpoint. False when another live host holds the
        project or the port cannot be bound (said once in the log); with ``wait``, it looks again
        every WAIT_RETRY_S until it can, or until the host is stopped."""
        said = None
        while True:
            other = (self.store.runtime().get("control") or {}).get("pid")
            why = None
            if other and other != os.getpid() and alive(other):
                why = f"another orchestra-agents run holds this project (pid {other})"
            else:
                try:
                    self._start_control()
                    return True
                except OSError as e:
                    why = f"control endpoint unavailable on port {self.cfg.control_port}: {e}"
            if not wait:
                log(f"{why}; exiting")
                return False
            if why != said:
                log(f"{why}; waiting for it to clear")
                said = why
            if self.stop.wait(WAIT_RETRY_S):
                return False

    def _start_control(self) -> None:
        """Serve the Supervisor chat; raises OSError when the port cannot be bound."""
        port = self.cfg.control_port                     # 0: any free port; it is recorded for the web
        self.control = ControlServer(port, self.prompt_supervisor)
        self.control.start()
        self.store.set_control(self.control.port, os.getpid())
        log(f"control endpoint on 127.0.0.1:{self.control.port} (orchestra-agents chat and the web attach here)")

    def _turn_lock(self, name: str) -> threading.Lock:
        """One turn at a time on an agent's session: a wake-up (tick) and the Supervisor chat
        (the control endpoint's thread) both take it for the whole turn."""
        return self._turn_locks.setdefault(name, threading.Lock())

    def prompt_supervisor(self, message: str, emit) -> dict:
        """One supervisor turn on the resident session, with the event digest prefix. While it
        runs, the chat's thread stands in agent_threads like a wake-up's, so the tick queues
        wake-ups behind it and a pause, restart or reset waits for it to end."""
        st = self.store.agent_state("supervisor")
        if st.get("paused") or st.get("deleted"):
            why = st.get("crash_loop") or "by request"
            raise StoreError(f"the Supervisor is {'deleted' if st.get('deleted') else 'paused'} ({why}); its messages wait until it is resumed")
        with self._turn_lock("supervisor"):
            self.agent_threads["supervisor"] = threading.current_thread()
            try:
                return self._supervisor_turn(message, emit)
            finally:
                if self.agent_threads.get("supervisor") is threading.current_thread():
                    self.agent_threads.pop("supervisor")

    def _supervisor_turn(self, message: str, emit) -> dict:
        self.residents.trim_if_large(self.residents.procs.get("supervisor"), "supervisor", "supervisor")
        proc = self.residents.procs.get("supervisor")
        if proc is None or not proc.alive():
            proc = self._session_for(self.agents.get("supervisor") or built_in_agents()["supervisor"], "supervisor")
            proc.open()
            self.residents.procs["supervisor"] = proc
        evs, self._digest_at = self.events.after(self._digest_at)
        new = [e for e in evs if e["type"] not in ("session.started", "session.settled")]
        prefix = prompting.supervisor_context(tooling.snapshot(self.store, "supervisor"), new, self.project, self.store,
                                              roles=self.fillers.lines(self.cfg.serves))
        self._session_running("supervisor", "supervisor", proc)
        on_session_event = self.turns.on_event("supervisor")

        def on_event(ev: dict) -> None:
            on_session_event(ev)
            if ev.get("type") in ("tool_start", "assistant_end"):
                emit(ev)

        res = self._honest(proc, prompting.chat_turn(prefix, message), on_event)
        self.turns.note("supervisor", res, "supervisor")
        self._session_idle("supervisor", res.text, resident=True)
        self.events.emit("agent.said", session="supervisor", summary=(res.text or "")[:500])
        return {"text": res.text, "error": res.error, "timed_out": res.timed_out}

    def _honest(self, proc: AgentSession, text: str, on_event) -> TurnResult:
        """A Supervisor turn whose reply is for the human. A reply that claims an action the turn
        did not take (promises.py) gets one more turn on the same session to take it or say it is
        not done; the human gets both, the correction after the reply."""
        calls = [0]

        def counting(ev: dict) -> None:
            if ev.get("type") == "tool_start":
                calls[0] += 1
            on_event(ev)

        limits = {"timeout": self.cfg.turn_timeout or None, "idle_timeout": self.cfg.silence_timeout}
        res = proc.prompt(text, on_event=counting, **limits)
        said = None if res.error or res.timed_out else promises.unkept(res.text, calls[0])
        if said is None:
            return res
        log(f"supervisor: the reply claims an action the turn did not take ({said[:120]!r}); one more turn to do it or say so")
        again = proc.prompt(promises.follow_up(said), on_event=on_event, **limits)
        if again.error or again.timed_out or not again.text.strip():
            return res
        return TurnResult(text=f"{res.text.rstrip()}\n\n{again.text.strip()}")

    def _wake(self, target: str, reasons: list[str]) -> None:
        agent = self.agents.get(target)
        if agent is None:
            return
        with self._turn_lock(target):
            self._wake_turn(agent, target, reasons)

    def _wake_turn(self, agent: AgentDef, target: str, reasons: list[str]) -> None:
        sid = agent.session_id
        snap = tooling.snapshot(self.store, target)
        if snap.get("unavailable") and all(r.startswith(("scheduled check-in", "scheduled daily session")) for r in reasons):
            log(f"{sid}: the task system did not answer; the scheduled wake-up is skipped")   # a check-in on no picture is a wasted turn
            return
        ephemeral = agent.ephemeral
        if ephemeral:
            sid = f"{agent.session_id}-{time.strftime('%Y%m%d-%H%M%S')}"
            proc = self._session_for(agent, sid)
        else:
            self.residents.trim_if_large(self.residents.procs.get(target), sid, target)
            proc = self.residents.procs.get(target)
            if proc is None or not proc.alive():
                proc = self._session_for(agent, sid)
                self.residents.procs[target] = proc
        self.store.set_agent_state(target, last_woken=time.time(), turn_depth=self._pending_depth.pop(target, 0))
        log(f"{sid}: waking ({len(reasons)} reason(s))")
        self._session_running(sid, target, proc)
        task = prompting.wake_task(target, reasons, snap, self.fillers.lines(self.cfg.serves))
        if target == "supervisor":
            task = prompting.SUPERVISOR_WAKE + prompting.supervisor_context(snap, [], self.project, self.store) + task
            res = self._honest(proc, task, self.turns.on_event(sid))
        else:
            res = proc.prompt(task, self.cfg.turn_timeout or None, on_event=self.turns.on_event(sid), idle_timeout=self.cfg.silence_timeout)
        self.turns.note(sid, res, target)
        self._session_idle(sid, res.text, resident=not ephemeral)
        if ephemeral:
            proc.close()
        self.events.emit("agent.said", session=sid, summary=(res.text or "")[:500])
        log(f"{sid}: {(res.text or '(no text)')[:160]!r}")
