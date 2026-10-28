"""The agents in Orchestra's web app (an Orchestra plugin, ``orchestra/plugins.py``): the Agents
page (who is in the project, their roles, teams, wake-ups and the event map), the agents' session
transcripts, the Supervisor chat and the way to it on every page, and the agents' machine cards.
Its API is at ``/api/plugins/agents``. Besides the pages, the plugin declares the configured
agents (the machines it implements, for the Machines page before they run), the host that runs
them (``Plugin.processes``), which is no machine and so is declared as none, and the instrument
that reads their measurements (``Plugin.collectors``: ``agents.turns``, measures.py), also served
at ``/metrics``."""

from __future__ import annotations

import asyncio
import json
import shlex
import sys
import threading
import time
import tomllib
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from orchestra.collect.spec import Collector
from orchestra.launcher import Process
from orchestra.plugins import Declared, Page, Plugin
from orchestra.runtime import alive

from .api_org import read_org
from .config import ConfigError, Project
from .control import bearer as control_bearer
from .events import Events
from .harness import read_transcript
from .listening import listens_for
from .org import EVENT_HELP
from .org_store import all_agents
from .org_views import agents_status, metrics_text, residents_of, session_alive
from .store import AgentStore, StoreError

STATIC = Path(__file__).parent / "static"
CHAT_PING_SECONDS = 20   # keep the chat stream alive through proxies while the Supervisor thinks
STOP_GRACE_S = 30.0      # SIGTERM to SIGKILL for the host: above its worst-case shutdown (host.SHUTDOWN_WORST_S, 20 s)


def plugin(orchestra_project) -> Plugin:
    """The plugin for the repository Orchestra serves (its entry point, in pyproject.toml)."""
    project = Project.of(orchestra_project)
    project.ensure_dirs()
    return Plugin("agents", [Page("/agents", "Agents", STATIC / "agents.html")], STATIC, router(project),
                  scripts=["attention.js", "machine-cards.js"], stylesheets=["agents.css"],
                  machines=declared(project), processes=processes(project), collectors=collectors(project))


def set_up(project) -> bool:
    """Whether the project runs agents: its configuration has an ``[agents]`` section (in
    orchestra.toml or the owner's overlay; read through Orchestra, ``project.orchestra.config``)."""
    return "agents" in project.orchestra.config.raw


def processes(project) -> list[Process]:
    """What `orchestra run` keeps running for the agents: their host (`orchestra-agents run --wait`;
    Orchestra names the process `agents.host`, which is no machine: the agents it runs are), in the
    environment this package is installed in, when the project is set up for agents (``set_up``). A
    project without it has the pages and no host: installing the package beside Orchestra starts no AI.
    ``--wait``: a host restarted while the previous one still shuts down waits for it. The stop
    grace outlasts the host's own shutdown, so Orchestra never kills it mid-way and leaves its
    sessions' processes behind."""
    return ([Process("host", [sys.executable, "-m", "orchestra_agents", "run", "--wait"], stop_grace_s=STOP_GRACE_S)]
            if set_up(project) else [])


def collectors(project) -> list[Collector]:
    """The instrument that measures the agents (``agents.turns``): ``orchestra run`` runs
    ``orchestra-agents metrics`` every discovery step, which prints the level and the counters from
    the agents' runtime record (measures.py), and labels each point ``instrument=agents.turns``. A
    command, not a scrape of ``/metrics``: the web server's port is not known to ``orchestra run``."""
    if not set_up(project):
        return []
    step = max(1.0, float(project.orchestra.config.discovery_step))
    return [Collector.collect("turns", f"{shlex.quote(sys.executable)} -m orchestra_agents metrics", every_s=step, timeout_s=min(step, 10.0))]


def declared(project) -> list[Declared]:
    """The agents, for Orchestra's Machines page before they run: the resident and custom agents,
    and the per-ticket roles the agents serve (a role another machine fills is that machine's, not
    declared here), each with what it listens for (listening.py), as it announces itself. The host
    that runs them is no machine and is not declared."""
    work = project.config.work
    return [Declared(a.name, "agent", "continuous" if a.resident else "ad hoc", listens_for(a, work))
            for a in all_agents(project).values()]


def current(project: Project) -> Project:
    """The project with ``[agents]`` as the configuration says now (through Orchestra, the overlay
    merged): the host follows an edit within a second, and so do the pages. A half-written or broken
    file keeps the last good config."""
    try:
        project.reload_config()
    except (ConfigError, OSError, tomllib.TOMLDecodeError):
        pass
    return project


