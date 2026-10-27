"""What an agent changed, as Orchestra recorded it: the facts a reply is checked against.

Every tool call that changes something (a commit of ``orchestra.toml``, a restart, a ticket or
milestone request the task system carried out, a change to the organisation, a note to another
agent) is answered with one plain line saying what it changed (``done`` in the tool's answer), and
the line is kept in the agent's state with the time (``AgentStore.add_action``). A call that
failed, or changed nothing (a setting already as asked), records nothing. The record is made
where every tool call of every harness passes (``tooling.handle``), so it does not depend on the
model, its memory or its context's compaction.

The host reads the Supervisor's record back (``since``): it is put in front of the next turn, and
a reply that says nothing was done while the record shows changes is corrected (``truth.py``)."""

from __future__ import annotations

import time
from typing import Any

from .api_org import MUTATIONS as ORG_MUTATIONS
from .protocol import REQUESTS
from .setup_tools import SETUP_COMMANDS

KEPT = 40                 # the newest actions kept per agent
AGENT_COMMANDS = frozenset({"pause_agent", "resume_agent", "restart_agent", "reset_agent", "run_agent", "message_agent",
                            "emit_event"})
CHANGES = (SETUP_COMMANDS | ORG_MUTATIONS | AGENT_COMMANDS | frozenset(REQUESTS)
           | frozenset({"restart_machine", "update_project_context", "escalate_to_supervisor"}))

PAST = {"create": "created", "update": "updated", "delete": "deleted", "close": "closed", "pause": "paused",
        "resume": "resumed", "restart": "asked for a restart of", "reset": "asked for a reset of", "cancel": "cancelled",
        "unblock": "unblocked"}


def _ticket(result: dict, args: dict) -> str:
    t = result.get("ticket") or {}
    return str(t.get("id") or args.get("id") or "?")


def did(command: str, args: dict[str, Any], result: dict) -> str | None:
    """One line saying what a successful call changed, or None when it changed nothing."""
    a = args or {}
    verb, _, noun = command.partition("_")
    if command in SETUP_COMMANDS:
        if not result.get("commit"):
            return None
        return f"committed {result['commit']} to orchestra.toml on the base branch: {result.get('changed')}"
    if command == "restart_machine":
        return f"restarted the program {result.get('restarted') or a.get('name')} (pid {result.get('pid_before')} -> {result.get('pid')})"
    if command == "update_project_context":
        return "rewrote the project context document"
    if command == "create_ticket":
        return f"created {_ticket(result, a)} ({(result.get('ticket') or {}).get('title') or a.get('title') or ''})"
    if command == "update_ticket":
        return f"updated {_ticket(result, a)} ({', '.join(sorted(k for k in a if k != 'id')) or 'nothing named'})"
    if command == "add_note":
        return f"added a note to {_ticket(result, a)}"
    if noun == "ticket":                                          # cancel, unblock
        return f"{PAST[verb]} {_ticket(result, a)}"
    if noun == "milestone":
        m = result.get("milestone") or {}
        return f"{PAST[verb]} milestone {m.get('id') or a.get('id') or ''} {m.get('name') or a.get('name') or ''}".rstrip()
    if command == "set_capacity":
        return "set the capacity dial (" + ", ".join(f"{k}={a[k]}" for k in sorted(a) if k != "reason") + ")"
    if command == "resume_pipeline":
        return "resumed the pipeline"
    if command in ("message_agent", "run_agent"):
        return f"{'briefed' if command == 'run_agent' else 'left a message for'} {a.get('name')}"
    if command == "escalate_to_supervisor":
        return "escalated to the Supervisor"
    if command == "emit_event":
        return f"raised the event {a.get('type')}"
    if verb in PAST:                                              # the organisation: roles, agents, teams
        return f"{PAST[verb]} the {noun} {a.get('name') or ''}".rstrip()
    return None


def record(store, role: str, command: str, args: dict[str, Any], result: dict) -> dict:
    """The answer of a call that changed something, with ``done`` saying so plainly, and the
    line kept in the agent's record. Any other answer is returned as it is."""
    if command not in CHANGES or not role or not isinstance(result, dict) or result.get("ok") is False:
        return result
    line = did(command, args, result)
    if not line:
        return result
    store.add_action(role, {"at": time.time(), "tool": command, "did": line}, keep=KEPT)
    return {**result, "done": f"Changed: {line}."}


def since(store, role: str, t: float) -> list[str]:
    """What the agent changed at or after ``t``, oldest first."""
    return [a["did"] for a in store.agent_state(role).get("actions") or [] if a.get("at", 0) >= t]
