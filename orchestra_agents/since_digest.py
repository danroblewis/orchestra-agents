"""The whole answer to "what changed since <when>", written by code (``read_events`` with
``since``: its ``digest``), so the Supervisor replies with it as it is. Seven walkthroughs gave a
small model every fact in one tool answer, and each time it dropped some and misstated others (T2g
s16: "#33 and #34 opened at 04:57" for 04:16, "all three merged at 05:12" for one, "2 opened, 2
closed" for three tickets Done, the triage verdict and the prediction's state left out).

It leads with what has died (``since_dead``, from Orchestra's runtime record: a process the launcher
stopped retrying, named with the items waiting on it, and an instrument that cannot fetch its
target; T2k s16), then what looks wrong now (``since_state``, from the Workflows plugin's open items,
``item_state``: an outside result that never came, attention raised and unanswered, past a step's
limit, longer than the step usually takes), then what the window's events say looks wrong
(``since_anomalies``: new work on a Done ticket, a label on a merged pull request, an open item
whose pull request closed unmerged) and its oddities (the host restarted again and again); then one
paragraph per class, in this order: tickets, pull requests (with each rejection and triage
verdict), checks, labels, incidents, the Outbox, items (the open ones now, named), machines marked
quiet, the other events counted, what waits on the owner, the predictions, the machines by age
(``machine_ages``), the subsystems line at most once a day (``subsystem_offer``), the Supervisor's
own pull request (``supervisor_pr.digest_line``), and the model line last (``closing_line``).

**Replays** (an event a poller read again at a resume, ``replayed: true``) are counted apart in the
first line and never flagged nor listed as new; what they close stays closed. **Every list names the
newest first** (T2j s16: each list showed its oldest entry, and the night's tickets SCRUM-45 to 50
and pull requests #49 to #55 never appeared; T2k s16: the open ones put first broke the order, and
the attention list ran oldest first), and a list cut short says how many it leaves out.

**A stale nudge is withdrawn** (``stale_nudges.withdraw_stale``): an Outbox draft that reminds
someone about a pull request the bus says merged or closed is never offered as waiting on the
owner; the owner's paragraph says it was withdrawn and why (T2k s16: a reminder about #51, merged
an hour before, was offered for approval).

**The reply is bounded** to ``LIMIT`` (1,500 characters: what an owner reads in a minute; T2j's
ran about 4,000): it leaves to "more" the other events, the machines marked quiet, the machines
by age, the labels and the checks, then says each list by its names alone (``brief``), then
shortens what waits on the owner to its first words, then names fewer entries, and says
'Say "more" for the full list' before the model line whenever anything was left out. ``full`` (the
owner's "more") names up to ``FULL`` entries per list, unbounded. What looks wrong now names at
least ``LOOK`` per kind, and what waits on the owner is always named.

Every number is counted here and every time is the event's own, in one zone, named once in the
first line (the host's: the pages show the viewer's clock, and the owner's viewer is this host).

A ticket is known by the ticket system's events, and also by an Outbox send whose kind names a
ticket (``live.create_ticket``, ``live.close_ticket``) for an item whose facts name the ticket
(a ``ticket`` fact, its key the last part of the link): a ticket opened and closed through the
Outbox is counted once, at the earliest time either says. Nothing here reads beyond the events,
the facts ``since_facts`` and ``owner_waits`` read, the open items ``item_state`` reads, and the
model line."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import since_dead, since_state
from .bus_reads import _ts
from .event_tally import DONE, field
from .since_anomalies import anomalies

CAPS = (5, 4, 3, 2, 1)       # entries named per list (the bounded reply starts at 3)
LIMIT = 1500                 # characters: what an owner reads in a minute (T2j: 4,000 was not read)
FULL = 20                    # entries named per list on "more" (the full list)
LOOK = 3                     # the anomalies name at least this many per kind, whatever the cap

OWNER = ("owner", "person.owner")
KEY = re.compile(r"[A-Z][A-Z0-9_]*-\d+")
OPENS, CLOSES = ("create", "open"), ("close", "done", "resolve")
TOLD = ("issue.created", "issue.assigned", "issue.transitioned", "issue.closed", "issue.resolved", "pr.opened",
        "pr.merged", "pr.labeled", "pr.unlabeled", "pr.commented", "pr.synchronize", "checks.completed",
        "check.completed", "incident.triggered", "incident.acknowledged", "incident.resolved", "outbox.draft.created",
        "outbox.draft.approved", "outbox.draft.rejected", "outbox.sent", "outbox.send_failed", "item.requested",
        "item.opened", "item.closed", "attention.needed", "orchestra.machine.quiet", "orchestra.model.changed")


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _times(n: int) -> str:
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


def _cap(xs: list, n: int) -> list:
    return xs if len(xs) <= n else [*xs[:n], f"… and {len(xs) - n} more"]


def _newest(xs: list, n: int) -> list:
    """A list in time order, said newest first and capped."""
    return _cap(list(reversed(xs)), n)


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

    def __init__(self, events: list[dict], clock: Clock, cap: int = CAPS[0], brief: bool = False):
        self.clock, self.cap, self.brief = clock, cap, brief      # brief: the bounded reply's short paragraphs
        timed = sorted(((t, e) for e in events if (t := _ts(e)) is not None), key=lambda x: x[0])
        self.total = len(timed)
        self.all = timed                              # with the replays: what is closed is closed, replayed or not
        self.events = [(t, e) for t, e in timed if field(e, "replayed") is not True]    # a resume's replays: apart
        self.replayed = self.total - len(self.events)
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
        self.flagged: set[str] = set()               # the open items "Look first" named (the Items line: "above")

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
        """``C at 04:57, A and B at 04:11``: newest first, labels at one minute said together (T2j: the
        oldest first hid the night's work; T2k: the open ones first broke the order)."""
        groups: list[tuple[str, list[str]]] = []
        more = len(pairs) - self.cap
        pairs = sorted(pairs, key=lambda x: -x[1])
        if self.brief:                                    # the bounded reply: the names alone
            return ", ".join(_cap([label for label, _ in pairs], self.cap))
        for label, ts in pairs[:self.cap]:
            t = self.clock.at(ts)
            if groups and groups[-1][0] == t:
                if label not in groups[-1][1]:           # the same one twice in a minute (a replay): said once
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
        return d.runs([(k + (" (through the Outbox)" if via.get((what, k)) else ""), t) for k, t in book.items()])
    parts = [f"{len(opened)} opened: {told(opened, 'opened')}" if opened else "none opened"]
    done = "Done" if d.brief else "closed (Done)"
    if assigned and not d.brief:
        parts.append(f"{len(assigned)} assigned: {d.runs(assigned)}")
    parts.append(f"{len(closed)} {done}: {told(closed, 'closed')}" if closed else "none closed")
    return "Tickets: " + "; ".join(parts) + "."


def _bare(pairs: list[tuple[str, float]], d: Day) -> list[tuple[str, float]]:
    """In the bounded reply a pull request by its number alone once its ticket was named."""
    return [(x.split(" ", 1)[0], t) for x, t in pairs] if d.brief else pairs


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
            triage.append((d.item(item), d.clock.at(t), field(e, "outcome") or "?", why))
    if not (opened or merged or rejected or comments or pushed or triage):
        return None
    parts = [f"{len(opened)} opened: {d.runs(opened)}" if opened else "none opened",
             f"{len(merged)} merged: {d.runs(_bare(merged, d))}" if merged else "none merged",
             (f"{len(rejected)} rejected: {d.runs(_bare(rejected, d))}" if d.brief else
              f"{len(rejected)} rejected (labelled REJECTED): {d.runs(rejected)}") if rejected else "none rejected"]
    if triage:
        if d.brief:                                   # by verdict: "local twice (SCRUM-49, SCRUM-48)"
            by: dict[str, list[str]] = {}
            for who, _, outcome, _ in reversed(triage):
                by.setdefault(str(outcome), []).append(who.split(" ", 1)[0])
            parts.append(f"{_n(len(triage), 'triage verdict')}: " +
                         "; ".join(f"{o} {_times(len(ws))} ({', '.join(_cap(ws, d.cap))})" for o, ws in by.items()))
        else:
            said = [f"{who} at {at}: {outcome}" + (f" ({why})" if why else "") for who, at, outcome, why in triage]
            parts.append(f"{_n(len(triage), 'triage verdict')}: {'; '.join(_newest(said, d.cap))}")
    if d.brief:
        return "Pull requests: " + "; ".join(parts) + "."
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
            return ", ".join(_newest([x for x, _ in xs], d.cap)) + (f" (each: {w})" if w else "")
        return ", ".join(_newest([x + (f" ({w})" if w else "") for x, w in xs], d.cap))
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
        out.append(f"{inc}" + (f" ({about})" if about else "") + ("" if d.brief else " " + ", ".join(row["what"])) +
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
    if d.brief:
        how = ("all approved by configuration" if by_config == n and not by_owner and n else
               f"{by_config} approved by configuration, {len(by_owner)} by you" if by_config or by_owner else "")
        bad = ", ".join(x for x in (f"{len(rejected)} rejected" if rejected else "",
                                    f"{len(failed)} failed to send" if failed else "") if x)
        return ("The Outbox: " + f"{n} sent" + (f" ({', '.join(f'{k} {len(ts)}' for k, ts in sent.items())})" if n else "") +
                "".join(f"; {x}" for x in (how, bad) if x) + "." +
                (f" Now: {re.sub(r'; \d+ sent since then( \([^)]*\))?', '', state)}." if state else ""))
    parts = [f"{n} sent" + (": " + "; ".join(f"{k} {_times(len(ts))} ({', '.join(_newest(ts, d.cap))})" for k, ts in sent.items()) if n else "")]
    if by_config or by_owner:
        parts.append(f"{by_config} approved by configuration, {len(by_owner)} by you" +
                     (f" ({', '.join(_newest(by_owner, d.cap))})" if by_owner else ""))
    parts.append(f"{len(rejected)} rejected" + (f" ({', '.join(_newest(rejected, d.cap))})" if rejected else ""))
    parts.append(f"{len(failed)} failed to send" + (f" ({', '.join(_newest(failed, d.cap))})" if failed else ""))
    return "The Outbox: " + "; ".join(parts) + "." + (f" Its state now: {state}." if state else "")


def items(d: Day, open_now: int | None = None, rows: list[dict] | None = None) -> str | None:
    """Items started (``item.requested``, ``item.opened``), closed and raised; and how many are open
    now (``open_now``, the Workflows plugin's own count): never "none started" while any are open
    (bootstrap run 3: a start event opens an item and publishes no item event)."""
    started, closed, attention = [], [], []
    for t, e in d.of("item.requested", "item.opened", "item.closed", "attention.needed"):
        item, ty = str(e.get("subject") or "?"), e["type"]
        if ty in ("item.requested", "item.opened"):
            by = ("by you" if e.get("source") in OWNER else "on its schedule" if field(e, "every")
                  else f"by {e.get('source')}")
            started.append(f"{d.item(item)} {by} at {d.clock.at(t)}")
        elif ty == "item.closed":
            how = ": ".join(str(x) for x in (field(e, "last_step"), field(e, "outcome")) if x)
            closed.append(f"{d.item(item)} at {d.clock.at(t)}" + (f" ({how})" if how else ""))
        else:
            why = field(e, "reason")
            attention.append(f"{d.item(item)} at {d.clock.at(t)}" + (f" ({why})" if why else ""))
    if rows:
        from .since_state import spoken
        now = sorted(rows, key=lambda r: r.get("age_s") or 0)             # the newest first
        above = [r for r in now if d.brief and str(r.get("item")) in d.flagged]
        now = [r for r in now if r not in above]
        named = [f"{d.item(str(r.get('item')))} at {r.get('step')}" +
                 (f" {spoken(r['age_s'])}" if isinstance(r.get("age_s"), int | float) else "") for r in now]
        open_now = len(rows)
    if not (started or closed or attention or open_now):
        return None
    parts = []
    if rows:
        parts.append(f"{open_now} open now" + (f": {', '.join(_cap(named, max(d.cap, LOOK)))}" if named else "") +
                     (f"{' and' if named else ':'} the {len(above)} above" if above else ""))
    elif open_now:
        parts.append(f"{open_now} open now" + ("" if started else
                     " (a workflow's start event opens one with no item event; the Items page lists them)"))
    if d.brief:                                       # the open ones named; attention is in "Look first"
        started = [x.split(" by ", 1)[0].split(" on its schedule", 1)[0] for x in started]
        closed = [x.split(" (", 1)[0] if x.count(" (") > 1 or not x.startswith(x.split(" ")[0] + " (") else x
                  for x in closed]
        closed = [re.sub(r" \([a-z_]+: [a-z_]+\)$", "", x) for x in closed]
    parts.append(f"{len(started)} started" + ("" if d.brief and rows else f": {'; '.join(_newest(started, d.cap))}")
                 if started else "" if open_now else "none started")
    parts.append(f"{len(closed)} closed" + (f": {'; '.join(_newest(closed, d.cap))}" if not d.brief else "")
                 if closed else "none closed")
    if attention and not d.brief:
        parts.append(f"attention raised {_times(len(attention))}: {'; '.join(_newest(attention, d.cap))}")
    return "Items: " + "; ".join(x for x in parts if x) + "."


def quiet(d: Day) -> str | None:
    who: dict[str, list[tuple[str, float]]] = {}
    for t, e in d.of("orchestra.machine.quiet"):
        name = str(field(e, "instance") or field(e, "machine") or e.get("subject") or "?")
        who.setdefault(name, []).append((name, t))
    if not who:
        return None
    said = [f"{name} at {', '.join(_newest([d.clock.at(t) for _, t in xs], d.cap))}" for name, xs in who.items()]
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


SHORT = 40                   # a disabled machine's reason in the bounded reply, at most (the rest on "more")


def _short(text: str, short: bool) -> str:
    return text if not short or len(text) <= SHORT else text[:SHORT - 1].rstrip() + "…"


def owner(w: dict | None, clock: Clock, short: bool = False) -> str:
    if not isinstance(w, dict):
        return "Waiting on you: not read."
    parts, nothing = [], True
    m = w.get("machines")
    if isinstance(m, str):
        parts.append(f"the machines were {m}")
    elif m:
        nothing = False
        parts += [f"{x.split(': ', 1)[0]} is disabled" + (f" ({_short(x.split(': ', 1)[1], short)})" if ": " in x else "")
                  for x in m]
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
    gone = withdrawn(w.get("withdrawn"), clock)
    if nothing and not parts:
        return "Waiting on you: nothing." + gone
    return "Waiting on you: " + "; ".join(parts) + "." + gone


def withdrawn(rows, clock: Clock) -> str:
    """The stale nudges withdrawn from what waits on the owner, in a sentence (or nothing)."""
    if not rows:
        return ""
    said = [f"Outbox draft {r.get('id')} ({r.get('kind')}) about #{r.get('number')}, {r.get('state')}" +
            (f" at {clock.at(r['at'])}" if isinstance(r.get("at"), int | float) else "") for r in rows]
    return (" Withdrawn, never offered (the pull request it reminds about is done): " + "; ".join(said) +
            "; reject it on the Outbox page to clear it.")


RESTARTS = 3                 # the host started this many times in the window: said among the oddities


def oddities(d: Day) -> list[tuple[str, list[str]]]:
    """The window's oddities beside its anomalies: the host restarted again and again."""
    starts = [t for t, e in d.events if e.get("type") == "orchestra.host.started"]
    if len(starts) >= RESTARTS:
        return [("the host restarted", [f"{_times(len(starts))}, the last at {d.clock.at(starts[-1])}"])]
    return []


def _named(line: str) -> str:
    """A window anomaly by what it is about alone (the bounded reply; the rest on "more")."""
    return line.split(" (", 1)[0].split(" at ", 1)[0]


def first(d: Day, state: dict | None = None, look: int = LOOK, record: dict | None = None) -> str | None:
    """What has died (``since_dead``: a stopped process with the items waiting on it, an instrument
    that cannot fetch), then what is wrong now (``since_state``: the open items stuck, from the
    Workflows plugin; an item a dead process blocks is said there once), then what the window's
    events say looks wrong (``since_anomalies``), then its oddities; never a replay. With the plugin
    read, its open items say which attention is unanswered, not the bus."""
    died, blocked = since_dead.dead(d, record, state)
    now = died + since_state.stuck(d, state, blocked)
    day = [(k, xs) for k, xs in anomalies(d, d.clock.now) if not (state and k.startswith("attention over"))]
    odd = oddities(d)
    if not (now or day or odd):
        return None
    lines = []
    for what, xs in now if d.brief else now + day + odd:
        xs = list(dict.fromkeys(xs))                   # one line once (T2j: a REJECTED said twice)
        lines.append(f"- {what[0].upper()}{what[1:]}: {'; '.join(_cap(xs, look))}.")
    if d.brief and (day or odd):                       # the day's oddities in one line, each by what it is about
        said = [f"{what} ({', '.join(_cap(list(dict.fromkeys(_named(x) for x in xs)), 2))})" for what, xs in day]
        said += [f"{what} {xs[0].split(',', 1)[0]}" for what, xs in odd]
        lines.append(f"- {'Also today' if now else 'Today'}: {'; '.join(said)}.")
    text = "Look first:\n" + "\n".join(lines)
    d.flagged = {str(r.get("item")) for r in (state or {}).get("rows") or []
                 if any(f"{d.item(str(r.get('item')))} {w}at " in text for w in ("", "waits "))}
    return text


def predictions(p) -> str:
    if isinstance(p, str):
        return f"Predictions: {p}."
    if not p:
        return "Predictions: none."
    rows = [f"{x.get('id')} by {x.get('by')} about {x.get('about')}" + (f" ({x['expects']})" if x.get("expects") else "") +
            f": {x.get('state')}" for x in p]
    return f"Predictions ({len(rows)}): " + "; ".join(rows) + "."


MORE = 'Say "more" for the full list.'
EXTRA = ("others", "quiet", "ages", "labels", "checks")     # left to "more" first, in this order


def digest(events: list[dict], start: float, now: float, tz: str | None, *, outbox_state: str | None = None,
           waiting: dict | None = None, preds=None, model: str | None = None, ages: str | None = None,
           subsystems: str | None = None, own_pr: str | None = None, open_items: int | None = None,
           state: dict | None = None, record: dict | None = None, full: bool = False) -> str:
    """The reply: bounded to about ``LIMIT`` characters (an owner reads it in a minute), the rest on
    "more" (``full``: every entry of every list, unbounded). The bounded reply leaves out, in turn,
    the paragraphs of ``EXTRA``, then names fewer entries per list, then shortens the reasons of what
    waits on the owner, then fewer per kind of what looks wrong; never the head, what looks wrong,
    what waits on the owner, the Supervisor's own pull request or the model line, which is last."""
    clock = Clock(tz, now)
    rows = (state or {}).get("rows")
    if full:
        return _text(Day(events, clock, FULL), start, outbox_state, waiting, preds, model, ages, subsystems, own_pr,
                     open_items, state, rows, (), False, FULL, record, more=False)
    ladder = [(CAPS[2], EXTRA[:k], False, False, LOOK) for k in range(len(EXTRA) + 1)]
    ladder += [(CAPS[2], EXTRA, True, False, LOOK)] + [(c, EXTRA, True, True, LOOK) for c in CAPS[2:]]
    ladder += [(1, EXTRA, True, True, 2), (1, EXTRA, True, True, 1)]
    text = ""
    for cap, drop, brief, short, look in ladder:
        text = _text(Day(events, clock, cap, brief), start, outbox_state, waiting, preds, model, ages, subsystems, own_pr,
                     open_items, state, rows, drop, short, look, record, more=True)
        if len(text) <= LIMIT:
            break
    return text


def _text(d: Day, start, outbox_state, waiting, preds, model, ages, subsystems, own_pr, open_items, state, rows,
          drop, short: bool, look: int, record: dict | None, *, more: bool) -> str:
    clock = d.clock
    replays = (f", {d.replayed} of them replayed at a resume (read again by a poller: counted apart, never flagged)"
               if d.replayed and not d.brief else f", {d.replayed} replayed at a resume (counted apart)" if d.replayed else "")
    zone = (clock.local(clock.now).tzname() or clock.named()) if d.brief else clock.named()
    head = (f"Since {clock.local(start):%a} {clock.local(start).day} {clock.local(start):%b} "
            f"{clock.local(start):%H:%M} (all times {zone}): {_n(d.total, 'event')} on the bus{replays}.")
    paras = [("head", head), ("look", first(d, state, look, record)), ("tickets", tickets(d)), ("prs", pull_requests(d)),
             ("checks", checks(d)), ("labels", labels(d)), ("incidents", incidents(d)), ("outbox", outbox(d, outbox_state)),
             ("items", items(d, open_items, rows)), ("quiet", quiet(d)), ("others", others(d)),
             ("owner", owner(waiting, clock, short)), ("predictions", predictions(preds)), ("ages", ages),
             ("subsystems", subsystems), ("own_pr", own_pr)]
    kept = [p for name, p in paras if p and name not in drop]
    cut = any(p and name in drop for name, p in paras) or any("… and" in p for p in kept)
    if more and cut:
        kept.append(MORE)
    return "\n\n".join([*kept, *([model] if model else [])])