def router(project) -> APIRouter:
    r = APIRouter()
    events = Events.for_project(project)
    store = AgentStore(project, events)

    @r.get("/roles/{name}")
    async def role_detail(name: str) -> dict:
        try:
            return read_org(current(project), store, "get_role", {"name": name})
        except StoreError:
            raise HTTPException(404)

    @r.get("/org")
    async def org() -> dict:
        """The organisation in one read: roles (job descriptions), agents (what runs), teams (who
        owns what), which of the task system's roles the agents here serve, and what each event
        an agent may wake on means."""
        current(project)
        return {"ok": True, "now": time.time(), "serves": project.config.serves, "event_help": EVENT_HELP,
                "roles": read_org(project, store, "list_roles", {})["roles"],
                "agents": read_org(project, store, "list_agents", {})["agents"],
                "teams": read_org(project, store, "list_teams", {})["teams"]}

    @r.get("/agents/{name}")
    async def agent_detail(name: str) -> dict:
        try:
            return read_org(current(project), store, "get_agent", {"name": name})
        except StoreError:
            raise HTTPException(404)

    @r.get("/teams/{name:path}")
    async def team_detail(name: str) -> dict:
        try:
            return read_org(current(project), store, "get_team", {"name": name})
        except StoreError:
            raise HTTPException(404)

    @r.get("/state")
    async def state() -> dict:
        """The agents' sessions (alive or not) and the teams' probes, for the Agents page."""
        current(project)
        rt = store.runtime()
        ctl = rt.get("control") or {}
        host_alive, residents = alive(ctl.get("pid")), residents_of(project)
        sessions = list(rt["sessions"].values())
        for s in sessions:
            s["alive"] = session_alive(s, host_alive, residents)
        probes = [{"name": n, **rt["probes"][n]} for n in rt["probes"] if not rt["probes"][n].get("removed")]
        return {"ok": True, "now": time.time(), "sessions": sessions, "probes": probes,
                "control": {**ctl, "alive": host_alive}}

    @r.get("/attention")
    async def attention() -> dict:
        """A Supervisor message the human has not read (shown on every page, static/attention.js),
        and whether the host that answers the chat is up."""
        rt = store.runtime()
        s = rt["sessions"].get("supervisor") or {}
        seen = float(rt.get("supervisor_seen_at") or 0)
        settled = float(s.get("settled_at") or 0)
        message = {"text": s.get("last_text") or "", "at": settled} if settled > seen and s.get("last_text") else None
        return {"ok": True, "message": message, "working": s.get("status") == "running", "count": 1 if message else 0,
                "host": {"alive": alive((rt.get("control") or {}).get("pid"))}}

    @r.post("/attention/seen")
    async def attention_seen() -> dict:
        return {"ok": True, "seen_at": store.mark_supervisor_seen()}

    @r.post("/supervisor")
    async def supervisor_message(request: Request):
        """The one write path in the UI: a human message to the resident Supervisor.
        Proxied to the control endpoint that `orchestra-agents run` serves; streams its NDJSON."""
        body = await request.json()
        current(project)
        ctl = store.runtime().get("control") or {}
        if not ctl.get("port"):
            raise HTTPException(503, "the agents' host (orchestra-agents run) is not running; the Supervisor is only reachable while it is")
        req = urllib.request.Request(f"http://127.0.0.1:{ctl['port']}/supervisor", method="POST",
                                     data=json.dumps({"message": body.get("message", "")}).encode(),
                                     headers={"Content-Type": "application/json", **control_bearer(project)})
        try:
            # The control endpoint ends the stream when the turn ends; reads must not time out before that.
            resp = await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=max(project.config.turn_timeout or 0, 86400)))
        except Exception as e:
            raise HTTPException(503, f"cannot reach the agents' host: {e}") from e

        async def gen():
            # Proxies in front of this page (Cloudflare closes a response that is silent for
            # 100s) must see bytes while the Supervisor thinks, so a ping goes out whenever
            # nothing has arrived for CHAT_PING_SECONDS. The client ignores pings.
            q: asyncio.Queue[bytes | None] = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def pump():
                try:
                    while True:
                        line = resp.readline()
                        loop.call_soon_threadsafe(q.put_nowait, line or None)
                        if not line:
                            return
                except Exception:  # noqa: BLE001 - a broken upstream ends the stream
                    loop.call_soon_threadsafe(q.put_nowait, None)
            threading.Thread(target=pump, daemon=True).start()
            while True:
                try:
                    line = await asyncio.wait_for(q.get(), timeout=CHAT_PING_SECONDS)
                except TimeoutError:
                    yield b'{"type": "ping"}\n'
                    continue
                if line is None:
                    break
                yield line

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @r.get("/sessions/{session_id}/transcript")
    async def transcript(session_id: str, before: int = 0, limit: int = 30, after: int | None = None) -> dict:
        """A page of a session's transcript, newest first. `before` counts messages back from
        the end that are already shown; the page returns the next `limit` older ones. `after`
        instead returns every message past the first `after` ones, which is how an open
        drawer polls for what arrived since it was rendered (transcripts only grow)."""
        s = store.find_session(session_id)
        if not s:
            raise HTTPException(404)
        f = s.get("session_file")
        exists = bool(f) and Path(f).exists()
        msgs = read_transcript(s) if exists else []
        total = len(msgs)
        if after is not None:
            page, end = msgs[max(0, after):], total
        else:
            end = max(0, total - max(0, before))
            page = msgs[max(0, end - max(1, min(limit, 200))):end]
        page.reverse()
        ctl = store.runtime().get("control") or {}
        host_alive = alive(ctl.get("pid"))
        s = {**s, "alive": session_alive(s, host_alive, residents_of(project)), "file_exists": exists}
        pf = project.logs_dir / "agents" / f"{session_id}.prompt.md"
        started_with = pf.read_text(encoding="utf-8") if pf.exists() else None
        # What the host did to this session (turn starts, cut-offs, errors, resets) is kept on
        # its record; the session file stays the only transcript, and the view overlays these.
        return {"session": s, "messages": page, "total": total, "before": before, "next_before": total - (end - len(page)) if page else before,
                "has_more": end - len(page) > 0, "turns": sum(1 for m in msgs if m["role"] == "user"), "started_with": started_with,
                "markers": s.get("markers") or [], "host": {**ctl, "alive": host_alive}}

    @r.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        """The agents' measurements in the Prometheus text format (measures.py)."""
        return metrics_text(current(project), store)

    @r.get("/agents")
    async def agents_view() -> dict:
        """Every agent, and every session as the machine instance it runs (or last ran) as, with
        the ticket it is on: for the machine cards and the Agents page (org_views.agents_status)."""
        return agents_status(current(project), store)

    return r
