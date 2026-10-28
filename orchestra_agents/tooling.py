"""What an agent's tool call does (the tools are defined once, in harness/tools.py; the MCP server
and the Pi extension both call this).

Some tools are the agents' own: a worker's or reviewer's report (handed to the session that runs
it, which reports to the task system on the bus), the project context, escalating to the
Supervisor, reading the bus, the organisation (roles, agents, teams, emit_event), reading the
whole system (``observe.py``, ``observe_model.py``), the model and experiments on it, and the
predictions of an agent's own changes (``modeling.py``), and the Supervisor's changes to the
project's ``orchestra.toml`` (``setup_tools.py``, ``restart.py``). Everything
about tickets and milestones is the task system's, asked for on the bus (protocol.TaskClient): a
request it answers, so only it changes them. The role is the session's, as the host issued it
(access.identify, at the MCP server and `orchestra agents api`), never the agent's, and every
call is checked against that role's tool groups before it runs (access.authorize)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from . import (
    access,
    guides,
    handoff,
    instruments,
    ledger,
    modeling,
    observe,
    observe_model,
    prompting,
    source_tools,
)
from .api_org import ORG_COMMANDS, handle_org
from .config import Project
from .org import effective_tools
from .org_store import all_agents, all_teams
from .protocol import PLANNER_ROLES, QUERIES, REQUESTS, TaskClient
from .restart import restart_machine
from .roles import all_roles
from .setup_tools import SETUP_COMMANDS, handle_setup
from .store import AgentStore, StoreError


def check_args(command: str, args: dict[str, Any]) -> None:
    """A model's tool call against the tool's own schema (harness/tools.py), checked where a model's
    call comes in (the MCP server, `orchestra agents api`), not in handle(): a model that
    names an argument the tool does not have, or leaves out a required one, is told so, rather
    than the call going through with what it meant to say dropped."""
    from .harness.tools import BY_NAME
    tool = BY_NAME.get(command)
    if tool is None:
        return
    unknown = sorted(set(args) - set(tool.properties))
    missing = [k for k in tool.required if args.get(k) in (None, "")]
    if unknown or missing:
        expected = ", ".join(f"{k}{' (required)' if k in tool.required else ''}" for k in tool.properties)
        raise StoreError(f"{command}: " + "; ".join(
            ([f"no argument {', '.join(unknown)}"] if unknown else []) + ([f"missing {', '.join(missing)}"] if missing else []))
            + f". It takes: {expected or 'no arguments'}.")


def handle(project: Project, store: AgentStore, command: str, args: dict[str, Any], role: str = "", ticket_env: str = "") -> dict:
    """One tool call, first checked against the caller's role and its tool groups (access.py:
    an unknown or missing role, or a tool outside the role's groups, is refused). A call that
    changed something is answered with what it changed, and the change is kept in the agent's
    record (ledger.py), whatever the model says about it later."""
    access.authorize(project, command, role)
    with store.events.acting_as(role):          # what the call publishes is the caller's, never the host's
        return ledger.record(store, role, command, args or {}, _handle(project, store, command, args, role, ticket_env))


def _handle(project: Project, store: AgentStore, command: str, args: dict[str, Any], role: str = "", ticket_env: str = "") -> dict:
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
        store.set_report(ticket_env, {"state": state, "summary": a.get("summary", ""),
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
        store.set_report(ticket_env, {"verdict": verdict, "summary": a.get("summary", ""),
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

    if command in SETUP_COMMANDS or command in SYSTEM_COMMANDS:
        project.reload_config()               # the configuration as it is now: an earlier call may have changed it
    if command in SETUP_COMMANDS:
        return handle_setup(project, store, command, a, author, role)
    if command == "restart_machine":
        return restart_machine(project, store, a, author, role)
    if command == "check_setup":
        return observe.check_setup(project, store)
    if command == "read_config":
        return observe.read_config(project)
    if command == "list_processes":
        return observe.list_processes(project)
    if command == "read_machine_log":
        return observe.read_machine_log(project, store, a)
    if command == "list_metrics":
        return observe_model.list_metrics(project, a)
    if command == "get_model":
        return observe_model.get_model(project)
    if command == "read_instrument":
        return instruments.read_instrument(project, a)
    if command == "try_collector":
        if role != "supervisor":
            raise StoreError("only the supervisor tries collectors (it sets them up)")
        return instruments.try_collector(project, a)
    if command in source_tools.COMMANDS:
        return source_tools.COMMANDS[command](project, a)
    if command == "list_guides":
        return guides.list_guides(project)
    if command == "read_guide":
        return guides.read_guide(project, a)
    if command == "hand_to_coordinator":
        return handoff.hand_to_coordinator(store, a, role)
    if command == "report_plan":
        return handoff.report_plan(store, a, role)

    if command in modeling.COMMANDS:
        return modeling.handle_model(project, store, command, a, role, ticket_env)

    if command in REQUESTS or command in QUERIES:                 # the task system's
        if command in REQUESTS and not may_change_tickets(project, role, command):
            raise StoreError(f"role {role} may not change tickets or milestones ({command})")
        got = TaskClient(store.events, role)(command, a)
        if got.get("ok") is False:                                # refused, or no answer: the tool failed
            raise StoreError(got.get("error") or "the task system refused")
        return got

    raise StoreError(f"unknown command {command!r}")


PLANNERS = set(PLANNER_ROLES)
SYSTEM_COMMANDS = frozenset({"restart_machine", "check_setup", "read_config", "list_processes", "read_machine_log",
                             "list_metrics", "get_model", "read_instrument", "try_collector", *source_tools.COMMANDS})
PLANNING = frozenset({"create_ticket", "create_milestone", "update_milestone", "close_milestone"})   # the Coordinator's          # they read the setup (observe.py, restart.py)


def may_change_tickets(project: Project, role: str, command: str = "") -> bool:
    """Whether an agent may ask the task system for ``command``: creating tickets and milestones
    (``planning``) is the Coordinator's, and a custom agent's whose roles or team grant it
    ``planning``; the other changes (an update, a note, a cancel, an unblock) need ``tickets``, which
    the Supervisor holds too (the same grants the harness started it with). Without a role, nothing."""
    if not role:
        return False
    group = "planning" if command in PLANNING else "tickets"
    if role in PLANNERS:
        return role == "coordinator" or group == "tickets"
    ag = all_agents(project).get(role)
    return bool(ag) and not ag.builtin and group in effective_tools(ag, all_roles(project), all_teams(project))


_UNANSWERED: dict[str, float] = {}     # project key -> when the task system last left a snapshot unanswered
SILENT_FOR_S, SILENT_WAIT_S = 600.0, 3.0   # after a snapshot nobody answered, the next ones wait this long, for this long


def snapshot(store: AgentStore, role: str, wait_s: float | None = None) -> dict:
    """The task system's snapshot (a query; the fields the prompts read are listed in protocol.py)
    for a wake-up's or the Supervisor's prompt, asked as the agent being woken (the query's
    source is its instance, which for a resident is its name), waiting ``wait_s`` for the answer
    (protocol.WAIT_S by default; SILENT_WAIT_S for ten minutes after one went unanswered, so a project
    without the task system, as before its setup, does not hold every turn for half a minute). When the
    task system does not answer, an empty one that says so."""
    if wait_s is None and time.time() - _UNANSWERED.get(store.project.key, 0.0) < SILENT_FOR_S:
        wait_s = SILENT_WAIT_S                    # it did not answer a moment ago: no task system runs, most likely
    got = TaskClient(store.events, role, source=role, wait_s=wait_s)("snapshot")
    snap = got.get("snapshot") if got.get("ok") else None
    if snap:
        _UNANSWERED.pop(store.project.key, None)
    else:
        _UNANSWERED[store.project.key] = time.time()
    if not snap:
        snap = {"counts": {}, "tickets": [], "milestones": [], "pipeline": {"paused": False}, "capacity": {},
                "assignments": [], "unavailable": got.get("error") or "the task system did not answer"}
    return snap
