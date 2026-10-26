"""The `orchestra-agents` command line: the agents' host, the Supervisor chat, the agents' tool
interface (what the Pi extension calls), their sessions and roles."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import tooling
from .config import ConfigError, Project, load_project
from .events import Events
from .harness.base import assistant_text
from .store import AgentStore, StoreError


def _project(args) -> Project:
    try:
        return load_project(getattr(args, "repo", None))
    except ConfigError as e:
        sys.exit(f"orchestra-agents: {e}")


def _store(project: Project) -> tuple[AgentStore, Events]:
    events = Events.for_project(project)
    return AgentStore(project, events), events


def cmd_run(args) -> int:
    from .host import AgentHost
    project = _project(args)
    store, events = _store(project)
    AgentHost(project, store, events).run(once=args.once)
    return 0


def cmd_sessions(args) -> int:
    from .harness import read_transcript

    project = _project(args)
    store, _ = _store(project)
    if args.action == "list":
        for s in store.sessions():
            print(f"{s['id']:28} {s.get('status', '?'):8} {s.get('ticket_id') or '':10} {(s.get('last_text') or '')[:60]!r}")
        return 0
    s = next((s for s in store.sessions() if s["id"] == args.id), None)
    if not s or not s.get("session_file"):
        sys.exit(f"no transcript for {args.id}")
    for m in read_transcript(s):
        tools = " ".join(f"[{t['name']}]" for t in m["tool_calls"])
        print(f"--- {m['role']} {tools}\n{m['text'][:3000]}\n")
    return 0


def cmd_tools(args) -> int:
    from .harness.tools import TOOLS, tools_for

    groups = args.groups or os.environ.get("ORCHESTRA_TOOLS")
    tools = tools_for([g for g in groups.split(",") if g]) if groups else list(TOOLS)
    if args.json:
        print(json.dumps([t.to_dict() for t in tools]))
        return 0
    for t in tools:
        print(f"{t.name:26} {t.group:14} {t.description[:80]}")
    return 0


def cmd_api(args) -> int:
    project = _project(args)
    store, _ = _store(project)
    try:
        payload = json.loads(args.args) if args.args else {}
        out = tooling.handle(project, store, args.command, payload,
                         role=os.environ.get("ORCHESTRA_ROLE", ""), ticket_env=os.environ.get("ORCHESTRA_TICKET", ""))
    except (StoreError, KeyError, ValueError) as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        return 1
    print(json.dumps(out, indent=None if args.compact else 1))
    return 0


def _print_event(ev: dict) -> None:
    t = ev.get("type")
    if t == "tool_start":
        print(f"  ⚙ {ev.get('name', 'tool')} {json.dumps(ev.get('args') or {})[:160]}", flush=True)
    elif t == "assistant_end":
        txt = assistant_text(ev.get("text") or "")      # the harnesses normalise it to a string; a list of parts is read too
        if txt.strip():
            print(f"\n{txt.strip()}\n", flush=True)


def _chat_attached(project: Project, store: AgentStore, port: int) -> int:
    import urllib.request

    print(f"Supervisor for {project.key} via orchestra-agents run on 127.0.0.1:{port}. Ctrl-D or 'exit' to leave.\n")
    while True:
        try:
            line = input("you> ").strip()
        except EOFError:
            break
        if line in ("exit", "quit"):
            break
        if not line:
            continue
        req = urllib.request.Request(f"http://127.0.0.1:{port}/supervisor", method="POST",
                                     data=json.dumps({"message": line}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=(project.config.turn_timeout or 86400) + 30) as resp:
                for raw in resp:
                    ev = json.loads(raw)
                    if ev.get("type") == "done":
                        if ev.get("error") or ev.get("timed_out"):
                            print(f"[agents] turn ended: {ev.get('error') or 'timed out'}")
                    else:
                        _print_event(ev)
        except Exception as e:  # noqa: BLE001 - whatever cut the connection, say so and leave
            print(f"[agents] lost the host: {e}. Is `orchestra-agents run` still up?")
            return 1
    return 0


def cmd_chat(args) -> int:
    """Talk to the Supervisor through the host's control endpoint (control.py)."""
    import urllib.request

    project = _project(args)
    store, _ = _store(project)
    ctl = store.runtime().get("control") or {}
    if ctl.get("port"):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{ctl['port']}/health", timeout=3)
            return _chat_attached(project, store, int(ctl["port"]))
        except OSError:
            print(f"[agents] the host's control endpoint on port {ctl['port']} is not answering.")
    print("[agents] the Supervisor is reachable while `orchestra-agents run` is up; start it first.")
    return 1


def cmd_roles(args) -> int:
    project = _project(args)
    store, _ = _store(project)
    if args.action == "list":
        for r in tooling.handle(project, store, "list_roles", {})["roles"]:
            kind = "built-in" if r["builtin"] else "custom"
            wake = ", ".join(r.get("wake_on") or []) + (f" every {r['every']}" if r.get("every") else "") + (f" at {r['at']}" if r.get("at") else "")
            print(f"{r['name']:20} {kind:9} {r['session']:9} {'paused' if r['paused'] else '':7} {wake.strip() or '-'}")
        return 0
    print(json.dumps(tooling.handle(project, store, "get_role", {"name": args.name}), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orchestra-agents", description="AI agents as Orchestra machines: they fill a task system's roles over the bus")
    p.add_argument("--repo", help="repository root (default: ORCHESTRA_REPO or cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run", help="run the agents' host: resident agents, wake-ups, assignments for the roles they fill")
    s.add_argument("--once", action="store_true", help="until nothing is running, then exit")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("chat", help="talk to the Supervisor")
    s.set_defaults(fn=cmd_chat)
    s = sub.add_parser("sessions")
    s.add_argument("action", choices=["list", "show"])
    s.add_argument("id", nargs="?")
    s.set_defaults(fn=cmd_sessions)
    s = sub.add_parser("tools", help="the tools agents get, as data (used by the Pi extension)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--groups", help="comma-separated tool groups (default: ORCHESTRA_TOOLS, else all)")
    s.set_defaults(fn=cmd_tools)
    s = sub.add_parser("api", help="the interface the agents' tools call")
    s.add_argument("command")
    s.add_argument("--args", default="{}", help="JSON object of arguments")
    s.add_argument("--compact", action="store_true")
    s.set_defaults(fn=cmd_api)
    s = sub.add_parser("roles", help="agent roles: built-in and custom")
    s.add_argument("action", choices=["list", "show"])
    s.add_argument("name", nargs="?")
    s.set_defaults(fn=cmd_roles)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
