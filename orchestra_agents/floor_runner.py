"""The AI agents' side of the Workflows floor (floor.py; orchestra-workflows' machine contract),
as runner.py is the task system's: for each ``offer.made`` with a role the agents serve here
(floor_roles.py) it announces the instance, claims the offer, reads the claim back (a loser says
nothing more and starts no session), runs the agent's turn in the handed worktree with the brief,
says ``offer.alive`` within ``heartbeat_s`` with what the agent is saying now, and finishes with
the outcome the agent's exit tool reported, or ``blocked`` with why (the BLOCKED rule). An
``offer.withdrawn`` naming the offer or its holder kills the turn, and nothing more is said for it.

A turn runs in a session per agent and item (``<agent>-<item>``), so rework that comes back to the
same instance (``to``) finds the conversation it left; its instance is a numbered slot of the
agent (``worker/1``, ``triage/1``), given back when the turn ends. Without a worktree among the
offer's resources the session is in the repository, read only. When the host stops mid-turn
nothing is finished: the floor takes the offer back when its lease runs out (there is no
machine-sent release), and offers it again."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from . import floor, prompting
from .builtins import ROLES as BUILTIN
from .floor_roles import extra_groups, served
from .offer_turn import heartbeat, run_turn
from .org import effective_tools
from .org_store import all_agents, all_teams
from .roles import all_roles

CONFIRM_S = 180.0      # how long a claim is read back for when the offer names no lease
CONFIRM_POLL_S = 0.2


def slug(item: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", item)[:80]


class FloorRunner:
    def __init__(self, host):
        self.host = host
        self.events = host.events
        self.offset = host.events.end()                      # offers made from now on
        self.threads: dict[str, threading.Thread] = {}      # offer -> the thread running its turn
        self.instances: dict[str, str] = {}                  # offer -> the instance that claimed it
        self.sessions: dict[str, str] = {}                   # offer -> its session id
        self.procs: dict = {}                                # offer -> its session, while open
        self.withdrawn: dict[str, str] = {}                  # offer -> why the floor took it back

    def busy(self) -> bool:
        return any(th.is_alive() for th in self.threads.values())

    def tick(self) -> None:
        for oid in [o for o, th in self.threads.items() if not th.is_alive()]:
            self.threads.pop(oid)
        start = self.offset                                   # every offer below is after this: a claim is read back from here
        evs, self.offset = self.events.after(self.offset)
        if not any(e.get("type") in floor.LISTENS_FOR for e in evs):
            return
        roles = served(self.host.project)
        for ev in evs:
            if ev.get("type") == floor.MADE and floor.for_role(ev, ev.get("role") or "") and ev["role"] in roles \
                    and ev["offer"] not in self.threads:
                self._offered(ev, roles[ev["role"]], start)
            elif ev.get("type") == floor.WITHDRAWN:
                for oid, inst in list(self.instances.items()):
                    if floor.stops(ev, oid, inst):
                        self.withdrawn[oid] = ev.get("reason") or "withdrawn"
                        proc = self.procs.get(oid)
                        if proc is not None:
                            try:
                                proc.kill()                   # the turn ends; turns.py records the interruption
                            except OSError:
                                pass

    def _offered(self, ev: dict, agent: str, start: int) -> None:
        """Take the offer as ``agent``, unless its item's session already runs a turn here. One
        addressed ``to`` an instance is taken only as that instance, when it is this agent's and free."""
        oid, sid = ev["offer"], f"{agent}-{slug(ev['item'])}"
        if sid in self.sessions.values() or sid in self.host.runner.sessions.values():
            return
        to = ev.get("to")
        if to:
            if to.partition("/")[0] != agent or (to in self.host._slots.values() and self.host._slots.get(sid) != to):
                return                                        # another machine's, or an instance of ours busy elsewhere
            self.host._slots[sid] = to
            self.host.events.bind(sid, to)
        instance = self.host._instance(sid, agent, oid)       # on the tick's thread, which alone allocates slots
        self.instances[oid], self.sessions[oid] = instance, sid
        th = threading.Thread(target=self._run, args=(ev, agent, sid, instance, start), daemon=True, name=oid)
        self.threads[oid] = th
        th.start()

    def _run(self, o: dict, agent: str, sid: str, instance: str, start: int) -> None:
        host, oid, item = self.host, o["offer"], o["item"]
        say = lambda kind, **body: self.events.publish(kind, {"offer": oid, **body}, source=instance, subject=item, causationid=oid)
        done = threading.Event()
        report, text, failed, lost = None, "", True, False
        try:
            host._announce(instance, agent, host.model_of(agent))   # before the claim: the floor reads its roles
            claim = say(floor.CLAIMED)
            if not self._held(o, instance, claim, start):
                lost = True
                return                                        # another claim came first, or it was withdrawn: nothing spent
            path = floor.workspace(o)
            cwd = Path(path) if path else host.project.repo_root
            system, tools = self._session_of(agent)
            task, nudge = floor.task_text(o, agent, path)
            every = float(o.get("heartbeat_s") or floor.HEARTBEAT_S)
            heartbeat(host, sid, done, lambda: oid in self.withdrawn, every, lambda t: say(floor.ALIVE, text=t or "working"))
            host.store.pop_report(oid)                        # nothing stale: the report is this turn's
            report, text, failed = run_turn(
                host, sid, agent, oid,
                lambda: host._open_session(sid, agent, system, tools, cwd, oid, read_only=path is None),
                self.procs, oid, task, nudge, lambda: self.withdrawn.get(oid),
                {"offer": oid, "offer_outcomes": list(o.get("outcomes") or []), "ticket_title": f"{item} ({o.get('step') or 'offer'})"},
                taken_by="the Workflows floor")
        except Exception as e:  # noqa: BLE001 - whatever happened, the offer finishes (the BLOCKED rule)
            text = f"{type(e).__name__}: {e}"
        finally:
            done.set()
            self.instances.pop(oid, None)
            self.sessions.pop(oid, None)
            self.procs.pop(oid, None)
            stopped = self.withdrawn.pop(oid, None) is not None or (host.stop.is_set() and failed)
            if not lost and not stopped:                     # withdrawn, or the host stopping: not this instance's to end
                outcome, summary = floor.outcome_of(report, o.get("outcomes"))
                if report is None and text:
                    summary = f"{summary}: {text}"[-2000:]
                try:
                    say(floor.FINISHED, outcome=outcome, summary=summary)
                except Exception as e:  # noqa: BLE001 - the lease takes it back
                    host.log(f"{oid}: could not finish: {e!r}")
            host._release(sid)

    def _session_of(self, agent: str) -> tuple[str, tuple[str, ...]]:
        """The system prompt and tool groups: a built-in role's own, or the agent's (its roles, its
        team, its machine spec) with the floor's exit tool."""
        host = self.host
        if agent in BUILTIN:
            spec = BUILTIN[agent]
            return prompting.role_prompt(host.project, spec.prompt), tuple(spec.tools)
        a = host.agents.get(agent) or all_agents(host.project)[agent]
        roles, teams = host.role_defs or all_roles(host.project), host.teams or all_teams(host.project)
        groups = effective_tools(a, roles, teams)
        return (prompting.agent_prompt(host.project, a, roles, teams),
                groups + tuple(g for g in extra_groups(host.project, agent) if g not in groups))

    def _held(self, o: dict, instance: str, claim: dict, start: int) -> bool:
        """Whether this instance holds the offer, read back from the bus from where the offer was
        read up to its own claim: the first claim after the offer, before any withdrawal."""
        oid = o["offer"]
        end = time.time() + float(o.get("lease_s") or CONFIRM_S)
        while time.time() < end:
            if oid in self.withdrawn:
                return False
            evs, _ = self.events.after(start)
            ids = [e.get("id") for e in evs]
            if claim.get("id") in ids:
                return floor.winner(evs[:ids.index(claim["id"]) + 1], oid) == instance
            time.sleep(CONFIRM_POLL_S)
        return False
