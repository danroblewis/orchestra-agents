"""The dummy harness's agent: one turn of a scripted agent, as a process, with no model.

    python -m orchestra_agents.harness.dummy_agent <transcript.jsonl>   # the prompt on stdin

It plays the rule of the script (``dummy_script.py``) that matches the turn, writes the
transcript (already in the common message shape ``read_transcript`` returns) and prints the
normalized events (``base.py``) on stdout, one JSON object per line, ending with ``settled``, or
``error`` when a ``fail`` step or a broken script ends the turn (exit status 1). The agents' tools
are called as the MCP server calls them (``mcp.Server.call``: only the session's tool groups,
``tooling.check_args``, then ``tooling.handle`` with the session's role), so a scripted agent can
do exactly what a model could, and nothing more."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import dummy_script
from .dummy_script import Rule, ScriptError, Turn

OUTPUT_KEPT = 6000      # characters of a command's output or a tool's answer kept in the transcript


def emit(ev: dict) -> None:
    sys.stdout.write(json.dumps(ev) + "\n")
    sys.stdout.flush()


class Agent:
    def __init__(self, transcript: Path, cwd: Path):
        self.transcript, self.cwd = transcript, cwd
        self._server = None
        self.last_said = ""

    def record(self, role: str, text: str = "", tool_calls: list | None = None, **extra) -> None:
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        with self.transcript.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"role": role, "text": text, "tool_calls": tool_calls or [],
                                "ts": datetime.now(UTC).isoformat(timespec="milliseconds"), **extra}) + "\n")

    def say(self, text: str) -> None:
        emit({"type": "assistant_start"})
        emit({"type": "token", "kind": "text", "delta": text})
        emit({"type": "assistant_end", "text": text, "tool_calls": []})
        self.record("assistant", text)
        self.last_said = text

    def use(self, name: str, args: dict, run) -> str:
        """One tool call, as a model's would be: announced, run, its answer kept."""
        emit({"type": "assistant_start"})
        emit({"type": "tool_start", "name": name, "args": args})
        emit({"type": "assistant_end", "text": "", "tool_calls": [name]})
        self.record("assistant", tool_calls=[{"name": name, "args": args}])
        self.last_said = ""
        try:
            out, error = run(), False
        except Exception as e:  # noqa: BLE001 - a model sees the error and goes on; so does a script
            out, error = f"{type(e).__name__}: {e}", True
        if isinstance(out, tuple):
            out, error = out
        emit({"type": "tool_end", "name": name})
        self.record("toolResult", str(out)[-OUTPUT_KEPT:], tool_name=name, is_error=error)
        return str(out)

    def tool(self, name: str, args: dict) -> tuple[str, bool]:
        if self._server is None:
            from .mcp import Server
            self._server = Server()
        r = self._server.call(name, args)
        return "".join(c.get("text", "") for c in r.get("content", [])), bool(r.get("isError"))

    def shell(self, command: str) -> tuple[str, bool]:
        p = subprocess.run(["bash", "-c", command], cwd=self.cwd, capture_output=True, text=True, check=False)
        return (p.stdout + p.stderr + (f"\n(exit status {p.returncode})" if p.returncode else "")), p.returncode != 0

    def write(self, path: str, text: str, append: bool) -> str:
        f = self.cwd / path
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a" if append else "w", encoding="utf-8") as out:
            out.write(text)
        return f"wrote {path}"

    def commit(self, message: str) -> tuple[str, bool]:
        """Everything in the working directory, committed; a commit even when nothing changed, so
        a branch always has the commit a reviewer reads. Without a git identity configured, the
        commit is the dummy agent's."""
        who = [] if subprocess.run(["git", "config", "user.email"], cwd=self.cwd, capture_output=True, check=False).stdout.strip() \
            else ["-c", "user.name=dummy agent", "-c", "user.email=dummy@example.invalid"]
        add = subprocess.run(["git", "add", "-A"], cwd=self.cwd, capture_output=True, text=True, check=False)
        if add.returncode:
            return add.stderr, True
        p = subprocess.run(["git", *who, "commit", "-q", "--allow-empty", "-m", message], cwd=self.cwd,
                           capture_output=True, text=True, check=False)
        if p.returncode:
            return p.stdout + p.stderr, True
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=self.cwd, capture_output=True, text=True, check=False)
        return f"committed {head.stdout.strip()}", False

    def play(self, rule: Rule, t: Turn) -> str:
        """The rule's steps in order; the turn's last message. A ``fail`` step raises."""
        said, kept = "", {}
        for raw in rule.steps:
            step = dummy_script.fill_kept(t.fill(raw), kept)
            if "say" in step:
                said = step["say"]
                self.say(said)
            elif "write" in step:
                self.use("write", {"path": step["write"]}, lambda s=step: self.write(s["write"], s.get("text", ""), bool(s.get("append"))))
            elif "run" in step:
                self.use("shell", {"command": step["run"]}, lambda s=step: self.shell(s["run"]))
            elif "commit" in step:
                self.use("commit", {"message": step["commit"]}, lambda s=step: self.commit(s["commit"]))
            elif "tool" in step:
                said = self.use(step["tool"], step.get("args", {}), lambda s=step: self.tool(s["tool"], s.get("args", {})))
                if step.get("keep"):
                    try:
                        kept[step["keep"]] = json.loads(said)
                    except json.JSONDecodeError:
                        kept[step["keep"]] = said
            elif "sleep" in step:
                time.sleep(float(step["sleep"]))
            elif "fail" in step:
                raise TurnFailed(step["fail"])
        return str(dummy_script.fill_kept(t.fill(rule.reply), kept)) if rule.reply is not None else (said or "Done.")


class TurnFailed(RuntimeError):
    pass


def turns_so_far(transcript: Path) -> int:
    if not transcript.exists():
        return 0
    n = 0
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            n += json.loads(line).get("role") == "user"
        except (json.JSONDecodeError, AttributeError):
            continue
    return n


def script_path() -> Path | None:
    """``[agents] dummy_script`` of the project (read through Orchestra, the overlay merged),
    relative to its repository; None when unset."""
    from orchestra.config import load_project

    from ..config import Config
    o = load_project(os.environ.get("ORCHESTRA_REPO") or None)
    name = Config.from_raw(o.config.raw).dummy_script
    return (o.repo_root / name) if name else None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    transcript, cwd = Path(argv[0]), Path.cwd()
    prompt = sys.stdin.read()
    t = Turn(role=os.environ.get("ORCHESTRA_ROLE", ""), ticket=os.environ.get("ORCHESTRA_TICKET", ""), prompt=prompt,
             turn=turns_so_far(transcript) + 1, checkout=(cwd / ".git").exists(), instance=os.environ.get("ORCHESTRA_SOURCE", ""))
    agent = Agent(transcript, cwd)
    emit({"type": "turn_start", "pid": os.getpid()})
    agent.record("user", prompt)
    try:
        rule = dummy_script.load(script_path()).pick(t)
        text = agent.play(rule, t)
    except (ScriptError, TurnFailed) as e:
        emit({"type": "error", "error": str(e)})
        return 1
    if text != agent.last_said:
        agent.say(text)
    emit({"type": "settled", "text": text})
    return 0


if __name__ == "__main__":
    sys.exit(main())
