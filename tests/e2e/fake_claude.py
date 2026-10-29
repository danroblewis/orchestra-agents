#!/usr/bin/env python3
"""A fake `claude -p` for end-to-end tests of the Claude Code harness.

Accepts the flags orchestra_agents.harness.claude_code passes, reads the prompt from stdin,
and emits `--output-format stream-json` events. It plays the role named in the MCP
config's env (ORCHESTRA_ROLE) exactly like fake_pi.py plays it, but every Orchestra
tool call goes through the real MCP server named in that config (JSON-RPC over stdio),
so the server is exercised end to end. It appends a Claude-format transcript under
CLAUDE_CONFIG_DIR/projects/<cwd>/<uuid>.jsonl, where the harness expects it.

A code worker runs the description's ``sh`` blocks, makes the files the acceptance criteria name
(do_what_it_asks), writes its ticket's own note under work/ and commits. A reviewer judges: each
`file` named in the acceptance criteria must exist in the workspace and each "text" must be in
RESULT.md there, else it rejects with a line per failure (criteria of other shapes are not its
to check).

Env knobs shared with fake_pi: FAKE_PI_WORKER_BLOCK, FAKE_PI_REJECT_FIRST, FAKE_PI_BREAK_FIRST.
FAKE_CLAUDE_SLOW=<seconds>: a reviewer says what it is reading, then takes that long before its
verdict, so what it is doing can be seen while it holds the ticket.
FAKE_CLAUDE_SUPERVISOR=<file>: in a chat turn (one with the human's message) the Supervisor calls
the tools the file lists, a JSON list of {"tool", "args"}, in order, and replies with what each
answered, or with the text of a {"reply"} step when the list has one (a reply as a model might
get it wrong); without it, a Supervisor only acknowledges.
FAKE_CLAUDE_COORDINATOR=<file>: woken with a Supervisor's hand-off (work.requested), the Coordinator
calls the tools the file lists the same way, with {request} replaced by the request's id.

With no ``orchestra`` server in the MCP config (``[agents] tools_via = "bash"``, or ``auto`` after
the switch) every tool call is a Bash call of the command the system prompt gives (``Shell``).
FAKE_CLAUDE_MCP_BLOCKED=1: the ``orchestra`` server, when configured, never connects (status
``failed``), as when an organisation's policy blocks it."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid as uuidlib
from datetime import UTC, datetime
from pathlib import Path


class Shell:
    """Orchestra's tools with no MCP server (``[agents] tools_via = "bash"``): each call is the
    command the system prompt gives, run by a shell in the session's working directory with this
    process's environment, as Claude Code's Bash tool runs it."""

    def __init__(self):
        system = Path(arg("--append-system-prompt-file")).read_text()
        found = re.search(r"^    (.+) <tool> '<arguments as one JSON object>'$", system, re.MULTILINE)
        assert found, "the system prompt does not say how to call the tools from the shell"
        self.cmd = found.group(1)

    def command(self, name: str, args: dict) -> str:
        return f"{self.cmd} {name} {shlex.quote(json.dumps(args))}"

    def run(self, command: str) -> str:
        p = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, env=dict(os.environ), check=False)
        return (p.stdout + p.stderr).strip()

    def tools(self) -> set[str]:
        return {line.split()[0] for line in self.run(f"{self.cmd} --list").splitlines() if line.strip()}


def reviewed_marker(cwd):
    """Where this fake remembers it rejected once: in the worktree's git directory, never in the
    tree, which a reviewer leaves at the reviewed commit (untracked files removed)."""
    import subprocess
    from pathlib import Path
    r = subprocess.run(["git", "rev-parse", "--git-path", "fake_reviewed"], cwd=cwd, capture_output=True, text=True, check=False)
    return Path(cwd) / r.stdout.strip() if r.returncode == 0 else Path(cwd) / ".fake_reviewed"

def arg(flag: str) -> str | None:
    a = sys.argv[1:]
    return a[a.index(flag) + 1] if flag in a else None


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


class Mcp:
    """Minimal MCP client over stdio, enough to call Orchestra's tools."""

    def __init__(self, server: dict):
        self.p = subprocess.Popen([server["command"], *server["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, env={**os.environ, **server.get("env", {})}, bufsize=1)
        self._id = 0
        self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "fake-claude", "version": "0"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()

    def rpc(self, method: str, params: dict) -> dict:
        self._id += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("mcp server exited")
            msg = json.loads(line)
            if msg.get("id") == self._id:
                return msg

    def call(self, name: str, args: dict) -> str:
        r = self.rpc("tools/call", {"name": name, "arguments": args})
        return "".join(c.get("text", "") for c in r.get("result", {}).get("content", []))

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait(timeout=10)


def judge(prompt: str, cwd: Path) -> list[str]:
    """What the acceptance criteria ask for and the workspace does not show."""
    m = re.search(r"## Acceptance criteria\n(.*?)(?=\n## |\Z)", prompt, re.DOTALL)
    criteria = m.group(1) if m else ""
    result = (cwd / "RESULT.md").read_text() if (cwd / "RESULT.md").exists() else None
    failures = [f"`{name}` is not in the workspace" for name in re.findall(r"`([^`\n]+)`", criteria) if not (cwd / name).exists()]
    for text in re.findall(r'"([^"\n]+)"', criteria):
        if result is None:
            failures.append(f'RESULT.md is missing, so it cannot show "{text}"')
        elif text not in result:
            failures.append(f'RESULT.md does not show "{text}"')
    return failures


# What the fake writes for a ticket asking for a collector (machines/repo_stats.sh, declared under
# [[collect]]): a real one, written as docs/machines.md in Orchestra's repository says (a script that
# prints Prometheus text and imports nothing of Orchestra's), so the demo's e2e can see it land, run
# and its series reach the store.
COLLECTOR = """#!/bin/sh
# How many files git tracks, as Prometheus text.
set -eu
echo "repo_files $(git ls-files | wc -l)"
"""
DECLARED = '\n[[collect]]\nname = "repo_stats"\ncommand = "machines/repo_stats.sh"\nevery = "3s"\n'


def do_what_it_asks(prompt: str, cwd: Path) -> None:
    """A code worker's stand-in for doing the ticket: it runs the description's ``sh`` blocks, if
    any (a ticket written for a program worker), and makes each file the acceptance criteria name
    that is not there yet, so the fake reviewer finds them. A ticket for the repository's collector
    (its acceptance names machines/repo_stats.sh and [[collect]]) gets a real one, declared in
    orchestra.toml."""
    m = re.search(r"## Description\n(.*?)(?=\n## |\Z)", prompt, re.DOTALL)
    for block in re.findall(r"```(?:sh|bash|shell)\s*\n(.*?)```", m.group(1) if m else "", re.DOTALL):
        subprocess.run(["bash", "-c", block], cwd=cwd, check=False)
    m = re.search(r"## Acceptance criteria\n(.*?)(?=\n## |\Z)", prompt, re.DOTALL)
    acceptance = m.group(1) if m else ""
    if "machines/repo_stats.sh" in acceptance and "[[collect]]" in acceptance:
        (cwd / "machines").mkdir(exist_ok=True)
        script = cwd / "machines" / "repo_stats.sh"
        script.write_text(COLLECTOR)
        script.chmod(0o755)
        toml = cwd / "orchestra.toml"
        toml.write_text(toml.read_text() + DECLARED)
    for name in re.findall(r"`([^`\s]+)`", acceptance):
        path = cwd / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            # a test file holds a test that passes: a gate running pytest fails on a file with none
            body = "def test_written_by_the_fake_worker():\n    assert True\n" if path.name.startswith("test_") and path.suffix == ".py" else ""
            path.write_text("# written by the fake worker\n" + body)


def main() -> None:
    sid = arg("--session-id") or arg("--resume") or str(uuidlib.uuid4())
    prompt = sys.stdin.read()
    mcp_cfg = json.loads(Path(arg("--mcp-config")).read_text())["mcpServers"].get("orchestra")
    env = (mcp_cfg or {}).get("env") or os.environ      # no server: the tools come from the shell, with this process's env
    role, ticket = env.get("ORCHESTRA_ROLE", ""), env.get("ORCHESTRA_TICKET", "")
    cwd = Path.cwd()
    projects = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    tdir = projects / re.sub(r"[^A-Za-z0-9]", "-", str(cwd.resolve()))   # as Claude Code names it
    tdir.mkdir(parents=True, exist_ok=True)
    transcript = tdir / f"{sid}.jsonl"
    if arg("--session-id") and any(projects.glob(f"*/{sid}.jsonl")):     # as Claude Code refuses it
        print(f"Error: Session ID {sid} is already in use.", file=sys.stderr)
        sys.exit(1)

    def record(entry_type: str, content) -> None:
        with transcript.open("a") as f:
            f.write(json.dumps({"type": entry_type, "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                                "message": {"role": entry_type, "content": content}, "sessionId": sid}) + "\n")

    mcp, servers = None, []
    if mcp_cfg is not None:
        try:                                # as Claude Code does: the MCP servers connect before the init event names them
            if os.environ.get("FAKE_CLAUDE_MCP_BLOCKED"):
                raise OSError("blocked by the organisation's policy")
            mcp = Mcp(mcp_cfg)
            mcp_status = "connected"
        except (OSError, RuntimeError, ValueError):
            mcp_status = "failed"
        servers = [{"name": "orchestra", "status": mcp_status}]
    emit({"type": "system", "subtype": "init", "session_id": sid, "cwd": str(cwd), "mcp_servers": servers})
    if mcp_cfg is not None and mcp is None:  # a real one would go on without the tools; the harness stops it here
        time.sleep(30)
        return
    record("user", prompt)
    shell = Shell() if mcp is None else None
    tools = shell.tools() if shell else {t["name"] for t in mcp.rpc("tools/list", {}).get("result", {}).get("tools", [])}

    def use_tool(name: str, args: dict) -> str:
        assert name in tools, f"{role} was not granted {name}; has {sorted(tools)}"
        tid = f"toolu_{uuidlib.uuid4().hex[:12]}"
        call = {"name": "Bash", "input": {"command": shell.command(name, args)}} if shell else \
            {"name": f"mcp__orchestra__{name}", "input": args}
        emit({"type": "stream_event", "event": {"type": "message_start"}, "session_id": sid})
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": ""}}})
        use = [{"type": "tool_use", "id": tid, **call}]
        emit({"type": "assistant", "message": {"role": "assistant", "content": use}, "session_id": sid})
        record("assistant", use)
        out = shell.run(call["input"]["command"]) if shell else mcp.call(name, args)
        res = [{"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": out}]}]
        emit({"type": "user", "message": {"role": "user", "content": res}, "session_id": sid})
        record("user", res)
        return out

    text = "ok"
    if role == "worker":
        if os.environ.get("FAKE_PI_WORKER_BLOCK"):
            use_tool("report_ticket", {"state": "BLOCKED", "summary": "cannot", "blocked_reason": "need a decision"})
            text = "I am blocked."
        else:
            checkout = (cwd / ".git").exists()           # a code ticket's worktree; a task's folder is not a checkout
            # in a checkout each ticket writes a file of its own, so two tickets' branches land side by side
            f = cwd / "work" / f"{ticket}.md" if checkout else cwd / "WORK.md"
            f.parent.mkdir(exist_ok=True)
            f.write_text((f.read_text() if f.exists() else "") + f"work on {ticket}\n")
            broke = cwd / ".fake_broke"
            if os.environ.get("FAKE_PI_BREAK_FIRST") and not broke.exists():
                broke.write_text("1")
                (cwd / "README.md").unlink()
            elif broke.exists() and not (cwd / "README.md").exists():
                (cwd / "README.md").write_text("# e2e\n")
            if checkout:
                do_what_it_asks(prompt, cwd)
                subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
                subprocess.run(["git", "commit", "-qm", f"{ticket}: fake work"], cwd=cwd, check=True)
            else:
                (cwd / "RESULT.md").write_text(f"# {ticket}\n\nthe answer\n")
            use_tool("report_ticket", {"state": "READY_FOR_REVIEW", "summary": "added WORK.md line"})
            text = "Done, reported ready."
    elif role == "reviewer":
        if os.environ.get("FAKE_CLAUDE_SLOW"):
            said = "Reading RESULT.md against the acceptance criteria."
            emit({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": said}]}, "session_id": sid})
            record("assistant", [{"type": "text", "text": said}])
            time.sleep(float(os.environ["FAKE_CLAUDE_SLOW"]))
        marker = reviewed_marker(cwd)
        failures = judge(prompt, cwd)
        if os.environ.get("FAKE_PI_REJECT_FIRST") and not marker.exists():
            marker.write_text("1")
            use_tool("report_review", {"verdict": "REJECTED", "summary": "needs work", "feedback": "1. add more"})
            text = "Rejected."
        elif failures:
            use_tool("report_review", {"verdict": "REJECTED", "summary": "the acceptance criteria are not met",
                                       "feedback": "\n".join(f"{i}. {f}" for i, f in enumerate(failures, 1))})
            text = "Rejected: " + "; ".join(failures)
        else:
            use_tool("report_review", {"verdict": "APPROVED", "summary": "looks good"})
            text = "Approved."
    elif role == "coordinator" and os.environ.get("FAKE_CLAUDE_COORDINATOR") and (
            asked := re.search(r"event work\.requested request=(r-[0-9a-f]+)", prompt)):
        steps = json.loads(Path(os.environ["FAKE_CLAUDE_COORDINATOR"]).read_text().replace("{request}", asked.group(1)))
        said = [f"- {s['tool']}: {use_tool(s['tool'], s.get('args', {}))}" for s in steps if "tool" in s]
        text = "Planned:\n" + "\n".join(said)
    elif role == "supervisor" and os.environ.get("FAKE_CLAUDE_SUPERVISOR") and "[The human's message]" in prompt:
        steps = json.loads(Path(os.environ["FAKE_CLAUDE_SUPERVISOR"]).read_text())
        said = [f"- {s['tool']}: {use_tool(s['tool'], s.get('args', {}))}" for s in steps if "tool" in s]
        replies = [s["reply"] for s in steps if "reply" in s]
        text = replies[-1] if replies else "What I did with my tools:\n" + "\n".join(said)
    else:
        text = f"{role} acknowledges: {prompt[:60]}"
    if mcp is not None:
        mcp.close()
    emit({"type": "stream_event", "event": {"type": "message_start"}, "session_id": sid})
    emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}})
    emit({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}, "session_id": sid})
    record("assistant", [{"type": "text", "text": text}])
    emit({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": sid, "duration_ms": 1})
    time.sleep(0.05)


if __name__ == "__main__":
    main()
