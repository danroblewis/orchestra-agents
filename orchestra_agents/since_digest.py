"""The whole answer to "what changed since <when>", written by code (``read_events`` with
``since``: its ``digest``), so the Supervisor replies with it as it is. Seven walkthroughs gave a
small model every fact in one tool answer, and each time it dropped some and misstated others (T2g
s16: "#33 and #34 opened at 04:57" for 04:16, "all three merged at 05:12" for one, "2 opened, 2
closed" for three tickets Done, the triage verdict and the prediction's state left out).

It leads with what looks wrong (``since_anomalies``: new work on a Done ticket, a label on a merged
pull request, attention over an hour old, an open item whose pull request closed unmerged), then
one paragraph per class, in this order: tickets, pull requests (with each rejection and triage
verdict), checks, labels, incidents, the Outbox, items, machines marked quiet, the other events
counted, what waits on the owner, the predictions, the machines by age (``machine_ages``: who is due to graduate),
and the model line last (``closing_line``).
Each list names at most 5 entries, then "… and K more", the count still the whole one; when the
digest would pass ``LIMIT`` (2,500 characters) the lists name 4, then 3, down to 1, so three days of
a sprint board read at a glance (R3: 57 hours of the owner's board, 8 tickets and 15 pull requests,
5,791 characters uncapped, about 2,500 capped at 1). The anomalies name at least ``LOOK`` per kind,
and what waits on the owner is never cut.
Every number is counted here and every time is the event's own, in one zone, named once in the
first line (the host's: the pages show the viewer's clock, and the owner's viewer is this host).

A ticket is known by the ticket system's events, and also by an Outbox send whose kind names a
ticket (``live.create_ticket``, ``live.close_ticket``) for an item whose facts name the ticket
(a ``ticket`` fact, its key the last part of the link): a ticket opened and closed through the
Outbox is counted once, at the earliest time either says. Nothing here reads beyond the events,
the facts ``since_facts`` and ``owner_waits`` read, and the model line."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .bus_reads import _ts
from .event_tally import DONE, field
from .since_anomalies import anomalies

CAPS = (5, 4, 3, 2, 1)       # entries named per list, the first that keeps the digest within LIMIT
LIMIT = 2500                 # characters: three days of the owner's sprint board at a glance
LOOK = 3                     # the anomalies name at least this many per kind, whatever the cap

OWNER = ("owner", "person.owner")
KEY = re.compile(r"[A-Z][A-Z0-9_]*-\d+")
OPENS, CLOSES = ("create", "open"), ("close", "done", "resolve")
TOLD = ("issue.created", "issue.assigned", "issue.transitioned", "issue.closed", "issue.resolved", "pr.opened",
        "pr.merged", "pr.labeled", "pr.unlabeled", "pr.commented", "pr.synchronize", "checks.completed",
        "check.completed", "incident.triggered", "incident.acknowledged", "incident.resolved", "outbox.draft.created",
        "outbox.draft.approved", "outbox.draft.rejected", "outbox.sent", "outbox.send_failed", "item.requested",
        "item.closed", "attention.needed", "orchestra.machine.quiet", "orchestra.model.changed")


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _times(n: int) -> str:
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


def _cap(xs: list, n: int) -> list:
    return xs if len(xs) <= n else [*xs[:n], f"… and {len(xs) - n} more"]


def _n(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


class Clock:
    def __init__(self, tz: str | None, now: float):
        self.zone = ZoneInfo(tz) if tz else None
        self.tz, self.now = tz, now
        self.today = self.local(now).date()

    def local(self, ts: float) -> datetime:
        return datetime.fromtimestamp(ts, self.zone) if self.zone else datetime.fromtimestamp(ts).astimezone()

    def at(self, ts: float) -> str:
        d = self.local(ts)
        return f"{d:%H:%M}" if d.date() == self.today else f"{d:%a} {d.day} {d:%b} {d:%H:%M}"

    def named(self) -> str:
        abbr = self.local(self.now).tzname() or ""
        return f"{abbr}, {self.tz}" if self.tz and abbr != self.tz else (self.tz or abbr or "the host's zone")


class Day:
    """The window's events, sorted by their own time, with what the items' facts say."""

    def __init__(self, events: list[dict], clock: Clock, cap: int = CAPS[0]):
        self.clock, self.cap = clock, cap
        self.events = sorted(((t, e) for e in events if (t := _ts(e)) is not None), key=lambda x: x[0])
        self.ticket: dict[str, str] = {}             # item -> its ticket's key
        self.pr: dict[str, int] = {}                 # item -> its pull request's number
        for _, e in self.events:
            subject = str(e.get("subject") or "")
            for facts in (_set(field(e, "facts")), _set(e) if e.get("type") == "item.fact" else {}):
                if (key := _key(facts.get("ticket"))) and subject:
                    self.ticket[subject] = key
                if isinstance(facts.get("number"), int) and subject:
                    self.pr[subject] = facts["number"]
            if str(e.get("type") or "").endswith(".pr.opened") and isinstance(field(e, "number"), int) and subject:
                self.pr[subject] = field(e, "number")
        self.used: set[int] = set()

    def of(self, *suffixes: str):
        for t, e in self.events:
            if str(e.get("type") or "").endswith(suffixes):
                self.used.add(id(e))
                yield t, e

    def key_of(self, item: str) -> str | None:
        return self.ticket.get(item) or (item if KEY.fullmatch(item) else None)

    def item(self, item: str) -> str:
        key = self.ticket.get(item) if self.ticket.get(item) != item else None
        names = [k for k in (key, f"#{self.pr[item]}" if item in self.pr else None) if k]
        return f"{item} ({', '.join(names)})" if names else item

    def pull(self, e: dict) -> str:
        n, subject = field(e, "number"), str(e.get("subject") or "")
        key = self.key_of(subject) if subject else None
        return f"#{n}" + (f" ({key})" if key else f" ({subject})" if subject and subject != str(n) else "")

    def runs(self, pairs: list[tuple[str, float]]) -> str:
        """``A and B at 04:11, C at 04:57``: labels at one minute said together, in time order."""
        groups: list[tuple[str, list[str]]] = []
        more = len(pairs) - self.cap
        for label, ts in pairs[:self.cap]:
            t = self.clock.at(ts)
            if groups and groups[-1][0] == t:
                groups[-1][1].append(label)
            else:
                groups.append((t, [label]))
        return ", ".join(f"{_and(ls)} at {t}" for t, ls in groups) + (f", … and {more} more" if more > 0 else "")


def _set(v) -> dict:
    s = v.get("set") if isinstance(v, dict) else None
    return s if isinstance(s, dict) else {}


def _key(v) -> str | None:
    tail = str(v or "").rstrip("/").rsplit("/", 1)[-1]
    return tail if KEY.fullmatch(tail) else None


def _ticket_kind(kind: str) -> str | None:
    last = str(kind or "").rsplit(".", 1)[-1].lower()
    if "ticket" not in last:
        return None
    return "opened" if any(w in last for w in OPENS) else "closed" if any(w in last for w in CLOSES) else None


def tickets(d: Day) -> str | None:
    opened: dict[str, float] = {}
    closed: dict[str, float] = {}
    assigned: list[tuple[str, float]] = []
    via: dict[tuple[str, str], bool] = {}
    for t, e in d.of(".issue.created", ".issue.assigned", ".issue.transitioned", ".issue.closed", ".issue.resolved"):
        verb, key = str(e["type"]).rsplit(".", 1)[-1], str(e.get("subject") or "?")
        if verb == "created":
            opened.setdefault(key, t)
        elif verb == "assigned":
            assigned.append((key, t))
        elif verb in ("closed", "resolved") or str(field(e, "to") or "").lower() in DONE:
            closed.setdefault(key, t)
    for t, e in d.of("outbox.sent"):
        if (what := _ticket_kind(field(e, "kind"))) and (item := str(e.get("correlationid") or "")):
            key = d.key_of(item) or f"the ticket for {item}"
            book = opened if what == "opened" else closed
            if key not in book:
                book[key], via[(what, key)] = t, True
    if not (opened or closed or assigned):
        return None

    def told(book, what):
        pairs = sorted(((k + (" (through the Outbox)" if via.get((what, k)) else ""), t) for k, t in book.items()),
                       key=lambda x: x[1])
        return d.runs(pairs)
    parts = [f"{len(opened)} opened: {told(opened, 'opened')}" if opened else "none opened"]
    if assigned:
        parts.append(f"{len(assigned)} assigned: {d.runs(assigned)}")
    parts.append(f"{len(closed)} closed (Done): {told(closed, 'closed')}" if closed else "none closed")
    return "Tickets: " + "; ".join(parts) + "."


def pull_requests(d: Day) -> str | None:
    opened, merged, rejected, comments, pushed = [], [], [], [], []
    for t, e in d.of(".pr.opened", ".pr.merged", ".pr.labeled", ".pr.commented", ".pr.synchronize"):
        verb = str(e["type"]).rsplit(".", 1)[-1]
        if verb == "opened":
            opened.append((d.pull(e), t))
        elif verb == "merged":
            merged.append((d.pull(e), t))
        elif verb == "labeled" and str(field(e, "label")) == "REJECTED":
            rejected.append((d.pull(e), t))
        elif verb == "commented":
            comments.append((d.pull(e), t))
        elif verb == "synchronize":
            pushed.append((d.pull(e), t))
    triage = []
    for t, e in d.events:
        offer, item = str(field(e, "offer") or ""), str(e.get("subject") or "")
        if e.get("type") == "offer.finished" and item and offer.startswith(item + ".") and "triage" in offer[len(item):]:
            why = field(e, "summary")
            triage.append(f"{d.item(item)} at {d.clock.at(t)}: {field(e, 'outcome') or '?'}" + (f" ({why})" if why else ""))
    if not (opened or merged or rejected or comments or pushed or triage):
        return None
    parts = [f"{len(opened)} opened: {d.runs(opened)}" if opened else "none opened",
             f"{len(merged)} merged: {d.runs(merged)}" if merged else "none merged",
             f"{len(rejected)} rejected (labelled REJECTED): {d.runs(rejected)}" if rejected else "none rejected"]
    if triage:
        parts.append(f"{_n(len(triage), 'triage verdict')}: {'; '.join(_cap(triage, d.cap))}")
    if pushed:
        parts.append(f"new commits pushed: {d.runs(pushed)}")
    if comments:
        parts.append(f"{_n(len(comments), 'comment')}: {d.runs(comments)}")
    return "Pull requests: " + "; ".join(parts) + "."


WORDS = {"success": "succeeded", "failure": "failed"}


def checks(d: Day) -> str | None:
    rows: dict[str, list[tuple[str, str | None]]] = {}
    for t, e in d.of("checks.completed", "check.completed"):
        fam = str(e["type"]).rsplit(".", 1)[0]
        outcome = str(field(e, "outcome") or field(e, "conclusion") or "?")
        why = field(e, "reason") if outcome != "success" else None
        rows.setdefault(f"{fam} {WORDS.get(outcome, outcome)}", []).append((f"{d.pull(e)} at {d.clock.at(t)}", why))
    if not rows:
        return None

    def said(xs):            # one reason shared by every failure is said once, after the list
        whys = {w for _, w in xs}
        if len(whys) == 1 and len(xs) > 1:
            w = whys.pop()
            return ", ".join(_cap([x for x, _ in xs], d.cap)) + (f" (each: {w})" if w else "")
        return ", ".join(_cap([x + (f" ({w})" if w else "") for x, w in xs], d.cap))
    return "Checks: " + "; ".join(f"{what} {_times(len(xs))}: {said(xs)}" for what, xs in rows.items()) + "."


def labels(d: Day) -> str | None:
    put: dict[str, list[tuple[str, float]]] = {}
    removed: dict[str, int] = {}
    for t, e in d.of(".pr.labeled", ".pr.unlabeled"):
        name = str(field(e, "label") or "?")
        if str(e["type"]).endswith(".unlabeled"):
            removed[name] = removed.get(name, 0) + 1
        else:
            put.setdefault(name, []).append((d.pull(e), t))
    if not (put or removed):
        return None
    parts = [f"{name} put on {_times(len(xs))}: {d.runs(xs)}" for name, xs in put.items()]
    if removed:
        parts.append("removed: " + ", ".join(f"{name} {_times(n)}" for name, n in removed.items()))
    return "Labels: " + "; ".join(parts) + "."


def incidents(d: Day) -> str | None:
    seen: dict[str, dict] = {}
    for t, e in d.of(".incident.triggered", ".incident.acknowledged", ".incident.resolved"):
        row = seen.setdefault(str(e.get("subject") or "?"), {"what": [], "service": field(e, "service"),
                                                            "urgency": field(e, "urgency"), "resolved": False})
        verb = str(e["type"]).rsplit(".", 1)[-1]
        row["what"].append(f"{'opened' if verb == 'triggered' else verb} at {d.clock.at(t)}")
        row["resolved"] = row["resolved"] or verb == "resolved"
    if not seen:
        return None
    out = []
    for inc, row in seen.items():
        about = ", ".join(str(x) for x in (row["service"], row["urgency"]) if x)
        out.append(f"{inc}" + (f" ({about})" if about else "") + " " + ", ".join(row["what"]) +
                   ("" if row["resolved"] else ", not resolved in this window"))
    opened = sum(1 for r in seen.values() if any(w.startswith("opened") for w in r["what"]))
    done = sum(1 for r in seen.values() if r["resolved"])
    return f"Incidents: {opened} opened, {done} resolved: " + "; ".join(_cap(out, d.cap)) + "."


def outbox(d: Day, state: str | None) -> str | None:
    kind: dict[str, str] = {}
    sent: dict[str, list[str]] = {}
    by_owner, by_config, rejected, failed = [], 0, [], []
    for t, e in d.of("outbox.draft.created", "outbox.draft.approved", "outbox.draft.rejected", "outbox.sent",
                     "outbox.send_failed"):
        draft, ty = str(field(e, "draft") or e.get("subject") or "?"), str(e["type"])
        k = kind.setdefault(draft, str(field(e, "kind") or "?"))
        if ty == "outbox.draft.approved":
            if field(e, "by") == "policy":
                by_config += 1
            else:
                by_owner.append(f"{k} at {d.clock.at(t)}")
        elif ty == "outbox.draft.rejected":
            rejected.append(f"{k} at {d.clock.at(t)}")
        elif ty == "outbox.sent":
            sent.setdefault(k, []).append(d.clock.at(t))
        elif ty == "outbox.send_failed":
            failed.append(f"{k} at {d.clock.at(t)}")
    if not (kind or state):
        return None
    n = sum(len(x) for x in sent.values())
    parts = [f"{n} sent" + (": " + "; ".join(f"{k} {_times(len(ts))} ({', '.join(_cap(ts, d.cap))})" for k, ts in sent.items()) if n else "")]
    if by_config or by_owner:
        parts.append(f"{by_config} approved by configuration, {len(by_owner)} by you" +
                     (f" ({', '.join(_cap(by_owner, d.cap))})" if by_owner else ""))
    parts.append(f"{len(rejected)} rejected" + (f" ({', '.join(_cap(rejected, d.cap))})" if rejected else ""))
    parts.append(f"{len(failed)} failed to send" + (f" ({', '.join(_cap(failed, d.cap))})" if failed else ""))
    return "The Outbox: " + "; ".join(parts) + "." + (f" Its state now: {state}." if state else "")


def items(d: Day) -> str | None:
    started, closed, attention = [], [], []
    for t, e in d.of("item.requested", "item.closed", "attention.needed"):
        item, ty = str(e.get("subject") or "?"), e["type"]
        if ty == "item.requested":
            by = ("by you" if e.get("source") in OWNER else "on its schedule" if field(e, "every")
                  else f"by {e.get('source')}")
            started.append(f"{d.item(item)} {by} at {d.clock.at(t)}")
        elif ty == "item.closed":
            how = ": ".join(str(x) for x in (field(e, "last_step"), field(e, "outcome")) if x)
            closed.append(f"{d.item(item)} at {d.clock.at(t)}" + (f" ({how})" if how else ""))
        else:
            why = field(e, "reason")
            attention.append(f"{d.item(item)} at {d.clock.at(t)}" + (f" ({why})" if why else ""))
    if not (started or closed or attention):
        return None
    parts = [f"{len(started)} started: {'; '.join(_cap(started, d.cap))}" if started else "none started",
             f"{len(closed)} closed: {'; '.join(_cap(closed, d.cap))}" if closed else "none closed"]
    if attention:
        parts.append(f"attention raised {_times(len(attention))}: {'; '.join(_cap(attention, d.cap))}")
    return "Items: " + "; ".join(parts) + "."


def quiet(d: Day) -> str | None:
    who: dict[str, list[tuple[str, float]]] = {}
    for t, e in d.of("orchestra.machine.quiet"):
        name = str(field(e, "instance") or field(e, "machine") or e.get("subject") or "?")
        who.setdefault(name, []).append((name, t))
    if not who:
        return None
    said = [f"{name} at {', '.join(_cap([d.clock.at(t) for _, t in xs], d.cap))}" for name, xs in who.items()]
    return ("Marked quiet (not heard for a while; not that it stopped): " + "; ".join(_cap(said, d.cap)) + ".")


def others(d: Day) -> str | None:
    rest: dict[str, int] = {}
    for _, e in d.events:
        ty = str(e.get("type") or "?")
        if id(e) not in d.used and not ty.endswith(TOLD) and not (ty == "offer.finished" and "triage" in str(field(e, "offer"))):
            rest[ty] = rest.get(ty, 0) + 1
    if not rest:
        return None
    counted = [f"{ty} {_times(n)}" for ty, n in sorted(rest.items(), key=lambda x: (-x[1], x[0]))]
    return "Also on the bus: " + ", ".join(_cap(counted, d.cap)) + "."


def owner(w: dict | None, clock: Clock) -> str:
    if not isinstance(w, dict):
        return "Waiting on you: not read."
    parts, nothing = [], True
    m = w.get("machines")
    if isinstance(m, str):
        parts.append(f"the machines were {m}")
    elif m:
        nothing = False
        parts += [f"{x.split(': ', 1)[0]} is disabled" + (f" ({x.split(': ', 1)[1]})" if ": " in x else "") for x in m]
    o = w.get("outbox")
    if isinstance(o, str):
        parts.append(f"the Outbox was {o}")
    elif o:
        nothing = False
        parts += [f"Outbox draft {r.get('id')} ({r.get('kind')}) waits for your approval on the Outbox page" for r in o]
    c = w.get("changes")
    if isinstance(c, str):
        parts.append(f"the declared machines were {c}")
    elif c:
        nothing = False
        parts += [f"{r['machine']}'s change {r['change']} ({r['what']}) waits for your confirm on the Machines page" for r in c]
    q = w.get("questions")
    if isinstance(q, str):
        parts.append(f"my questions were {q}")
    elif q:
        nothing = False
        for a in q:
            said = a.get("owner_said_next")
            when = clock.at(a["asked_at"]) if isinstance(a.get("asked_at"), int | float) else "?"
            asked = a.get("question") or ""
            if a.get("choices"):
                asked += " " + " ".join(f"{n}. {c}" for n, c in enumerate(a["choices"], 1))
            parts.append(f"I asked at {when}: \"{asked}\"; " +
                         (f"what you said next: \"{said['message']}\"" if said and not said.get("this_turn")
                          else "your message now is the first since" if said else "you have not answered yet"))
    if nothing and not parts:
        return "Waiting on you: nothing."
    return "Waiting on you: " + "; ".join(parts) + "."


def first(d: Day) -> str | None:
    found = anomalies(d, d.clock.now)
    if not found:
        return None
    n = max(d.cap, LOOK)
    return "Look first: " + "; ".join(f"{what}: {'; '.join(_cap(xs, n))}" for what, xs in found) + "."


def predictions(p) -> str:
    if isinstance(p, str):
        return f"Predictions: {p}."
    if not p:
        return "Predictions: none."
    rows = [f"{x.get('id')} by {x.get('by')} about {x.get('about')}" + (f" ({x['expects']})" if x.get("expects") else "") +
            f": {x.get('state')}" for x in p]
    return f"Predictions ({len(rows)}): " + "; ".join(rows) + "."


def digest(events: list[dict], start: float, now: float, tz: str | None, *, outbox_state: str | None = None,
           waiting: dict | None = None, preds=None, model: str | None = None, ages: str | None = None) -> str:
    clock = Clock(tz, now)
    for cap in CAPS:
        d = Day(events, clock, cap)
        head = (f"Since {clock.local(start):%a} {clock.local(start).day} {clock.local(start):%b} "
                f"{clock.local(start):%H:%M} (all times {clock.named()}): {_n(len(d.events), 'event')} on the bus.")
        paras = [head, first(d), tickets(d), pull_requests(d), checks(d), labels(d), incidents(d),
                 outbox(d, outbox_state), items(d), quiet(d), others(d), owner(waiting, clock), predictions(preds), ages, model]
        text = "\n\n".join(p for p in paras if p)
        if len(text) <= LIMIT:
            break
    return text
