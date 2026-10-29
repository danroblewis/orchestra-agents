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
from .mcp_status import (
    BACKOFF,
    CONTINUE,
    FALLBACK,
    SHELL_NOW,
    SWITCHED,
    TOOL_PREFIX,
    policy_files,
    problem,
    refusal,
    refused_problem,
    try_command,
    until,
)

# Claude Code's own tools that stand in for Orchestra's work: a private todo list the model can
# "create a task" in, and schedules that die with the one-shot process. With them, a turn without
# Orchestra's tools reports work nobody will ever see; without them, it can only say it has none.
# Subagents (Task/Agent) stay: they research inside the turn, and their work is in the transcript.
# ReportFindings is the product's own review report: a reviewer that calls it reports to nobody.
NOT_ORCHESTRA = ("TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TodoWrite",
                 "CronCreate", "CronDelete", "CronList", "ScheduleWakeup", "RemoteTrigger", "ReportFindings")
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
        self.told: str | None = None     # how the session's system prompt says to call the tools: mcp or bash

    @property
    def harness_name(self) -> str:
        return "claude_code"

    def open(self) -> None:
        """Create or load the session's uuid. Claude writes the transcript on the first turn."""
        if self._meta_path.exists():
            meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            self.uuid, self.told = meta["uuid"], meta.get("tools")
        else:
            self.uuid = str(uuid.uuid4())
            self._write_meta()
        self.session_file = str(transcript_path(self.spec.cwd, self.uuid))

    def _write_meta(self) -> None:
        meta = {"uuid": self.uuid, "cwd": str(self.spec.cwd), "session_id": self.spec.session_id}
        if self.told:
            meta["tools"] = self.told
        self._meta_path.write_text(json.dumps(meta) + "\n", encoding="utf-8")

    def _resuming(self) -> bool:
        if self.uuid is None:
            self.open()
        return bool(self.session_file and Path(self.session_file).exists())

    def _tell(self, text: str, via: str) -> str:
        """The turn's message. Claude Code keeps a resumed session's system prompt as it was when the
        session began (a new --append-system-prompt-file is not read), so a session begun with the
        tools through the MCP server that now calls them from its shell (``auto`` after the switch, or
        ``tools_via`` changed) is told how in its next message, once."""
        if via == "bash" and self._resuming() and self.told != "bash":      # (_resuming opens the session: told is read)
            section = bash_tools.prompt(self.spec.tool_groups, self._env().get("PATH"))
            return SHELL_NOW.format(section=section.strip()) + text
        return text

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
        resume = self._resuming()
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
                again = text
                if switched != "init" and self._resuming():
                    again = CONTINUE.format(tool=switched) + text     # resumed after a refused call: carry on from there
                cost = result.cost_usd
                result, _ = self._turn(again, timeout, idle_timeout, emit, "bash")
                if cost is not None:
                    result.cost_usd = (result.cost_usd or 0.0) + cost
            return result

    def _turn(self, text: str, timeout: float | None, idle_timeout: float | None, emit: _Normalizer,
              via: str) -> tuple[TurnResult, str | None]:
        """One ``claude -p`` run. Returns its result, and, when it was stopped to run again with the
        tools from the shell (``tools_via = "auto"``), why: ``"init"`` when the server was found
        blocked at the init event, before the model ran; the tool's name when a call to it was refused."""
        result = TurnResult()
        self.spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        text = self._tell(text, via)
        resume = self._resuming()
        stderr = self.spec.log_path.open("a", encoding="utf-8")
        try:
            self.proc = subprocess.Popen(self._args(via), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                         text=True, cwd=str(self.spec.cwd), env=self._env(),
                                         bufsize=1, start_new_session=True)
        except OSError as e:
            result.error = f"cannot start {self.binary}: {e}"
            stderr.close()
            return result, None
        emit.pid = self.proc.pid
        if not resume or via == "bash":      # a new session's system prompt says it; a resumed one's message did
            self.told = via
            self._write_meta()
        try:
            try:
                self.proc.stdin.write(text)  # type: ignore[union-attr]
                self.proc.stdin.close()      # type: ignore[union-attr]
            except (BrokenPipeError, OSError):
                pass
            clock = _Deadlines(timeout, idle_timeout)
            reader = _LineReader(self.proc.stdout)
            last_text = ""
            calls: dict[str, str] = {}       # Orchestra's tool calls through the server: tool_use id -> tool
            while True:
                why = clock.expired()
                if why:
                    result.timed_out, result.stopped_by, result.text = True, why, last_text
                    self.kill()
                    return result, None
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
                        return result, "init" if stop == "switch" else None
                emit(ev)
                if t == "assistant":
                    for part in (ev.get("message") or {}).get("content") or []:
                        if part.get("type") == "text" and part.get("text", "").strip():
                            last_text = part["text"]
                        elif part.get("type") == "tool_use" and str(part.get("name", "")).startswith(TOOL_PREFIX):
                            calls[part.get("id", "")] = part["name"]
                elif t == "user" and via == "mcp" and calls:
                    stop = self._refused(ev, calls, result, emit)
                    if stop:
                        self.kill()
                        result.text = last_text
                        return result, stop if self.spec.tools_via == "auto" else None
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
            return result, None
        finally:
            self.proc = None
            stderr.close()

    def _policy_files(self) -> list[Path]:
        return policy_files(claude_config_dir()) + self._project_settings()

    def _project_settings(self) -> list[Path]:
        """The settings Claude Code reads in the session's working directory, where a hook can be."""
        d = Path(self.spec.cwd) / ".claude"
        return [d / "settings.json", d / "settings.local.json"]

    def _refused(self, ev: dict, calls: dict[str, str], result: TurnResult, emit: _Normalizer) -> str | None:
        """A ``tool_result`` that says Claude Code refused a call to one of Orchestra's tools (a hook or a
        permission rule: mcp_status.refusal) is the server blocked, found at its first call. With
        ``tools_via = "auto"`` the turn is stopped and run again from the shell (said once, as at the
        init); returns the tool's name. With ``mcp`` it is a failure that says why, and no turn starts
        again for a while; returns ``"fail"``."""
        content = (ev.get("message") or {}).get("content")
        for part in content if isinstance(content, list) else []:
            if not (isinstance(part, dict) and part.get("type") == "tool_result" and part.get("tool_use_id") in calls):
                continue
            reason = refusal(part.get("content")) if part.get("is_error") else None
            if reason is None:
                continue
            tool = calls[part["tool_use_id"]]
            why = refused_problem(tool, reason, settings=[claude_config_dir() / "settings.json", *self._project_settings()])
            if self.spec.tools_via == "auto":
                if FALLBACK.switch(why):
                    emit._emit({"type": "notice", "text": SWITCHED + why})
                return tool.removeprefix(TOOL_PREFIX)
            at = BACKOFF.failed(why, self._policy_files())
            result.error = f"{why}\n\n{until(at)}"
            return "fail"
        return None

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
        self.uuid, self.told = str(uuid.uuid4()), None
        self._write_meta()
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
