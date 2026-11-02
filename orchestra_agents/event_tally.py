"""What changed in a window, counted (``read_events`` with ``since``: its ``tally``), so the
Supervisor copies numbers instead of counting events itself: the T2c walkthrough's answer to "what
changed since yesterday?" said "two were merged" of three pull requests, each merged in its own list.

Each class counts over every event in the window, not only those shown: tickets opened and closed,
pull requests opened and merged (each once, by number), checks by outcome, labels by name (SAIL,
REJECTED, SAILED, with the pull requests), triage verdicts (a step named for triage, finished with
its outcome), incidents opened and resolved, the Outbox's drafts, approvals (by configuration or by
the owner), sends, rejections and failures, items started (and how many by the owner) and closed,
and the model's changes. A class with nothing in it is left out. These are other machines' events,
read as this machine (a reader of the bus) reads them: a field is the event's ``data`` or, in the
bus's flat shape, the event itself."""

from __future__ import annotations

DONE = ("done", "closed", "resolved")
NOTE = ("tally counts every event in the window (not only the events shown), each pull request and ticket once: copy "
        "its numbers and names; never count the events yourself")


def field(e: dict, k: str):
    d = e.get("data")
    return d.get(k) if isinstance(d, dict) and k in d else e.get(k)


def _pr(e: dict) -> str:
    n, subject = field(e, "number"), e.get("subject")
    return f"#{n}" + (f" ({subject})" if subject and str(subject) != str(n) else "") if n is not None else str(subject or "?")


class Tally:
    def __init__(self):
        self.out: dict[str, dict] = {}

    def add(self, cls: str, what: str, who: str | None = None):
        row = self.out.setdefault(cls, {}).setdefault(what, {"count": 0, "which": []})
        if who is None:
            row["count"] += 1
        elif who not in row["which"]:
            row["which"].append(who)
            row["count"] += 1

    def result(self) -> dict:
        return {c: {w: (r if r["which"] else {"count": r["count"]}) for w, r in rows.items()} for c, rows in self.out.items()}


def _step(e: dict) -> str | None:
    """The step an offer was for: ``<item>.<step>.<id>`` (the Workflows floor's offer ids)."""
    offer, item = str(field(e, "offer") or ""), str(e.get("subject") or "")
    if item and offer.startswith(item + "."):
        return offer[len(item) + 1:].rsplit(".", 1)[0] or None
    return None


def tally(events: list[dict]) -> dict:
    t = Tally()
    for e in events:
        ty, src, subject = str(e.get("type") or ""), str(e.get("source") or ""), e.get("subject")
        fam, _, verb = ty.rpartition(".")
        if fam.endswith(".issue") and verb == "created":
            t.add("tickets", "opened", str(subject))
        elif fam.endswith(".issue") and (verb in ("closed", "resolved") or
                                         (verb == "transitioned" and str(field(e, "to") or "").lower() in DONE)):
            t.add("tickets", "closed", str(subject))
        elif fam.endswith(".pr") and verb == "opened":
            t.add("pull_requests", "opened", _pr(e))
        elif fam.endswith(".pr") and verb == "merged":
            t.add("pull_requests", "merged", _pr(e))
        elif fam.endswith(".pr") and verb in ("labeled", "unlabeled") and field(e, "label"):
            t.add("labels" if verb == "labeled" else "labels_removed", str(field(e, "label")), _pr(e))
        elif ty.endswith(("checks.completed", "check.completed")):
            t.add("checks", f"{fam} {field(e, 'outcome') or field(e, 'conclusion') or '?'}")
        elif fam.endswith(".incident") and verb == "triggered":
            t.add("incidents", "opened", str(subject))
        elif fam.endswith(".incident") and verb == "resolved":
            t.add("incidents", "resolved", str(subject))
        elif ty == "outbox.draft.created":
            t.add("outbox", "drafted")
        elif ty == "outbox.draft.approved":
            by = field(e, "by")
            t.add("outbox", "approved by configuration" if by == "policy" else
                  "approved by the owner" if src in ("owner", "person.owner") or by == "owner" else "approved")
        elif ty == "outbox.draft.rejected":
            t.add("outbox", "rejected")
        elif ty == "outbox.sent":
            t.add("outbox", "sent")
        elif ty == "outbox.send_failed":
            t.add("outbox", "failed")
        elif ty == "offer.finished" and (step := _step(e)) and "triage" in step:
            t.add("triage", str(field(e, "outcome") or "?"), str(subject))
        elif ty == "item.requested":
            t.add("items", "started", str(subject))
            if src == "owner":
                t.add("items", "started by the owner", str(subject))
        elif ty == "item.closed":
            t.add("items", "closed", str(subject))
        elif ty == "orchestra.model.changed":
            t.add("model", "changed")
    return t.result()
