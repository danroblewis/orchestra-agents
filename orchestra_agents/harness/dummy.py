"""The dummy harness: a scripted agent with no model, no network and no cost.

Each turn is one ``python -m orchestra_agents.harness.dummy_agent`` process, as each turn of
Claude Code is one ``claude -p``: the prompt on stdin, the normalized events on stdout, in its own
process group so ``kill`` and orphan recovery work as for any harness. What the agent does is its
role's rule in the project's script (``[agents] dummy_script``) or the built-in one
(``dummy_script.py``); its tools go through the same calls as the MCP server's
(``tooling.handle``), so the host, the runner, the task system and the web see a real agent. The
transcript is ``<sessions>/dummy/<session>.jsonl``, written by the agent in the common shape."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .base import (
    AgentSession,
    Harness,
    OnEvent,
    SessionSpec,
    TurnResult,
    _Deadlines,
    _LineReader,
    _NormalizerBase,
    records,
)


class DummySession(AgentSession):
    def __init__(self, spec: SessionSpec):
        self.spec = spec
        self._dir = spec.session_dir / "dummy"
        self._lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self._closed = False
        self.session_file = None

    @property
    def harness_name(self) -> str:
        return "dummy"

    def open(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self.session_file = str(self._dir / f"{self.spec.session_id}.jsonl")

    @property
    def pid(self) -> int | None:
        p = self.proc
        return p.pid if p is not None and p.poll() is None else None

    def alive(self) -> bool:
        return not self._closed

    def prompt(self, text: str, timeout: float | None, on_event: OnEvent | None = None,
               idle_timeout: float | None = None) -> TurnResult:
        with self._lock:
            if self.session_file is None:
                self.open()
            result, emit = TurnResult(), _NormalizerBase(on_event)
            self.spec.log_path.parent.mkdir(parents=True, exist_ok=True)
            stderr = self.spec.log_path.open("a", encoding="utf-8")
            env = {**os.environ, **self.spec.env, "ORCHESTRA_TOOLS": ",".join(self.spec.tool_groups)}
            try:
                self.proc = subprocess.Popen([sys.executable, "-m", "orchestra_agents.harness.dummy_agent", self.session_file],
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True,
                                             cwd=str(self.spec.cwd), env=env, bufsize=1, start_new_session=True)
            except OSError as e:
                stderr.close()
                result.error = f"cannot start the dummy agent: {e}"
                return result
            try:
                try:
                    self.proc.stdin.write(text)  # type: ignore[union-attr]
                    self.proc.stdin.close()      # type: ignore[union-attr]
                except (BrokenPipeError, OSError):
                    pass
                clock, reader, last = _Deadlines(timeout, idle_timeout), _LineReader(self.proc.stdout), ""
                while True:
                    why = clock.expired()
                    if why:
                        self.kill()
                        result.timed_out, result.stopped_by, result.text = True, why, last
                        return result
                    line = reader.next(clock.wait())
                    if line is None:
                        break
                    if not line:
                        continue
                    clock.touch()
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "error":
                        result.error = str(ev.get("error") or "the dummy agent failed")
                    elif ev.get("type") == "settled":
                        last = ev.get("text") or last
                    else:
                        if ev.get("type") == "assistant_end" and (ev.get("text") or "").strip():
                            last = ev["text"]
                        emit._emit(ev)
                rc = self.proc.wait()
                if rc != 0 and not result.error:
                    result.error = f"the dummy agent exited with status {rc}"
                result.text = last
                if not result.error:
                    emit.settled(last)
                return result
            finally:
                self.proc = None
                stderr.close()

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
        """The transcript moves under ``dummy/archive/``; the next turn starts a new one."""
        if not self.session_file or not Path(self.session_file).exists():
            return None
        f = Path(self.session_file)
        dest = f.parent / "archive" / f"{f.stem}-{int(time.time() * 1000)}.jsonl"
        dest.parent.mkdir(parents=True, exist_ok=True)
        f.rename(dest)
        return str(dest)


class DummyHarness(Harness):
    name = "dummy"

    def start(self, spec: SessionSpec) -> DummySession:
        return DummySession(spec)

    def model_for(self, wanted: str | None) -> str | None:
        """No model runs: the name is recorded as asked, so a spec's model shows as it would run."""
        return (wanted or "").strip() or None

    @staticmethod
    def read_transcript(session_file: str | Path) -> list[dict]:
        """The agent writes the common shape already: {role, text, tool_calls, ts, ...}."""
        return [r for r in records(session_file) if r.get("role") in ("user", "assistant", "toolResult")]
