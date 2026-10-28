"""``read_transcript`` (group ``observe``): an agent's transcript as a reader needs it to find a
blockage, the turns around a time, trimmed (docs/proposals/agent-first-machines.md §5.3).

The transcript is the harness's own file (AGENTS.md rule 6), read through ``session_turns`` like
the ticket drawer reads it; nothing here writes, and nothing knows which harness wrote it."""

from __future__ import annotations

from datetime import datetime

from .config import Project
from .session_turns import epoch, messages, split
from .store import AgentStore, StoreError

TEXT_MAX = 1500          # characters kept of one message
TURNS_MAX = 6


def _when(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(v)).timestamp()
    except ValueError:
        raise StoreError(f"around: {v!r} is neither unix seconds nor an ISO time") from None


def _cut(text: str) -> str:
    text = text or ""
    return text if len(text) <= TEXT_MAX else text[:TEXT_MAX // 2] + f"\n… ({len(text) - TEXT_MAX} characters left out) …\n" + text[-TEXT_MAX // 2:]


def _session(store: AgentStore, a: dict) -> dict:
    sid, agent = (a.get("session") or "").strip(), (a.get("agent") or "").strip()
    if not sid and not agent:
        raise StoreError("name an agent (supervisor, worker, ...) or a session id")
    every = store.sessions(include_archived=True)
    if sid:
        s = next((x for x in every if x.get("id") == sid), None)
        if s is None:
            raise StoreError(f"no session {sid!r}")
        return s
    mine = [x for x in every if agent in (x.get("kind"), x.get("instance"), (x.get("instance") or "").split("/")[0])
            and x.get("session_file")]
    if not mine:
        raise StoreError(f"no session of {agent!r} with a transcript")
    return max(mine, key=lambda x: x.get("started_at") or x.get("settled_at") or 0)


def read_transcript(project: Project, store: AgentStore, a: dict) -> dict:
    s = _session(store, a)
    msgs = messages(s)
    if not msgs:
        return {"ok": True, "session": s.get("id"), "turns": [], "note": "the transcript is empty or its file is gone"}
    turns = split(msgs)
    n = max(1, min(int(a.get("turns") or 2), TURNS_MAX))
    around = _when(a.get("around"))
    if around is None:
        chosen = turns[-n:]
    else:
        nearest = min(range(len(turns)), key=lambda i: abs((turns[i]["started"] or 0) - around))
        lo = max(0, nearest - (n - 1) // 2)
        chosen = turns[lo:lo + n]
    out = []
    for t in chosen:
        part = msgs[t["start"]:t["end"]]
        out.append({"started": t["started"], "kind": t["kind"], "prompt": _cut(t["prompt"]),
                    "steps": [{"role": m["role"], "at": epoch(m.get("ts")) or None, "text": _cut(m.get("text") or ""),
                               **({"tool_calls": [{"name": c.get("name"), "args": _cut(str(c.get("args")))[:400]}
                                                  for c in m.get("tool_calls") or []]} if m.get("tool_calls") else {})}
                              for m in part[1:]],
                    "reply": _cut(t["reply"])})
    markers = [m for m in s.get("markers") or [] if chosen and epoch(m.get("ts")) >= (chosen[0]["started"] or 0) - 1]
    return {"ok": True, "session": s.get("id"), "agent": s.get("instance") or s.get("kind"), "harness": s.get("harness"),
            "turns_in_session": len(turns), "turns": out,
            "host_markers": [{"type": m.get("type"), "at": m.get("ts"), "reason": m.get("reason") or m.get("error")} for m in markers][-10:]}
