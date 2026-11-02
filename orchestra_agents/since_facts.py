"""What ``read_events`` with ``since`` adds beside the tally and the model line, so "what changed
since" has every fact in its one answer (T2e s16: the Outbox was said to need starting while it ran
the whole time, and the prediction's state was left out):

- ``outbox``: the Outbox as its state API has it (``outbox_read.fetch``): "running; N sent, M
  waiting", or "not installed", or what the read said when it did not answer;
- ``predictions``: each prediction from the core's predictions API (``modeling.reads``): its id,
  and what it waits for or its verdict.

Neither raises: a read that fails is said so, never guessed."""

from __future__ import annotations

from . import outbox_read
from .config import Project
from .store import StoreError

DECIDED = 50                # the outbox's state lists this many decided drafts


def outbox(project: Project, start: float) -> str:
    try:
        st = outbox_read.fetch(project)
    except StoreError as e:
        return "not installed" if "not installed" in str(e) else f"not read: {e}"
    if not st.get("ok", True):
        return f"not read: {st.get('error') or 'the Outbox state did not answer'}"
    recent = st.get("recent") or []
    sent = sum(1 for d in recent if d.get("state") == "sent" and (d.get("sent_at") or 0) >= start)
    capped = f" (of the latest {len(recent)} decided)" if len(recent) >= DECIDED else ""
    return (f"{'running' if st.get('running') else 'not running'}; {sent} sent since then{capped}, "
            f"{len(st.get('waiting') or [])} waiting")


def _state(p: dict) -> str:
    if p.get("status") == "invalid":
        return f"invalid: {p.get('why')}"
    if v := p.get("verdict"):
        return f"judged: {v.get('verdict')}"
    h = p.get("horizon_s")
    judged = f", judged {round(h / 3600)} h after it begins" if isinstance(h, (int, float)) else ""
    return (f"waiting: its era {p['era']} has begun{judged}" if p.get("era")
            else f"waiting for its change to begin an era{judged}")


def predictions(project: Project) -> list[dict] | str:
    try:
        from .modeling import reads
        got = reads(project).predictions()["predictions"]
    except Exception as e:  # noqa: BLE001 - a failed read is said so, never as no predictions
        return f"not read ({getattr(e, 'status', None) or type(e).__name__})"
    return [{"id": p.get("id"), "by": p.get("source"), "about": p.get("subject"), "state": _state(p)} for p in got]
