"""The harness interface: how the host drives an agent session, whichever product runs it.

A *harness* is an agent runner (Pi, Claude Code, Codex). The host needs four things from one,
and nothing else:

1. ``start(spec)`` opens a named, resumable session with a system prompt, a working
   directory, and a set of tool groups.
2. ``session.prompt(text, timeout, on_event)`` runs one turn to completion and returns
   its final text; events arrive in the **normalized** vocabulary below so the host and
   the UI never see harness-specific shapes.
3. ``session.session_file`` names the harness's own transcript, which is the only one
   the host keeps (it never writes a parallel log); ``harness.read_transcript`` parses
   it into a common message list.
4. ``kill()`` / ``close()`` end a turn or the session; ``retire()`` forgets it, so the next
   open starts a new one, and says where the old transcript remains.

Normalized events (dicts with ``type``):
  turn_start {pid}           the prompt was accepted; pid is the process running the turn
  assistant_start            the model began a message
  token                      output is streaming (may be coalesced; used for liveness)
  tool_start {name, args}    a tool call began
  tool_end {name}            it finished
  assistant_end {text, tool_calls}   an assistant message completed
  settled {text}             the turn is over
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

OnEvent = Callable[[dict], None]


def resolve_binary(binary: str) -> str:
    """The product's binary as configured: a bare name is found on PATH when a session starts;
    a path is made absolute here, because every session runs in its own working directory
    (the project, or a ticket's workspace), against which a relative path would be looked up."""
    return str(Path(binary).resolve()) if os.sep in binary else binary


class _LineReader:
    """A harness's stdout, one line at a time with a timeout, so a turn's loop can keep its
    deadlines while the process is silent. ``next`` returns "" when nothing arrived in time and
    None at end of file."""

    def __init__(self, stream):
        self._q: queue.Queue[str | None] = queue.Queue()

        def run():
            for line in stream:
                self._q.put(line.rstrip("\n"))
            self._q.put(None)
        threading.Thread(target=run, daemon=True).start()

    def next(self, timeout: float) -> str | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return ""


class _NormalizerBase:
    """What every harness's normalizer shares: the listener, which never breaks the stream, the
    turn's process once started, and the ``settled`` event that ends a turn. Each harness's
    ``_Normalizer`` turns its own events into the normalized ones in ``__call__``."""

    def __init__(self, on_event: OnEvent | None, pid: int | None = None):
        self.on_event, self.pid = on_event, pid

    def _emit(self, ev: dict) -> None:
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:  # noqa: BLE001, S110 - a listener never breaks the stream
                pass

    def settled(self, text: str) -> None:
        self._emit({"type": "settled", "text": text})


@dataclass
class TurnResult:
    text: str = ""
    timed_out: bool = False
    stopped_by: str | None = None    # "silence" (no event for idle_timeout) or "limit" (the hard cap), when timed_out
    error: str | None = None         # the harness failed: the turn did not end on the agent's terms


class _Deadlines:
    """Shared turn-ending rule for every harness: a turn ends when the agent settles, when
    nothing has arrived from the harness for `idle` seconds (the agent is presumed stuck),
    or, only if a hard cap is set, when `cap` seconds have passed in total. A busy agent is
    never cut off by the cap unless the project asked for one."""

    def __init__(self, cap: float | None, idle: float | None):
        now = time.time()
        self.hard = now + cap if cap else None
        self.idle = idle if idle else None
        self.last = now

    def touch(self) -> None:
        self.last = time.time()

    def expired(self) -> str | None:
        now = time.time()
        if self.hard is not None and now >= self.hard:
            return "limit"
        if self.idle is not None and now - self.last >= self.idle:
            return "silence"
        return None

    def wait(self) -> float:
        """How long to block for the next event: until the nearest deadline, at most 5s."""
        now = time.time()
        waits = [5.0]
        if self.hard is not None:
            waits.append(self.hard - now)
        if self.idle is not None:
            waits.append(self.last + self.idle - now)
        return max(0.1, min(waits))


@dataclass
class SessionSpec:
    """Everything a harness needs to open a session. ``env`` carries the ORCHESTRA_*
    variables the agents' tools read (repo, role, ticket, tool groups)."""

    session_id: str
    session_dir: Path          # Orchestra's per-project sessions directory
    cwd: Path
    system_prompt: str
    role: str
    tool_groups: tuple[str, ...]
    env: dict[str, str]
    log_path: Path             # harness stderr / diagnostics
    model: str | None = None
    provider: str | None = None
    thinking: str | None = None
    compact_tokens: int | None = None   # ask the harness to compact its own context past this many tokens, if it can
    read_only: bool = False    # the agent reads the checkout and never edits a file: its harness's file-editing tools are taken away


class AgentSession(ABC):
    """One named session. Thread-safe for one prompt at a time."""

    spec: SessionSpec
    session_file: str | None = None   # the harness's transcript, once known

    @property
    def session_id(self) -> str:
        return self.spec.session_id

    @property
    def harness_name(self) -> str:
        return "unknown"

    @abstractmethod
    def open(self) -> None:
        """Make the session exist and learn ``session_file``. Idempotent."""

    @abstractmethod
    def prompt(self, text: str, timeout: float | None, on_event: OnEvent | None = None,
               idle_timeout: float | None = None) -> TurnResult:
        """Run one turn. `timeout` is a hard cap in seconds (None or 0: none); `idle_timeout`
        ends the turn after that many seconds without any event from the harness."""
        ...

    def compact(self) -> dict | None:
        """Ask the harness to summarise the older part of this session's context in place,
        keeping the transcript. Returns the harness's result (tokens before and after) or
        None when this harness cannot compact; the host then falls back to rotation."""
        return None

    @property
    @abstractmethod
    def pid(self) -> int | None:
        """A live OS process that proves this session is alive right now, or None."""

    @abstractmethod
    def alive(self) -> bool: ...

    @abstractmethod
    def kill(self) -> None:
        """Stop the running turn immediately (a release by the task system, or shutdown)."""

    @abstractmethod
    def close(self) -> None:
        """Release the session's resources. The transcript stays; the session is resumable."""

    @abstractmethod
    def retire(self) -> str | None:
        """Forget this session, after ``close``, so the next ``open`` of the same id starts a new
        one. Returns the path where the old transcript remains (the harness decides where), or
        None when no turn ever ran."""


@dataclass
class ContextSize:
    """How full a session's context is, as the harness's own transcript records it."""
    tokens: int | None = None      # at the last model call since the last compaction; None: no call since, or none recorded
    compactions: list[dict] = field(default_factory=list)   # every compaction recorded, oldest first: {tokens_before, tokens_after}


def records(session_file: str | Path) -> Iterator[dict]:
    """A JSONL transcript's records; a line that is not a JSON object is skipped."""
    p = Path(session_file)
    if not p.exists():
        return
    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(e, dict):
                yield e


class Harness(ABC):
    name: str = "unknown"

    @abstractmethod
    def start(self, spec: SessionSpec) -> AgentSession: ...

    @staticmethod
    @abstractmethod
    def read_transcript(session_file: str | Path) -> list[dict]:
        """Parse the harness's transcript into [{role, text, tool_calls, ts, ...}] where role
        is user | assistant | toolResult. ``ts`` is an ISO-8601 string or epoch seconds."""

    @staticmethod
    def context(session_file: str | Path) -> ContextSize:
        """How full the session's context is: the tokens the harness's last model call reported,
        and the compactions its transcript records (whoever asked for them). Never the
        transcript's size, which holds far more than the context (tool output, request records).
        Default: nothing known, so nothing is compacted or rotated."""
        return ContextSize()


def assistant_text(content) -> str:
    """Text of an assistant message whose content is a string or a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""
