"""A session's transcript as turns, for a reader: where each turn starts and ends in the messages
(``read_transcript``), what started it (an assignment, a review request, a wake-up, the human's
message), what it ended with (its last words, the report or verdict it gave) and whether it is
about a ticket. The ticket drawer's agent sessions (``ticket_sessions``) and the Supervisor's
unread messages (attention.py) read these; nothing here writes."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .harness import read_transcript

# A user message that goes on the turn before it instead of starting one: the host's nudge after
# a turn that ended without its report (runner.py) and the Supervisor's promise follow-up (promises.py).
CONTINUES = ("You stopped without", "[Orchestra: your reply says")
OUTCOME_TOOLS = {"report_ticket": "state", "report_review": "verdict"}    # a per-ticket turn's exit tools and what they decide
STOPS = {"session.timed_out": "cut off", "session.error": "failed", "session.interrupted": "interrupted"}
HUMAN = "[The human's message]\n"
WOKEN = "## Why you were woken"
_cache: dict[str, tuple[tuple[float, int], list[dict]]] = {}


def epoch(ts) -> float:
    """A message's or marker's time in epoch seconds: harnesses write ISO strings, epoch seconds or millis."""
    if isinstance(ts, (int, float)):
        return ts / 1000 if ts > 1e12 else float(ts)
    if isinstance(ts, str) and ts:
        try:
            return datetime.fromisoformat(ts).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def messages(session: dict) -> list[dict]:
    """The session's transcript, parsed once per change of its file."""
    f = session.get("session_file")
    if not f or not Path(f).exists():
        return []
    st = Path(f).stat()
    key, stamp = f"{session.get('harness')}:{f}", (st.st_mtime, st.st_size)
    hit = _cache.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    msgs = read_transcript(session)
    _cache[key] = (stamp, msgs)
    return msgs


def kind_of(prompt: str) -> str:
    """What started a turn, from its first message."""
    if prompt.startswith("# Your assignment"):
        return "assignment"
    if prompt.startswith("# Review request"):
        return "review"
    if "# Wake-up for " in prompt:
        return "wake"
    if HUMAN in prompt or not prompt.startswith("[Orchestra:"):
        return "chat"
    return "notice"


def split(msgs: list[dict]) -> list[dict]:
    """The turns of a transcript: each starts at a prompt (a user message that is not a nudge on
    the turn before) and runs to the next. ``start``/``end`` index the messages (end exclusive);
    ``reply`` is the turn's last words, the last assistant text in it."""
    turns: list[dict] = []
    for i, m in enumerate(msgs):
        if m["role"] == "user" and not (turns and m["text"].startswith(CONTINUES)):
            turns.append({"start": i, "end": i + 1, "kind": kind_of(m["text"]), "prompt": m["text"]})
        elif turns:
            turns[-1]["end"] = i + 1
        else:                                           # a transcript that begins mid-turn (a compacted one)
            turns.append({"start": i, "end": i + 1, "kind": "notice", "prompt": ""})
    for t in turns:
        part = msgs[t["start"]:t["end"]]
        t["started"], t["ended"] = epoch(part[0].get("ts")), epoch(part[-1].get("ts"))
        texts = [(j, m["text"].strip()) for j, m in enumerate(part, t["start"]) if m["role"] == "assistant" and m["text"].strip()]
        t["reply"], t["reply_index"] = (texts[-1][1], texts[-1][0]) if texts else ("", None)
        t["tool_calls"] = [c for m in part if m["role"] == "assistant" for c in m.get("tool_calls") or []]
    return turns


def outcome(turn: dict, markers: list[dict], running: bool) -> str:
    """What a turn came to: the report or verdict it gave, the host's stop, or that it is still running."""
    for c in reversed(turn["tool_calls"]):
        field = OUTCOME_TOOLS.get(c.get("name"))
        if field and isinstance(c.get("args"), dict) and c["args"].get(field):
            return str(c["args"][field])
    if running:
        return "running"
    for m in markers:
        if m.get("type") in STOPS and turn["started"] - 1 <= epoch(m.get("ts")) <= (turn["ended"] or turn["started"]) + 5:
            return STOPS[m["type"]]
    return {"assignment": "no report", "review": "no verdict"}.get(turn["kind"], "replied" if turn["reply"] else "no reply")


