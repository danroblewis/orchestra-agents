"""Bot threads in a chat queue (T44, bootstrap run 8's finding 5): the Supervisor's Slack queue left
out every thread a bot started (``"bot": {"not": [true]}``), so the alert bot's threads in
#hackriff-incidents, which are the incidents, were never items. The owner never asked for that.

- **Leaving bot threads out is the owner's choice.** A start on a chat thread (``*.thread.started``)
  that refuses a bot's (``bot: {"not": [true]}`` or ``bot: false``) is refused unless the owner's
  quoted words speak of bots; the refusal says to count them, and where the owner named a channel
  for incidents or alerts, to start the bot's threads there as alerts, under the owner's policy for
  that channel.
- **The answer says which channels count bot threads**, start by start, so the Supervisor tells
  the owner which and why (``bot_threads``)."""

from __future__ import annotations

import re

from .store import StoreError

THREADS = re.compile(r"^[a-z]+\.thread\.started$")
BOTS = re.compile(r"\b(bots?|automated|integrations?)\b", re.IGNORECASE)
CHANNEL = ("channel", "channel_name")


def _bot(data: dict) -> str:
    """How a start's data treats a bot's thread: ``left out``, ``only`` or ``counted``."""
    v = data.get("bot")
    if v is False or (isinstance(v, dict) and True in (v.get("not") or [])):
        return "left out"
    if v is True:
        return "only"
    return "counted"


def _where(data: dict) -> str:
    got = [f"{data[k]}" for k in CHANNEL if isinstance(data.get(k), str | int)]
    but = [str(x) for k in CHANNEL if isinstance(data.get(k), dict) for x in data[k].get("not") or []]
    if got:
        return " / ".join(got)
    return f"every channel but {', '.join(but)}" if but else "every channel"


def thread_starts(definition: dict) -> list[dict]:
    return [s for s in definition.get("starts") or []
            if isinstance(s, dict) and isinstance(s.get("event"), str) and THREADS.match(s["event"])]


def check(definition: dict, prov: dict, owner: str) -> None:
    """Refuse a start that leaves bot threads out when the owner's quoted words do not speak of bots."""
    starts = thread_starts(definition)
    out = [s for s in starts if _bot(s.get("data") or {}) == "left out"]
    if not out or all(any(_bot(o.get("data") or {}) != "left out" for o in starts if o["event"] == s["event"]) for s in out):
        return                          # another start of the same event takes the bots' threads
    if prov.get("author") == owner and BOTS.search(str(prov.get("quote") or "")):
        return
    where = ", ".join(_where(s.get("data") or {}) for s in out)
    raise StoreError(
        f"declare_workflow refused, nothing written: the start on {out[0]['event']} leaves out the threads a bot "
        f"starts ({where}), which the owner never asked for: their quoted words do not speak of bots. Count bot "
        "threads: where the owner named a channel for incidents or alerts (its name or their words: incidents, "
        "alerts, on-call, pages), the alert bot's threads are the incidents: start them there as their own kind, a "
        "workflow whose items are alert (a start on the same event with data {\"bot\": true, \"channel\": <that "
        "channel>}), under the response policy the owner gave that channel; and here, beside the people's start, a "
        "start with data {\"bot\": true, \"channel\": {\"not\": [<those channels>]}} (or drop the bot filter when "
        "no channel is for incidents). Leave bot threads out only with the owner's words about bots, quoted.")


def said(definition: dict) -> dict | None:
    """Which channels' bot threads start items of this workflow, start by start, for the answer."""
    starts = thread_starts(definition)
    if not starts:
        return None
    kind = definition.get("items")
    kind = (kind.get("one") if isinstance(kind, dict) else kind) or "item"
    lines = []
    for s in starts:
        data = s.get("data") or {}
        how, where = _bot(data), _where(data)
        lines.append({"left out": f"bot threads in {where} start no {kind}",
                      "only": f"bot threads in {where} start {kind}s (only a bot's)",
                      "counted": f"threads in {where}, a bot's too, start {kind}s"}[how])
    return {"starts": lines, "say": ("Tell the owner, in one line, which channels count bot threads (the alert bot's "
                                     "in an incidents or alerts channel) and why: their words naming that channel "
                                     "for incidents or alerts, or their words leaving bots out")}
