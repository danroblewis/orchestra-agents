"""The agents in Orchestra's web app (an Orchestra plugin, ``orchestra/plugins.py``): the Agents
page (who is in the project, their roles, teams, wake-ups and the event map), the agents' session
transcripts, the Supervisor chat and the way to it on every page, and the agents' machine cards.
Its API is at ``/api/plugins/agents``."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import threading
import time
import urllib.request
from pathlib import Path

import psutil
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from orchestra.plugins import Declared, Page, Plugin

from . import prompting, tooling
from .api_org import handle_org
from .builtins import SYSTEM_BINDINGS
from .config import load_project
from .events import Events
from .harness import read_transcript
from .org_store import all_agents, all_teams
from .protocol import Follower
from .roles import EVENT_TYPES
from .store import AgentStore, StoreError

STATIC = Path(__file__).parent / "static"
WEB = "agents_web"       # the source of the operator's controls on the bus
CHAT_PING_SECONDS = 20   # keep the chat stream alive through proxies while the Supervisor thinks
OBSERVED_WINDOW_S = 24 * 3600
OBSERVED_REFRESH_S = 60
_observed: dict[str, dict] = {}   # project key -> what the event map saw on the bus (read once a minute, not per request)


def _alive(pid) -> bool:
    try:
        return bool(pid) and psutil.Process(int(pid)).status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False


def plugin(orchestra_project) -> Plugin:
    """The plugin for the repository Orchestra serves (its entry point, in pyproject.toml)."""
    project = load_project(orchestra_project.repo_root)
    return Plugin("agents", [Page("/agents", "Agents", STATIC / "agents.html")], STATIC, router(project),
                  scripts=["attention.js", "machine-cards.js"], machines=declared(project))


def declared(project) -> list[Declared]:
    """The agents, for Orchestra's Machines page before they run: the host, the resident and
    custom agents, and the per-ticket roles the agents serve (a role another machine fills is
    that machine's, not declared here)."""
    out = [Declared("agents", "program", "Runs the AI agents: their sessions, wake-ups, and the task system's assignments.", "continuous")]
    for a in all_agents(project).values():
        if a.builtin and not a.resident and a.name not in project.config.serves:
            continue
        mode = {"continuous": "continuous", "scheduled": "scheduled", "ad_hoc": "ad hoc"}.get(a.kind, "ad hoc")
        out.append(Declared(a.name, "agent", a.description, mode, sorted({w.split(":")[0] for w in a.wake_on})))
    return out


def router(project) -> APIRouter:
    r = APIRouter()
    events = Events.for_project(project, source=WEB)
    store = AgentStore(project, events)

    @r.get("/roles")
    async def roles() -> dict:
        return tooling.handle(project, store, "list_roles", {})

    @r.get("/roles/{name}")
    async def role_detail(name: str) -> dict:
        try:
            return tooling.handle(project, store, "get_role", {"name": name})
        except StoreError:
            raise HTTPException(404)

    @r.get("/org")
    async def org() -> dict:
        """The organisation in one read: roles (job descriptions), agents (what runs), teams (who
        owns what), and which of the task system's roles the agents here serve."""
        return {"ok": True, "now": time.time(), "serves": project.config.serves,
                "roles": tooling.handle(project, store, "list_roles", {})["roles"],
                "agents": tooling.handle(project, store, "list_agents", {})["agents"],
                "teams": tooling.handle(project, store, "list_teams", {})["teams"]}

    @r.get("/agents/{name}")
    async def agent_detail(name: str) -> dict:
        try:
            return tooling.handle(project, store, "get_agent", {"name": name})
        except StoreError:
            raise HTTPException(404)

    @r.get("/teams/{name:path}")
    async def team_detail(name: str) -> dict:
        try:
            return tooling.handle(project, store, "get_team", {"name": name})
        except StoreError:
            raise HTTPException(404)

    @r.get("/state")
    async def state() -> dict:
        """The agents' sessions (alive or not) and the teams' probes, for the Agents page."""
        sessions = store.sessions()
        for s in sessions:
            s["alive"] = _alive(s.get("pid"))
        probes = [{"name": n, **store.probe_state(n)} for n in store.probes() if not store.probe_state(n).get("removed")]
        ctl = store.runtime().get("control") or {}
        return {"ok": True, "now": time.time(), "project": {"key": project.key}, "sessions": sessions, "probes": probes,
                "control": {**ctl, "alive": _alive(ctl.get("pid"))}}

    @r.post("/control")
    async def control(request: Request) -> dict:
        """The operator's controls on agents: pause, resume, restart, start over, delete. The
        commands are the Supervisor's (the permission check), recorded as the operator's."""
        body = await request.json()
        action = body.get("action")
        if action in ("pause_agent", "resume_agent", "restart_agent", "reset_agent", "delete_agent", "delete_role", "delete_team"):
            try:
                return handle_org(project, store, action, {"name": body.get("name") or ""}, author="operator", role="supervisor")
            except StoreError as e:
                raise HTTPException(409, str(e)) from e
        raise HTTPException(400, "unknown action")

    @r.get("/attention")
    async def attention() -> dict:
        """A Supervisor message the human has not read (shown on every page, static/attention.js)."""
        s = store.find_session("supervisor") or {}
        seen = float(store.runtime().get("supervisor_seen_at") or 0)
        settled = float(s.get("settled_at") or 0)
        message = {"text": s.get("last_text") or "", "at": settled} if settled > seen and s.get("last_text") else None
        return {"ok": True, "message": message, "working": s.get("status") == "running", "count": 1 if message else 0}

    @r.post("/attention/seen")
    async def attention_seen() -> dict:
        return {"ok": True, "seen_at": store.mark_supervisor_seen()}

    @r.post("/supervisor")
    async def supervisor_message(request: Request):
        """The one write path in the UI: a human message to the resident Supervisor.
        Proxied to the control endpoint that `orchestra-agents run` serves; streams its NDJSON."""
        body = await request.json()
        ctl = store.runtime().get("control") or {}
        if not ctl.get("port"):
            raise HTTPException(503, "the agents' host (orchestra-agents run) is not running; the Supervisor is only reachable while it is")
        req = urllib.request.Request(f"http://127.0.0.1:{ctl['port']}/supervisor", method="POST",
                                     data=json.dumps({"message": body.get("message", "")}).encode(),
                                     headers={"Content-Type": "application/json"})
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

    @r.get("/context")
    async def context() -> dict:
        return {"text": prompting.read_context(project)}

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
        s = {**s, "alive": _alive(s.get("pid")), "file_exists": exists}
        pf = project.logs_dir / "agents" / f"{session_id}.prompt.md"
        started_with = pf.read_text(encoding="utf-8") if pf.exists() else None
        # What the host did to this session (turn starts, cut-offs, errors, resets) is kept on
        # its record; the session file stays the only transcript, and the view overlays these.
        ctl = store.runtime().get("control") or {}
        return {"session": s, "messages": page, "total": total, "before": before, "next_before": total - (end - len(page)) if page else before,
                "has_more": end - len(page) > 0, "turns": sum(1 for m in msgs if m["role"] == "user"), "started_with": started_with,
                "markers": s.get("markers") or [], "host": {**ctl, "alive": _alive(ctl.get("pid"))}}

    @r.get("/eventmap")
    async def eventmap() -> dict:
        """Who wakes whom through which events (the Agents page's map): the intended bindings
        (each agent's wake_on and emits, team emits, probes, the task system's and the host's)
        and what was observed on the bus over the last 24 hours, by the machine that sent it."""
        agents_, teams = all_agents(project), all_teams(project)
        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        def node(nid, kind, label=None, **extra):
            nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label or nid, **extra})

        def split(w):
            t, _, f = w.partition(":")
            return t, f or None
        for sid, sb in SYSTEM_BINDINGS.items():
            node("sys:" + sid, "system", sid.replace("_", " "), description=sb["description"])
            for e in sb["emits"]:
                t, f = split(e)
                node("ev:" + t, "event", t)
                edges.append({"from": "sys:" + sid, "to": "ev:" + t, "kind": "emits", "filter": f})
            for w in sb["wake_on"]:
                t, f = split(w)
                node("ev:" + t, "event", t)
                edges.append({"from": "ev:" + t, "to": "sys:" + sid, "kind": "wakes", "filter": f})
        for t in teams.values():
            for e in t.emits:
                node("ev:" + e, "event", e, custom=True, team=t.name)
            for pr in t.probes or []:
                node("probe:" + pr["name"], "probe", pr["name"], team=t.name)
                for e in ("probe.failed", "probe.recovered"):
                    node("ev:" + e, "event", e)
                    edges.append({"from": "probe:" + pr["name"], "to": "ev:" + e, "kind": "emits", "filter": pr["name"]})
        for ag in agents_.values():
            node("agent:" + ag.name, "agent", ag.name, team=ag.team, builtin=ag.builtin, kind_of=ag.kind)
            for w in ag.wake_on:
                t, f = split(w)
                node("ev:" + t, "event", t)
                edges.append({"from": "ev:" + t, "to": "agent:" + ag.name, "kind": "wakes", "filter": f})
            for e in ag.emits:
                t, f = split(e)
                node("ev:" + t, "event", t)
                edges.append({"from": "agent:" + ag.name, "to": "ev:" + t, "kind": "emits", "filter": f})
            if ag.team and ag.team in teams:
                for e in teams[ag.team].emits:
                    edges.append({"from": "agent:" + ag.name, "to": "ev:" + e, "kind": "may_emit", "filter": None})
        counts, by = observed(project.key, events)
        for who, ts_ in by.items():
            if who in agents_:
                for t, n in ts_.items():
                    node("ev:" + t, "event", t)
                    hits = [x for x in edges if x["from"] == "agent:" + who and x["to"] == "ev:" + t]
                    if hits:
                        for x in hits:
                            x["seen"] = n                      # a declared edge that actually fired
                    else:
                        edges.append({"from": "agent:" + who, "to": "ev:" + t, "kind": "observed", "filter": None, "seen": n})
        return {"ok": True, "nodes": list(nodes.values()), "edges": edges, "counts": counts, "by": by,
                "known_types": list(EVENT_TYPES)}

    @r.get("/agents")
    async def agents_view() -> dict:
        """Every agent, and every session as the machine instance it runs (or last ran) as, with
        the ticket it is on (as its assignment named it): for the machine cards and the Agents page."""
        rt = store.runtime()
        sessions = list(rt["sessions"].values())
        out = []
        for a in all_agents(project).values():
            mine = [x for x in sessions if x["id"] == a.session_id or x.get("kind") == a.name or x["id"].startswith(a.name + "-")]
            jobs = [{"session": x["id"], "ticket": x.get("ticket_id"), "text": (x.get("partial_text") or x.get("last_text") or "")[-160:]}
                    for x in mine if x.get("status") == "running"]
            st = rt["agent_state"].get(a.name) or {}
            out.append({"id": a.name, "name": a.name, "kind": "agent", "description": a.description, "team": a.team, "session_id": a.session_id,
                        "resident": a.resident, "paused": bool(st.get("paused")), "next_scheduled_at": st.get("next_scheduled_at"),
                        "status": "working" if jobs else ("waiting" if any(_alive(x.get("pid")) for x in mine) else "not running"), "jobs": jobs})
        ctl = rt.get("control") or {}
        host_alive = _alive(ctl.get("pid"))
        residents = {a["session_id"] for a in out if a.get("resident")}
        insts = []
        for x in sessions:
            insts.append({**{k: v for k, v in x.items() if k not in ("partial_text", "last_text")},
                          "session": x["id"], "instance": x.get("instance") or x.get("kind"), "role": x.get("kind"),
                          # a resident agent between turns has no process on Claude Code or Codex: it is up while its host is
                          "alive": _alive(x.get("pid")) or (x["id"] in residents and x.get("status") != "running" and host_alive),
                          "tool": x.get("tool_name"), "last_text": (x.get("partial_text") or x.get("last_text") or "")[-300:],
                          "ticket": ({"id": x["ticket_id"], "title": x.get("ticket_title") or "", "state": x.get("ticket_state") or "IN_PROGRESS",
                                      "worker_turns": x.get("worker_turns") or 0} if x.get("ticket_id") else None)})
        cfg = project.config
        return {"ok": True, "host": {**ctl, "alive": host_alive}, "agents": out, "sessions": insts, "now": time.time(),
                "harness": cfg.harness, "model": cfg.model, "serves": cfg.serves}

    return r


