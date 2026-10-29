"""The Claude Code harness: ``claude -p`` headless, one process per turn, resumed by id.

Claude Code has no resident process: each turn is ``claude -p`` reading the prompt from
stdin, streaming ``--output-format stream-json`` events, and exiting. The session is the
transcript Claude keeps under its own config directory, resumed with ``--resume``.
The agents' tools reach the model through the MCP server in ``mcp.py``, or, with ``[agents]
tools_via = "bash"`` (or ``auto`` once the server is found blocked), through the command the
session runs from its Bash tool (``bash_tools.py``), with no MCP server at all."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from . import bash_tools
from .base import (
    AgentSession,
    ContextSize,
    Harness,
    OnEvent,
    SessionSpec,
    TurnResult,
    _Deadlines,
    _LineReader,
    _NormalizerBase,
    records,
    resolve_binary,
)
from .mcp_status import BACKOFF, FALLBACK, SWITCHED, policy_files, problem, try_command, until

TOOL_PREFIX = "mcp__orchestra__"
# Claude Code's own tools that stand in for Orchestra's work: a private todo list the model can
# "create a task" in, and schedules that die with the one-shot process. With them, a turn without
# Orchestra's tools reports work nobody will ever see; without them, it can only say it has none.
# Subagents (Task/Agent) stay: they research inside the turn, and their work is in the transcript.
NOT_ORCHESTRA = ("TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TodoWrite",
                 "CronCreate", "CronDelete", "CronList", "ScheduleWakeup", "RemoteTrigger")
EDITS = ("Edit", "Write", "NotebookEdit")    # taken from a read-only agent: it reads, searches and runs read-only commands


def claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def transcript_path(cwd: Path, session_uuid: str) -> Path:
    """Where Claude Code writes the session: projects/<cwd with every character but letters and
    digits as ->/<uuid>.jsonl (so /Users/me/.orchestrator/x is -Users-me--orchestrator-x)."""
    key = re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd).resolve()))
    return claude_config_dir() / "projects" / key / f"{session_uuid}.jsonl"


class ClaudeCodeSession(AgentSession):
    def __init__(self, spec: SessionSpec, binary: str | None = None):
        self.spec = spec
        self.binary = resolve_binary(binary or os.environ.get("CLAUDE_BINARY", "claude"))
        self._dir = spec.session_dir / "claude-code"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self._dir / f"{spec.session_id}.json"
        self._lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self._closed = False
        self.session_file = None
        self.uuid: str | None = None

    @property
    def harness_name(self) -> str:
        return "claude_code"

    def open(self) -> None:
        """Create or load the session's uuid. Claude writes the transcript on the first turn."""
        if self._meta_path.exists():
            meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            self.uuid = meta["uuid"]
        else:
            self.uuid = str(uuid.uuid4())
            self._meta_path.write_text(json.dumps({"uuid": self.uuid, "cwd": str(self.spec.cwd),
                                                   "session_id": self.spec.session_id}) + "\n", encoding="utf-8")
        self.session_file = str(transcript_path(self.spec.cwd, self.uuid))

    @property
    def pid(self) -> int | None:
        p = self.proc
        return p.pid if p is not None and p.poll() is None else None

    def alive(self) -> bool:
        return not self._closed

    def _mcp_env(self) -> dict[str, str]:
        env = {k: v for k, v in self.spec.env.items()}
        env["ORCHESTRA_TOOLS"] = ",".join(self.spec.tool_groups)
        env["ORCHESTRA_MCP_LOG"] = str(self.spec.log_path)      # where the server says why it could not start
        for k in ("ORCHESTRATOR_HOME", "PATH", "HOME"):
            if os.environ.get(k):
                env[k] = os.environ[k]
        return env

    def via(self) -> str:
        """How this turn calls Orchestra's tools: ``mcp`` or ``bash`` (``auto`` is MCP until the
        server is found not connected, then bash for every session: mcp_status.Fallback)."""
        if self.spec.tools_via == "bash" or (self.spec.tools_via == "auto" and FALLBACK.on):
            return "bash"
        return "mcp"

    def _env(self) -> dict[str, str]:
        """The turn's process environment, which Claude Code's Bash tool passes on to the commands it
        runs: the session's ORCHESTRA_* variables (its token among them) and its tool groups, which
        ``orchestra agents tool`` reads."""
        return {**os.environ, **self.spec.env, "ORCHESTRA_TOOLS": ",".join(self.spec.tool_groups)}

    def _mcp_config(self, via: str = "mcp") -> Path:
        """The MCP servers the turn may use: Orchestra's, or none at all when the tools come from the
        shell (with --strict-mcp-config, no other configured server is loaded either)."""
        servers = {} if via == "bash" else {"orchestra": {"command": sys.executable, "args": ["-P", "-m", "orchestra_agents.harness.mcp"],
                                                          "env": self._mcp_env()}}
        cfg = {"mcpServers": servers}
        p = self._dir / f"{self.spec.session_id}.{'none' if via == 'bash' else 'mcp'}.json"
        p.write_text(json.dumps(cfg, indent=1) + "\n", encoding="utf-8")
        return p

    def compact(self) -> dict | None:
        """Claude Code compacts its own context past the --autocompact window it was started
        with; there is no command to ask for it between turns. Saying so keeps the host from
        rotating a session the harness keeps bounded; each compaction it does is in the
        transcript (``context``)."""
        if not self.spec.compact_tokens:
            return None
        return {"mode": "auto", "limit": max(100_000, int(self.spec.compact_tokens))}

    def _args(self, via: str | None = None) -> list[str]:
        via = via or self.via()
        if self.uuid is None:
            self.open()
        resume = self.session_file and Path(self.session_file).exists()
        args = [self.binary, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--dangerously-skip-permissions", "--strict-mcp-config", "--mcp-config", str(self._mcp_config(via))]
        if self.spec.model:
            args += ["--model", self.spec.model]
        args += ["--disallowedTools", ",".join(NOT_ORCHESTRA + (EDITS if self.spec.read_only else ()))]
        if self.spec.compact_tokens:
            args += ["--autocompact", str(max(100_000, int(self.spec.compact_tokens)))]
        system = self.spec.system_prompt or ""
        if via == "bash":
            system = f"{system.rstrip()}\n\n{bash_tools.prompt(self.spec.tool_groups, self._env().get('PATH'))}"
        if system:
            sp = self._dir / f"{self.spec.session_id}.system.md"
            sp.write_text(system, encoding="utf-8")
            args += ["--append-system-prompt-file", str(sp)]
        args += ["--resume", self.uuid] if resume else ["--session-id", self.uuid]
        return args

    def prompt(self, text: str, timeout: float | None, on_event: OnEvent | None = None,
               idle_timeout: float | None = None) -> TurnResult:
        with self._lock:
            emit = _Normalizer(on_event)
            via = self.via()
            if via == "mcp" and self.spec.tools_via == "mcp" and (refused := BACKOFF.refusal(self._policy_files())):
                return TurnResult(error=refused)
            result, switched = self._turn(text, timeout, idle_timeout, emit, via)
            if switched:            # auto, and the server is not there: the same turn again, the tools from the shell
                result, _ = self._turn(text, timeout, idle_timeout, emit, "bash")
            return result

    def _turn(self, text: str, timeout: float | None, idle_timeout: float | None, emit: _Normalizer,
              via: str) -> tuple[TurnResult, bool]:
        """One ``claude -p`` run. Returns its result, and True when it was stopped at its init event
        to run again with the tools from the shell (``tools_via = "auto"``)."""
        result = TurnResult()
        self.spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        stderr = self.spec.log_path.open("a", encoding="utf-8")
        try:
            self.proc = subprocess.Popen(self._args(via), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                         text=True, cwd=str(self.spec.cwd), env=self._env(),
                                         bufsize=1, start_new_session=True)
        except OSError as e:
            result.error = f"cannot start {self.binary}: {e}"
            stderr.close()
            return result, False
        emit.pid = self.proc.pid
        try:
            try:
                self.proc.stdin.write(text)  # type: ignore[union-attr]
                self.proc.stdin.close()      # type: ignore[union-attr]
            except (BrokenPipeError, OSError):
                pass
            clock = _Deadlines(timeout, idle_timeout)
            reader = _LineReader(self.proc.stdout)
            last_text = ""
            while True:
                why = clock.expired()
                if why:
                    result.timed_out, result.stopped_by, result.text = True, why, last_text
                    self.kill()
                    return result, False
                line = reader.next(clock.wait())
                if line is None:          # EOF: the process exited
                    break
                if not line:
                    continue
                clock.touch()
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = ev.get("type")
                if t == "system" and ev.get("subtype") == "init" and via == "mcp":
                    stop = self._no_orchestra(ev, result, emit)
                    if stop:
                        self.kill()
                        return result, stop == "switch"
                emit(ev)
                if t == "assistant":
                    for part in (ev.get("message") or {}).get("content") or []:
                        if part.get("type") == "text" and part.get("text", "").strip():
                            last_text = part["text"]
                elif t == "result":
                    if isinstance(ev.get("total_cost_usd"), int | float):
                        result.cost_usd = float(ev["total_cost_usd"])
                    if ev.get("is_error"):
                        result.error = str(ev.get("result") or ev.get("subtype") or "error")
                    else:
                        last_text = ev.get("result") or last_text
            rc = self.proc.wait()
            if rc != 0 and not result.error:
                result.error = f"claude exited with status {rc}"
            result.text = last_text
            if not result.error:
                emit.settled(last_text)
            return result, False
        finally:
            self.proc = None
            stderr.close()

    def _policy_files(self) -> list[Path]:
        return policy_files(claude_config_dir())

    def _no_orchestra(self, init: dict, result: TurnResult, emit: _Normalizer) -> str | None:
        """The turn's first event: when Orchestra's MCP server is not connected, the turn ends
        here, before the model runs. With ``tools_via = "auto"`` it is run again with the tools
        from the shell ("switch"; said once, as a notice the host passes to the Supervisor);
        with ``mcp`` it is a failure that says why ("fail"), and no turn starts again for a while
        (mcp_status.Backoff)."""
        why = problem(init, config_dir=claude_config_dir(), try_cmd=try_command(sys.executable, self._mcp_env()),
                      log_path=self.spec.log_path)
        if why is None:
            BACKOFF.ok()
            return None
        if self.spec.tools_via == "auto":
            if FALLBACK.switch(why):
                emit._emit({"type": "notice", "text": SWITCHED + why.split("\n\n", 1)[-1]})
            return "switch"
        at = BACKOFF.failed(why, self._policy_files())
        result.error = f"{why}\n\n{until(at)}"
        return "fail"

    def kill(self) -> None:
        p = self.proc
        if p is None or p.poll() is not None:
            return
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass

    def close(self) -> None:
        self.kill()
        self._closed = True

    def retire(self) -> str | None:
        """A new uuid for the next turn; the transcript stays where Claude keeps it."""
        old = self.session_file if self.session_file and Path(self.session_file).exists() else None
        self.uuid = str(uuid.uuid4())
        self._meta_path.write_text(json.dumps({"uuid": self.uuid, "cwd": str(self.spec.cwd),
                                               "session_id": self.spec.session_id}) + "\n", encoding="utf-8")
        self.session_file = str(transcript_path(self.spec.cwd, self.uuid))
        return old


def _short(name: str) -> str:
    return name.removeprefix(TOOL_PREFIX)


def _call(part: dict) -> tuple[str, dict]:
    """A tool_use part as (tool, arguments): an Orchestra tool by its own name, whether it came
    through the MCP server or as ``orchestra agents tool`` run by the Bash tool (bash_tools.parse)."""
    args = part.get("input") or {}
    if part.get("name") == "Bash" and isinstance(args, dict) and (called := bash_tools.parse(str(args.get("command") or ""))):
        return called
    return _short(part.get("name", "?")), args


class _Normalizer(_NormalizerBase):
    """Claude Code stream-json events -> normalized events."""

    def __init__(self, on_event: OnEvent | None):
        super().__init__(on_event)
        self._pending: dict[str, str] = {}   # tool_use id -> name

    def __call__(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            self._emit({"type": "turn_start", "pid": self.pid})
        elif t == "stream_event":
            inner = (ev.get("event") or {}).get("type")
            if inner == "message_start":
                self._emit({"type": "assistant_start"})
            elif inner == "content_block_delta":
                self._emit({"type": "token"})
        elif t == "assistant":
            content = (ev.get("message") or {}).get("content") or []
            calls = []
            for part in content:
                if part.get("type") == "tool_use":
                    name, args = _call(part)
                    calls.append(name)
                    self._pending[part.get("id", "")] = name
                    self._emit({"type": "tool_start", "name": name, "args": args})
            text = "".join(p.get("text", "") for p in content if p.get("type") == "text")
            if text.strip() or calls:
                self._emit({"type": "assistant_end", "text": text, "tool_calls": calls})
        elif t == "user":
            content = (ev.get("message") or {}).get("content")
            for part in content if isinstance(content, list) else []:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    name = self._pending.pop(part.get("tool_use_id", ""), "?")
                    self._emit({"type": "tool_end", "name": name})


class ClaudeCodeHarness(Harness):
    name = "claude_code"

    def __init__(self, binary: str | None = None):
        self.binary = binary

    def start(self, spec: SessionSpec) -> ClaudeCodeSession:
        return ClaudeCodeSession(spec, self.binary)

    def model_for(self, wanted: str | None) -> str | None:
        """Claude Code takes the aliases (``haiku``, ``sonnet``, ``opus``) and full model ids."""
        w = (wanted or "").strip()
        return w if w in ("haiku", "sonnet", "opus") or w.startswith("claude-") else None

    @staticmethod
    def context(session_file: str | Path) -> ContextSize:
        """Each assistant record carries the API's usage (input, cache write, cache read and
        output tokens: the context of that call); a ``compact_boundary`` system record is a
        compaction Claude Code did by itself, with the tokens before and after. A subagent's
        records (``isSidechain``) are another context and are skipped."""
        out = ContextSize()
        for e in records(session_file):
            if e.get("isSidechain"):
                continue
            if e.get("type") == "system" and e.get("subtype") == "compact_boundary":
                meta = e.get("compactMetadata") or {}
                out.compactions.append({"tokens_before": meta.get("preTokens"), "tokens_after": meta.get("postTokens")})
                out.tokens = None
            elif e.get("type") == "assistant" and isinstance(u := (e.get("message") or {}).get("usage"), dict):
                out.tokens = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
        return out

    @staticmethod
    def read_transcript(session_file: str | Path) -> list[dict]:
        """Parse a Claude Code session JSONL into [{role, text, tool_calls, ts}]."""
        out: list[dict] = []
        p = Path(session_file)
        if not p.exists():
            return out
        names: dict[str, str] = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = e.get("type")
            if t not in ("user", "assistant"):
                continue
            msg = e.get("message") or {}
            content = msg.get("content")
            ts = e.get("timestamp")
            if t == "assistant":
                text, tools = "", []
                for part in content if isinstance(content, list) else []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        text += part.get("text", "")
                    elif part.get("type") == "tool_use":
                        name, args = _call(part)
                        names[part.get("id", "")] = name
                        tools.append({"name": name, "args": args})
                if text or tools:
                    out.append({"role": "assistant", "text": text, "tool_calls": tools, "ts": ts})
                continue
            if isinstance(content, str):
                if content.strip():
                    out.append({"role": "user", "text": content, "tool_calls": [], "ts": ts})
                continue
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_result":
                    body = part.get("content")
                    if isinstance(body, list):
                        body = "".join(b.get("text", "") for b in body if isinstance(b, dict) and b.get("type") == "text")
                    out.append({"role": "toolResult", "text": str(body or "")[:6000], "tool_calls": [], "ts": ts,
                                "tool_name": names.get(part.get("tool_use_id", ""), "?"),
                                "is_error": bool(part.get("is_error"))})
                elif part.get("type") == "text" and part.get("text", "").strip():
                    out.append({"role": "user", "text": part["text"], "tool_calls": [], "ts": ts})
        return out
