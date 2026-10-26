"""What an agent's tool call does (the tools are defined once, in harness/tools.py; the MCP server
and the Pi extension both call this).

Some tools are the agents' own: a worker's or reviewer's report (handed to the session that runs
it, which reports to the task system on the bus), the project context, escalating to the
Supervisor, reading the bus, and the organisation (roles, agents, teams, emit_event). Everything
about tickets and milestones is the task system's, asked for on the bus (protocol.TaskClient): a
request it answers, so only it changes them. The role comes from the environment the agent was
started in, never from the agent."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from . import prompting
from .api_org import ORG_COMMANDS, handle_org
from .config import Project
from .org import effective_tools
from .org_store import all_agents, all_teams
from .protocol import QUERIES, REQUESTS, TaskClient
from .roles import all_roles
from .store import AgentStore, StoreError


def handle(project: Project, store: AgentStore, command: str, args: dict[str, Any], role: str = "", ticket_env: str = "") -> dict:
    a = args or {}
    author = role or "agents"

    if command == "report_ticket":
        if role != "worker":
            raise StoreError("only workers may call report_ticket")
        state = a.get("state")
        if state not in ("READY_FOR_REVIEW", "BLOCKED"):
            raise StoreError("state must be READY_FOR_REVIEW or BLOCKED")
        if state == "BLOCKED" and not (a.get("blocked_reason") or "").strip():
            raise StoreError("BLOCKED requires blocked_reason")
        store.set_report(ticket_env, {"kind": "worker", "state": state, "summary": a.get("summary", ""),
                                      "blocked_reason": a.get("blocked_reason")})
        return {"ok": True, "message": "Report recorded. You may stop now."}

    if command == "report_review":
        if role != "reviewer":
            raise StoreError("only reviewers may call report_review")
        verdict = a.get("verdict")
        if verdict not in ("APPROVED", "REJECTED"):
            raise StoreError("verdict must be APPROVED or REJECTED")
        if verdict == "REJECTED" and not (a.get("feedback") or "").strip():
            raise StoreError("REJECTED requires feedback")
        store.set_report(ticket_env, {"kind": "reviewer", "verdict": verdict, "summary": a.get("summary", ""),
                                      "feedback": a.get("feedback")})
        return {"ok": True, "message": "Verdict recorded. You may stop now."}

    if command == "get_project_context":
        return {"ok": True, "context": prompting.read_context(project)}

    if command == "update_project_context":
        if role != "supervisor":
            raise StoreError("only the supervisor maintains the project context")
        text = a.get("text") or ""
        if len(text.strip()) < 20:
            raise StoreError("context text is too short; write the full document")
        prompting.write_context(project, text)
        store.events.emit("context.updated", by=author)
        return {"ok": True}

    if command == "escalate_to_supervisor":
        store.add_wakeup("supervisor", f"[{author}] {a.get('note', '')}")
        return {"ok": True, "message": "The supervisor will be woken with your note."}

    if command == "read_events":
        minutes, types = int(a.get("minutes") or 60), set(a.get("types") or [])
        since = time.time() - minutes * 60
        end = store.events.end()
        evs, _ = store.events.read(max(0, end - 5000), limit=5000)
        out = []
        for e in evs:
            try:
                dt = datetime.fromisoformat(e["ts"])
                ts = (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()
            except (KeyError, TypeError, ValueError):     # an event without a time is not in a time window
                continue
            if ts < since or (types and e.get("type") not in types) or e.get("type") in ("session.started", "session.settled"):
                continue
            out.append(e)
        return {"ok": True, "events": out[-200:], "count": len(out)}

    if command in ORG_COMMANDS:
        return handle_org(project, store, command, a, author, role)

    if command in REQUESTS or command in QUERIES:                 # the task system's
        if command in REQUESTS and not may_change_tickets(project, role):
            raise StoreError(f"role {role} may not change tickets or milestones ({command})")
        got = TaskClient(store.events, role)(command, a)
        if got.get("ok") is False:                                # refused, or no answer: the tool failed
            raise StoreError(got.get("error") or "the task system refused")
        return got

    raise StoreError(f"unknown command {command!r}")


PLANNERS = {"supervisor", "coordinator"}


def may_change_tickets(project: Project, role: str) -> bool:
    """Whether an agent may ask the task system to change tickets: the Supervisor and the
    Coordinator, and a custom agent whose roles or team grant it the ``tickets`` tools (the same
    grant the harness started it with). Without a role, nothing."""
    if not role:
        return False
    if role in PLANNERS:
        return True
    ag = all_agents(project).get(role)
    return bool(ag) and not ag.builtin and "tickets" in effective_tools(ag, all_roles(project), all_teams(project))


def snapshot(project: Project, store: AgentStore, role: str = "agents") -> dict:
    """The task system's snapshot (a query; the fields the prompts read are listed in protocol.py)
    for a wake-up's or the Supervisor's prompt. When the task system does not answer, an empty one
    that says so."""
    got = TaskClient(store.events, role)("snapshot")
    snap = got.get("snapshot") if got.get("ok") else None
    if not snap:
        snap = {"counts": {}, "tickets": [], "milestones": [], "pipeline": {"paused": False}, "capacity": {},
                "assignments": [], "unavailable": got.get("error") or "the task system did not answer"}
    return snap