def observed(key: str, events: Events) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """What was on the bus in the last 24 hours, for the event map: counts by event type (and by
    its variant, the transition target or the probe) and by the machine that sent each. The bus
    is read from where the last read stopped, at most once a minute, whoever asks."""
    now_t = time.time()
    c = _observed.get(key)
    if c is None:
        c = _observed[key] = {"follow": Follower(events, start=max(0, events.end() - 200000)), "recent": [], "at": 0.0}
    if now_t - c["at"] >= OBSERVED_REFRESH_S:
        c["at"] = now_t
        c["recent"] += [(e.get("ts") or "", e["type"], e.get("to") or e.get("probe"), (e.get("source") or "").partition("/")[0])
                        for e in c["follow"].new()]
        since = (dt.datetime.now(dt.UTC) - dt.timedelta(seconds=OBSERVED_WINDOW_S)).isoformat(timespec="seconds")
        c["recent"] = [x for x in c["recent"] if x[0] >= since]
    counts: dict[str, int] = {}
    by: dict[str, dict[str, int]] = {}
    for _ts, type_, variant, who in c["recent"]:
        counts[type_] = counts.get(type_, 0) + 1
        if variant:
            counts[f"{type_}:{variant}"] = counts.get(f"{type_}:{variant}", 0) + 1
        if who:                                            # the machine that sent it (worker/1 is worker)
            by.setdefault(who, {})
            by[who][type_] = by[who].get(type_, 0) + 1
    return counts, by
