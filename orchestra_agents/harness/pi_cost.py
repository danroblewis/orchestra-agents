"""What a Pi turn cost, from Pi's own usage records at the provider's prices.

A live run (2026-11-04, the Supervisor on Pi with ``claude-sonnet-5`` in bash mode) recorded $0.00
for Pi while its session files said $2.77 over four steps: Pi's ``usage.cost.total`` can be 0 (a
login that reports no price), and the calls that never become a message (a compaction's summary, a
branch summary) are only in the session file. So a turn's cost is read from the session file: every
entry with a ``usage`` not counted before (an assistant ``message``, a ``compaction``, a
``branch_summary``, a ``usage`` entry; each once, by its file and id), each at the prices below for its
model (per million tokens: input, output, cache read, cache write), or Pi's own figure when that is
higher. A model not in ``PRICES`` cannot be priced: the turn's cost is unknown (None), and the
dollar caps refuse to start a turn on it and say so (``budget.unpriced``)."""

from __future__ import annotations

import json
from pathlib import Path

# $ per million tokens: input, output, cache read, cache write (five minutes). Anthropic's list prices.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.50),
    "claude-sonnet-5": (2.0, 10.0, 0.20, 2.50),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75),
    "claude-sonnet-4-5": (3.0, 15.0, 0.30, 3.75),
}
KINDS = ("input", "output", "cacheRead", "cacheWrite")


def model_id(model: str | None) -> str:
    """``anthropic/claude-sonnet-5:high`` -> ``claude-sonnet-5``."""
    return (model or "").strip().split("/")[-1].split(":")[0]


def price(model: str | None) -> tuple[float, float, float, float] | None:
    m = model_id(model)
    for k in sorted(PRICES, key=len, reverse=True):          # the longest id first: sonnet-5-5 before sonnet-5
        if m == k or m.startswith(k + "-2"):                 # or a dated snapshot of it
            return PRICES[k]
    return None


def usage_cost(usage: dict, model: str | None) -> float | None:
    """One usage record's dollars: its tokens at the model's prices, or Pi's own figure when higher;
    None when the model is not priced here."""
    p = price(model)
    if p is None:
        return None
    ours = sum(float(usage.get(k) or 0) * r for k, r in zip(KINDS, p, strict=True)) / 1e6
    cost = usage.get("cost")
    theirs = float(cost.get("total") or 0) if isinstance(cost, dict) and isinstance(cost.get("total"), int | float) else 0.0
    return round(max(ours, theirs), 6)


class Meter:
    """The entries of a session's files already counted, so each turn adds only what is new."""

    def __init__(self, model: str | None):
        self.model = model
        self.seen: set[tuple[str, str]] = set()

    def entries(self, session_file: str | Path | None) -> list[tuple[dict, str | None]]:
        """The usage records not counted yet, with each one's model; marks them counted."""
        if not session_file:
            return []
        f = Path(session_file)
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        out = []
        for ln in lines:
            if '"usage"' not in ln:
                continue
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            msg = e.get("message") if isinstance(e.get("message"), dict) else {}
            usage = msg.get("usage") if e.get("type") == "message" else e.get("usage")
            if not isinstance(usage, dict) or (key := (f.name, str(e.get("id") or ln))) in self.seen:
                continue
            self.seen.add(key)
            out.append((usage, msg.get("model") or e.get("model") or self.model))
        return out

    def turn(self, session_file: str | Path | None) -> tuple[float | None, int]:
        """The dollars of what is new in the session file, and how many records; None for the dollars
        when any record's model is not priced here."""
        total, n, unknown = 0.0, 0, False
        for usage, model in self.entries(session_file):
            n += 1
            c = usage_cost(usage, model)
            if c is None:
                unknown = True
            else:
                total += c
        return (None if unknown or not n else round(total, 6)), n