def _mentions(ticket: str):
    return re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(ticket)}(?![A-Za-z0-9_])")


def about(turn: dict, msgs: list[dict], ticket: str) -> bool:
    """Whether a resident's turn is about the ticket: why it was woken, the human's words, what it
    wrote, or a tool it called names the ticket. The project snapshot every prompt carries lists
    every ticket, so the rest of the prompt does not count."""
    rx = _mentions(ticket)
    p = turn["prompt"]
    asked = p.split(WOKEN, 1)[1].split("\n## ", 1)[0] if WOKEN in p else p.split(HUMAN, 1)[1] if HUMAN in p else "" if p.startswith("[Orchestra:") else p
    if rx.search(asked):
        return True
    part = msgs[turn["start"]:turn["end"]]
    return any(m["role"] == "assistant" and (rx.search(m["text"]) or any(rx.search(json.dumps(c.get("args"), default=str)) for c in m.get("tool_calls") or []))
               for m in part)


def touched(turn: dict, ticket: str) -> list[str]:
    """The tools a turn called with the ticket in their arguments."""
    rx = _mentions(ticket)
    return list(dict.fromkeys(c.get("name") for c in turn["tool_calls"] if rx.search(json.dumps(c.get("args"), default=str))))


def current_turn(session: dict, turns: list[dict]) -> int | None:
    """The index of the turn that is running now: the last one, when the session is mid-turn and
    the turn's prompt is in the transcript already (it began when the session's turn did)."""
    if session.get("status") != "running" or not turns:
        return None
    last = turns[-1]
    return len(turns) - 1 if not last["started"] or last["started"] >= (session.get("started_at") or 0) - 2 else None


def ticket_sessions(store, ticket: str) -> list[dict]:
    """Every agent turn that worked on the ticket, oldest first: each worker turn and review round
    (the sessions the host ran for the ticket, live or archived), and the turns of the other
    agents (the Coordinator, the Supervisor, a custom agent) that were about it. Each says its
    session, role, instance, when, what started it, its outcome and last words, and where its
    messages are in the session's transcript (``start``/``end``)."""
    rt = store.runtime()
    out = []
    for s in [*rt["sessions"].values(), *rt.get("archived_sessions", {}).values()]:
        own = s.get("ticket_id") == ticket
        if s.get("ticket_id") and not own:
            continue
        msgs = messages(s)
        turns = split(msgs)
        live = current_turn(s, turns)
        for n, t in enumerate(turns):
            if not own and not about(t, msgs, ticket):
                continue
            running = n == live
            out.append({"session": s["id"], "role": s.get("kind") or "", "instance": s.get("instance") or s.get("kind") or "",
                        "harness": s.get("harness"), "turn": n + 1, "turns": len(turns), "kind": t["kind"],
                        "own": own, "started": t["started"], "ended": None if running else t["ended"], "running": running,
                        "outcome": outcome(t, s.get("markers") or [], running), "reply": t["reply"][-600:],
                        "touched": [] if own else touched(t, ticket), "start": t["start"], "end": t["end"],
                        "archived": s.get("status") == "archived"})
        if own and s.get("status") == "running" and live is None:     # started, no message of this turn in its transcript yet
            out.append({"session": s["id"], "role": s.get("kind") or "", "instance": s.get("instance") or "", "harness": s.get("harness"),
                        "turn": 1, "turns": 1, "kind": "assignment", "own": True, "started": s.get("started_at") or 0, "ended": None,
                        "running": True, "outcome": "running", "reply": (s.get("partial_text") or "")[-600:], "touched": [],
                        "start": 0, "end": 0, "archived": False})
    out.sort(key=lambda x: (x["started"] or 0, x["session"]))
    return out
