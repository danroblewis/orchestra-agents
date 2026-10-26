"""Agent harnesses: the products that run the agents (SPEC.md section 6).

``get_harness(name)`` returns the configured one. ``read_transcript(session)`` parses a
session record's transcript with the harness that wrote it, so the UI and CLI never
care which product an agent ran on."""

from __future__ import annotations

from pathlib import Path

from .base import AgentSession, Harness, SessionSpec, TurnResult
from .claude_code import ClaudeCodeHarness
from .codex import CodexHarness
from .pi import PiHarness

HARNESSES: dict[str, type[Harness]] = {
    PiHarness.name: PiHarness,
    ClaudeCodeHarness.name: ClaudeCodeHarness,
    CodexHarness.name: CodexHarness,
}
DEFAULT_HARNESS = PiHarness.name


def get_harness(name: str | None) -> Harness:
    key = (name or DEFAULT_HARNESS).replace("-", "_")
    if key not in HARNESSES:
        raise ValueError(f"unknown harness {name!r}; known: {sorted(HARNESSES)}")
    return HARNESSES[key]()


def read_transcript(session: dict | None, session_file: str | Path | None = None) -> list[dict]:
    """Messages of a session record (``{"harness": ..., "session_file": ...}``); none for a
    record without a file, or one written by a harness this package does not know."""
    harness = (session or {}).get("harness") or DEFAULT_HARNESS
    f = session_file or (session or {}).get("session_file")
    cls = HARNESSES.get(str(harness).replace("-", "_"))
    if not f or cls is None:
        return []
    return cls.read_transcript(f)


__all__ = [
    "DEFAULT_HARNESS",
    "HARNESSES",
    "AgentSession",
    "Harness",
    "SessionSpec",
    "TurnResult",
    "get_harness",
    "read_transcript",
]
