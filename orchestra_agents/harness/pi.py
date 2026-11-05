"""The Pi harness: one ``pi --mode rpc`` process per live session.

Pi stays resident between turns (the process is the session), takes one JSON command
per line on stdin and streams events on stdout; ``agent_settled`` ends a turn. Tools
come from the Orchestra extension (``pi_extension.ts``), which registers the tool
definitions in ``tools.py`` and runs each call through ``<this interpreter> -m orchestra agents tool`` (ORCHESTRA_PYTHON); with ``[agents]
tools_via = "file"`` the extension is not loaded (``file_output.py``). With ``tools_via = "bash"`` it is
not loaded either: the session calls the same tools as ``orchestra agents tool <name> '<json>'`` from
Pi's own bash tool, told how by the same system prompt section as Claude Code's and Codex's bash
mode (``bash_tools.prompt``), and its transcript shows each such call as the tool it ran
(``bash_tools.parse``). ``auto`` loads the extension: Pi has no MCP server for a policy to block.

The model: ``--provider`` and ``--model`` as configured (``[agents] provider`` and ``model``; Pi
takes ``provider/id`` in ``model`` too, and for Anthropic the API's ids, ``claude-sonnet-4-5``,
``claude-sonnet-5``: ``pi --list-models`` lists those its credentials reach). A turn's cost
(``TurnResult.cost_usd``) is what Pi's usage records new since the last turn cost at the model's
prices (``pi_cost.py``: the session file, a compaction's call included; the stream's messages when
the file cannot be read), so the day's caps (``budget.py``) count Pi's turns in dollars as they do
Claude Code's; a model not priced there leaves it unknown, and the dollar caps then refuse to start
(``PiHarness.priced``, ``budget.unpriced``)."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import bash_tools, pi_cost
from .base import (
    TOOL_CALL_S,
    AgentSession,
    ContextSize,
    Harness,
    OnEvent,
    SessionSpec,
    TurnResult,
    _Deadlines,
    _NormalizerBase,
    assistant_text,
    records,
    resolve_binary,
    session_env,
)

EXTENSION_PATH = Path(__file__).parent / "pi_extension.ts"
# The providers' API keys Pi reads from its environment (its own login is in ~/.pi): the one sign-in a
# session keeps of the host's credentials.
PI_AUTH = ("ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "GEMINI_API_KEY",
           "GROQ_API_KEY", "CEREBRAS_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "ZAI_API_KEY",
           "AI_GATEWAY_API_KEY", "COPILOT_GITHUB_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
           "AWS_PROFILE", "AWS_BEARER_TOKEN_BEDROCK")


class PiError(RuntimeError):
    pass


class PiSession(AgentSession):
    def __init__(self, spec: SessionSpec, pi_binary: str | None = None):
        self.spec = spec
        args = [resolve_binary(pi_binary or os.environ.get("PI_BINARY", "pi")), "--mode", "rpc",
                "--session-dir", str(spec.session_dir), "--session-id", spec.session_id]
        if spec.tools_via not in ("file", "bash"):  # file: no Orchestra tools at all (file_output.py); bash: from the shell
            args += ["--extension", str(EXTENSION_PATH)]
        if spec.provider:
            args += ["--provider", spec.provider]
        if spec.model:
            args += ["--model", spec.model]
        if spec.read_only:                       # reads, searches and runs commands; edits no file
            args += ["--exclude-tools", "edit,write"]
        if spec.thinking:
            args += ["--thinking", spec.thinking]
        full_env = {**session_env(PI_AUTH + spec.pass_env), **spec.env, "ORCHESTRA_TOOLS": ",".join(spec.tool_groups),
                    "ORCHESTRA_PYTHON": sys.executable, "ORCHESTRA_TOOL_CALL_MS": str(int(TOOL_CALL_S * 1000))}
        system = spec.system_prompt or ""
        if spec.tools_via == "bash":              # the tools as a command Pi's bash tool runs, in this environment
            system = bash_tools.system(system, spec.tool_groups, full_env.get('PATH'))
        if system:
            args += ["--append-system-prompt", system]
        spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._stderr = spec.log_path.open("a", encoding="utf-8")
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr, text=True,
                                     cwd=str(spec.cwd), env=full_env, bufsize=1, start_new_session=True)   # its own group: an orphan is found by it
        self._q: queue.Queue[dict | None] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self._lock = threading.Lock()
        self.session_file = None
        self.meter = pi_cost.Meter(spec.model)      # what each turn's usage records cost
        self._metered = False

    @property
    def harness_name(self) -> str:
        return "pi"

    def _read(self) -> None:
        for line in self.proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if not line:
                continue
            try:
                self._q.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        self._q.put(None)

    def _send(self, cmd: dict) -> None:
        if self.proc.poll() is not None or self.proc.stdin is None:
            raise PiError(f"pi process for {self.session_id} is not running")
        self.proc.stdin.write(json.dumps(cmd) + "\n")
        self.proc.stdin.flush()

    def _next(self, timeout: float) -> dict | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return {}

    def _reply_ui(self, ev: dict) -> None:
        rid = ev.get("id")   # never block on an extension dialog: cancel it
        if rid is not None:
            try:
                self._send({"type": "extension_ui_response", "id": rid, "cancelled": True})
            except PiError:
                pass

    def command(self, cmd: dict, timeout: float = 30) -> dict:
        with self._lock:
            self._send(cmd)
            deadline = time.time() + timeout
            while time.time() < deadline:
                ev = self._next(1.0)
                if ev is None:
                    raise PiError("pi exited")
                if ev.get("type") == "response" and ev.get("command") == cmd["type"]:
                    return ev
                if ev.get("type") == "extension_ui_request":
                    self._reply_ui(ev)
            raise PiError(f"timeout waiting for {cmd['type']}")

    def open(self) -> None:
        r = self.command({"type": "get_state"})
        data = r.get("data", {}) if r.get("success") else {}
        self.session_file = data.get("sessionFile") or self.session_file
        if not self._metered:                       # a resumed session's earlier turns were counted by their own
            self.meter.entries(self.session_file)
            self._metered = True

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc.poll() is None else None

    def alive(self) -> bool:
        return self.proc.poll() is None

    def prompt(self, text: str, timeout: float | None, on_event: OnEvent | None = None,
               idle_timeout: float | None = None) -> TurnResult:
        with self._lock:
            result = TurnResult()
            emit = _Normalizer(on_event, self.proc.pid)
            self._send({"type": "prompt", "message": text})
            clock = _Deadlines(timeout, idle_timeout)
            last_text = ""
            streamed: list[float | None] = []        # each assistant message's cost, from the stream
            while True:
                why = clock.expired()
                if why:
                    result.timed_out, result.stopped_by, result.text = True, why, last_text
                    self._abort()
                    return result
                ev = self._next(clock.wait())
                if ev is None:
                    result.error = "pi exited"
                    return result
                if not ev:
                    continue
                clock.touch()
                t = ev.get("type")
                emit(ev)
                if t == "response" and ev.get("command") == "prompt" and not ev.get("success", True):
                    result.error = f"prompt rejected: {ev.get('error')}"
                    return result
                if t == "extension_ui_request":
                    self._reply_ui(ev)
                elif t == "message_end":
                    msg = ev.get("message", {})
                    if msg.get("role") == "assistant":
                        txt = assistant_text(msg.get("content"))
                        if txt.strip():
                            last_text = txt
                        if isinstance(msg.get("usage"), dict):
                            streamed.append(pi_cost.usage_cost(msg["usage"], msg.get("model") or self.spec.model))
                elif t == "agent_settled":
                    result.text = last_text
                    result.cost_usd = self._cost(streamed)
                    emit.settled(last_text)
                    return result

    def _cost(self, streamed: list[float | None]) -> float | None:
        """The turn's dollars: the session file's new usage records (``pi_cost.Meter``), or the
        stream's messages when the file has none; None when a model is not priced."""
        usd, n = self.meter.turn(self.session_file)
        if n:
            return usd
        return None if not streamed or None in streamed else round(sum(streamed), 6)   # type: ignore[arg-type]

    def compact(self) -> dict | None:
        """Pi's own compaction: older messages become a summary entry in the session file and
        the context is rebuilt from it, so the agent keeps what it learned. Waits for the
        response; summarising is a model call and can take a while on a loaded server."""
        with self._lock:
            try:
                self._send({"type": "compact"})
            except PiError:
                return None
            clock = _Deadlines(1800, 900)
            while not clock.expired():
                ev = self._next(clock.wait())
                if ev is None:
                    return None
                if not ev:
                    continue
                clock.touch()
                if ev.get("type") == "extension_ui_request":
                    self._reply_ui(ev)
                elif ev.get("type") == "response" and ev.get("command") == "compact":
                    if not ev.get("success", True):
                        return None
                    d = ev.get("data") or {}
                    return {"tokens_before": d.get("tokensBefore"), "tokens_after": d.get("estimatedTokensAfter")}
            return None

    def _abort(self) -> None:
        try:
            self._send({"type": "abort"})
            deadline = time.time() + 15
            while time.time() < deadline:
                ev = self._next(1.0)
                if ev is None or (ev and ev.get("type") == "agent_settled"):
                    return
                if ev and ev.get("type") == "response" and ev.get("command") == "abort":
                    return
        except PiError:
            pass

    def kill(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        try:
            self._stderr.close()
        except OSError:
            pass

    def retire(self) -> str | None:
        """The session file moves to ``<session_dir>/archive/``; Pi starts a new one for the id."""
        return archive(self.session_file)


def shown_call(name: str, args) -> tuple[str, dict]:
    """A tool call as a transcript shows it: Pi's bash tool running ``orchestra agents tool <name>``
    (``tools_via = "bash"``) is the Orchestra tool it ran, with its arguments."""
    if name == "bash" and isinstance(args, dict) and (called := bash_tools.parse(str(args.get("command") or ""))):
        return called
    return name, args


def archive(session_file: str | None) -> str | None:
    """Move a Pi session file beside its siblings under ``archive/``; the new path, or None."""
    if not session_file or not Path(session_file).exists():
        return None
    f = Path(session_file)
    dest = f.parent / "archive" / f.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    f.rename(dest)
    return str(dest)


class _Normalizer(_NormalizerBase):
    """Pi RPC events -> normalized events."""

    def __call__(self, ev: dict) -> None:
        t = ev.get("type")
        msg = ev.get("message") or {}
        if t == "response" and ev.get("command") == "prompt" and ev.get("success", True):
            self._emit({"type": "turn_start", "pid": self.pid})
        elif t == "message_start" and msg.get("role") == "assistant":
            self._emit({"type": "assistant_start"})
        elif t == "message_update":
            u = ev.get("assistantMessageEvent") or {}
            kind = {"text_delta": "text", "thinking_delta": "thinking"}.get(u.get("type"))
            self._emit({"type": "token", "kind": kind, "delta": u.get("delta") if kind else None})
        elif t == "tool_execution_start":
            name, args = shown_call(ev.get("toolName") or ev.get("name", "?"), ev.get("args") or ev.get("arguments") or {})
            self._emit({"type": "tool_start", "name": name, "args": args})
        elif t == "tool_execution_end":
            self._emit({"type": "tool_end", "name": ev.get("toolName") or ev.get("name", "?")})
        elif t == "message_end" and msg.get("role") == "assistant":
            content = msg.get("content")
            calls = [shown_call(p.get("name", "?"), p.get("arguments"))[0] for p in content
                     if isinstance(p, dict) and p.get("type") == "toolCall"] \
                if isinstance(content, list) else []
            self._emit({"type": "assistant_end", "text": assistant_text(content), "tool_calls": calls})


class PiHarness(Harness):
    name = "pi"

    def __init__(self, binary: str | None = None):
        self.binary = binary

    def sign_in(self, environ: dict[str, str] | None = None) -> tuple[str, ...]:
        return PI_AUTH

    def start(self, spec: SessionSpec) -> PiSession:
        return PiSession(spec, self.binary)

    def priced(self, model: str | None) -> bool | None:
        """Whether a turn on ``model`` can be priced (``pi_cost.PRICES``): the dollar caps need it."""
        return pi_cost.price(model) is not None

    @staticmethod
    def context(session_file: str | Path) -> ContextSize:
        """An assistant message's ``usage`` holds the call's tokens (``totalTokens``, or input,
        cache and output added up); a ``compaction`` entry is a compaction, with ``tokensBefore``."""
        out = ContextSize()
        for e in records(session_file):
            if e.get("type") == "compaction":
                out.compactions.append({"tokens_before": e.get("tokensBefore"), "tokens_after": None})
                out.tokens = None
            elif e.get("type") == "message" and (m := e.get("message") or {}).get("role") == "assistant" and isinstance(u := m.get("usage"), dict):
                out.tokens = int(u.get("totalTokens") or sum(int(u.get(k) or 0) for k in ("input", "cacheRead", "cacheWrite", "output")))
        return out

    @staticmethod
    def read_transcript(session_file: str | Path) -> list[dict]:
        """Parse a Pi session JSONL into [{role, text, tool_calls, ts}]."""
        out: list[dict] = []
        p = Path(session_file)
        if not p.exists():
            return out
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") != "message":
                continue
            msg = e.get("message", {})
            role = msg.get("role")
            if role not in ("user", "assistant", "toolResult"):
                continue
            content = msg.get("content")
            text, tools = "", []
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        text += part.get("text", "")
                    elif part.get("type") == "toolCall":
                        args = part.get("arguments")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                pass
                        name, args = shown_call(part.get("name"), args)
                        tools.append({"name": name, "args": args})
            entry = {"role": role, "text": text, "tool_calls": tools, "ts": e.get("timestamp")}
            if role == "toolResult":
                entry["text"] = text[:6000]
                entry["tool_name"] = msg.get("toolName")
                entry["is_error"] = bool(msg.get("isError"))
            out.append(entry)
        return out
