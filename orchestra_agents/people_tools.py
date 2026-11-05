"""``propose_people``: the table that combines relayed accounts into people, for the Supervisor (the
owner, 2026-11-04: "people use different github user names than their jira or slack names, so we
would need a table of some sort to combine them"; the bridges' SPEC §8).

An account is not a person. The bridges' pollers relay each act as the account that did it
(``person.<system>.<account>``, announced as ``<system>.user.<account>`` with the name the system shows
as ``display_name``); only the project's identity map joins accounts into ``person.<key>``, with a
name and a team. This tool lists the candidate joins the bridges' table finds among the accounts the
bus announced and the map does not list (``orchestra_bridges.people_table``: exact, the same display
name on two systems; weak, a handle, word order, a close spelling, namesakes), each with its evidence
and confidence. Without ``join`` it is a dry listing and writes nothing.

**Who writes a join** (the owner, 2026-11-04: "why am I using AI if I need to do this"):

- an **exact** candidate (the same display name on two systems and no namesake on either, the
  table's 0.9 tier) is the Supervisor's to join under ``ask = "outside"``, on its own reason: written
  with ``joined_by: supervisor``, its evidence and confidence, and said to the owner in one line,
  with ``unjoin_people`` to undo it. Under ``always`` it is the owner's quoted yes, as before;
- a **weak** one (namesakes, a handle, word order, a close spelling) is the owner's in both modes:
  joined only when the owner's quoted words name it (its id, its proposed key or name, or one of
  its accounts). A matching handle is evidence about strings, and a wrong join merges two people's
  acts under a name that looks confirmed;
- a candidate someone unjoined is never joined again on the Supervisor's own reason: only on the
  owner's words.

The entries go to the identity map in the state directory (``<state>/bridges/people.toml``, written
through the bridges' ``identity.join``), never to a file in the repository; an owner's own
``[bridges] people`` file is read with it, its fields winning, and never written here. Who joined
each entry, with its evidence, is kept beside the map in the Supervisor's agent state
(``people_joins``, by key): the bridges' map reads every field of an entry other than its name,
team and ``bot`` as an account on a system, so it carries no field of ours. ``unjoin_people``
takes an entry out of the map again: one the Supervisor joined on its reason, one the owner joined
only on their words. The pollers read the map when they start: a join or an unjoin takes effect
for a poller at its next restart."""

from __future__ import annotations

import os
import re
import time

from . import bus_reads
from .ask import OWNER, acts
from .config import Project
from .confirm_words import ALL, YES
from .design import provenance, quoted_message, turn_of
from .store import AgentStore, StoreError

LISTED = 40               # candidates in one answer, the strongest first
NONE = "no candidates: no two unjoined accounts on two systems match"
MACHINE = "supervisor"
JOINS = "people_joins"    # the Supervisor's agent state: who joined each key, with what evidence
UNJOINED = "people_unjoined"   # candidate ids taken out again: never joined again on the Supervisor's own reason


def joins(store: AgentStore) -> dict:
    return store.agent_state(MACHINE).get(JOINS) or {}


def _unjoined(store: AgentStore) -> dict:
    return store.agent_state(MACHINE).get(UNJOINED) or {}


def _own(c) -> bool:
    """Whether the Supervisor may join candidate ``c`` on its own reason: exact evidence only."""
    from orchestra_bridges.people_table import CONFIDENCE
    return c.strength == "exact" and c.confidence >= CONFIDENCE["display_name"]


def _bridges():
    try:
        from orchestra_bridges import identity, people_table
    except ImportError:
        return None
    return identity, people_table


def _names(message: str, c) -> bool:
    """Whether the owner's words name candidate ``c``: its id, its proposed key or name, an account or
    a display name one of them shows."""
    text = (message or "").casefold()
    words = {c.id, c.key, c.key.replace("_", " "), *(a.account for a in c.accounts),
             *(a.display_name for a in c.accounts if a.display_name)}
    if c.name:
        words.add(c.name)
    return any(re.search(rf"(?<![\w]){re.escape(w.casefold())}(?![\w])", text) for w in words if w and len(w) >= 3)


def propose_people(project: Project, store: AgentStore, a: dict, role: str) -> dict:
    if role != "supervisor":
        raise StoreError("propose_people is the Supervisor's")
    found = _bridges()
    if found is None:
        return {"ok": False, "error": "the bridges plugin (orchestra-bridges) is not installed: no relayed accounts to join"}
    identity, table = found
    o = project.orchestra
    files = identity.people_files(o.config.raw, o.repo_root, o.state_dir)
    people = identity.People.load(files)
    candidates = table.propose(table.seen(bus_reads.recent(store, table.SCAN)), people)
    target = identity.state_map(o.state_dir)
    join = [str(x).strip() for x in a.get("join") or [] if str(x).strip()]
    if not join:
        exact = [c for c in candidates if c.strength == "exact"]
        own = acts(project)
        ask = (("Nothing is written yet. Join the exact candidates yourself (join with their ids and a reason: the same "
                "display name on two systems), then tell the owner in one line who you joined and that unjoin_people "
                "undoes it; put each weak one to the owner on its own and join it on their words."
                if exact else "Nothing is written. Each candidate is weak: put each to the owner on its own and join it on "
                "their words.") if own else
               ("Nothing is written. Under ask = \"always\" every join is the owner's yes, exact ones included: put the "
                + (f"{len(exact)} exact candidate{'s' if len(exact) != 1 else ''} to them in one sentence and "
                   if exact else "") + "each weak one on its own, then call again with join and their quote."))
        return {"ok": True, "dry_run": True, "candidates": [c.to_dict() for c in candidates[:LISTED]],
                "lines": [table.line(c) for c in candidates[:LISTED]] or [NONE],
                "more": max(0, len(candidates) - LISTED), "map": str(target), "ask": ask}
    prov = provenance(store, a)
    by_id = {c.id: c for c in candidates}
    unknown = [x for x in join if x not in by_id]
    if unknown:
        raise StoreError(f"no candidate {', '.join(unknown)} now (joined already, or not proposed): call propose_people "
                         "without join for the current list")
    chosen = [by_id[x] for x in join]
    if prov.get("author") != OWNER:
        _own_join(project, store, chosen)
        return _write(identity, target, store, chosen, prov, "supervisor")
    said = quoted_message(store, a.get("quote") or "", turn_of(store).get("message") or "") or ""
    if not YES.search(said):
        raise StoreError("the owner's words do not say yes: ask them in one sentence whether to join these accounts")
    unnamed = [c.id for c in chosen if not _names(said, c) and not (c.strength == "exact" and ALL.search(said))]
    if unnamed:
        raise StoreError(f"the owner's words do not name {', '.join(unnamed)}: a weak candidate is joined only when they "
                         "name it (its id, its name or one of its accounts), and exact ones by name or with 'all' or "
                         "'these'; ask again in one sentence")
    return _write(identity, target, store, chosen, prov, OWNER)


def _own_join(project: Project, store: AgentStore, chosen: list) -> None:
    """Refuse a join on the Supervisor's own reason that is not its to make."""
    if not acts(project):
        raise StoreError("under [agents] ask = \"always\" joining accounts into a person is the owner's yes, exact "
                         "candidates included: ask them in one sentence, then pass join with their words as quote")
    weak = [c.id for c in chosen if not _own(c)]
    if weak:
        raise StoreError(f"{', '.join(weak)} {'is' if len(weak) == 1 else 'are'} weak (no exact display name on two "
                         "systems): a weak candidate is the owner's to join; ask them about each on its own in one "
                         "sentence and join it on their quoted words. Nothing was joined")
    again = [c.id for c in chosen if c.id in _unjoined(store)]
    if again:
        raise StoreError(f"{', '.join(again)} {'was' if len(again) == 1 else 'were'} unjoined before: join "
                         f"{'it' if len(again) == 1 else 'them'} again only on the owner's words. Nothing was joined")


def _write(identity, target, store: AgentStore, chosen: list, prov: dict, by: str) -> dict:
    keys = identity.join(target, [c.entry() for c in chosen])
    now = time.time()
    recs = {k: {"joined_by": by, "candidate": c.id, "strength": c.strength, "confidence": c.confidence,
                "evidence": list(c.evidence), "accounts": c.entry()["accounts"], "reason": prov.get("reason"),
                **({"quote": prov["quote"]} if prov.get("quote") else {}), "turn": prov.get("turn"), "at": now}
            for k, c in zip(keys, chosen)}
    store.update_agent_state(MACHINE, JOINS, lambda d: {**(d or {}), **recs})
    store.update_agent_state(MACHINE, UNJOINED, lambda d: {k: v for k, v in (d or {}).items()
                                                            if k not in {c.id for c in chosen}})
    who = "on the owner's yes" if by == OWNER else "as your own decision, on exact evidence (the same display name)"
    return {"ok": True, "joined": [{"id": c.id, "key": c.key, "accounts": c.entry()["accounts"], "joined_by": by,
                                    "evidence": list(c.evidence)} for c in chosen],
            "keys": keys, "map": str(target),
            "line": (f"Joined {len(keys)} {'person' if len(keys) == 1 else 'people'} in the identity map ({', '.join(keys)}), "
                     f"{who}; each poller uses it from its next restart."
                     + ("" if by == OWNER else " Tell the owner in one line who you joined and why, and that unjoin_people "
                        "undoes it."))}


def unjoin_people(project: Project, store: AgentStore, a: dict, role: str) -> dict:
    """Take one person's entry out of the identity map in the state directory: their accounts are
    accounts again. One the Supervisor joined on its own reason; one the owner joined (or one with no
    record of who joined it) only on the owner's quoted words; never the owner's own file's."""
    if role != "supervisor":
        raise StoreError("unjoin_people is the Supervisor's")
    found = _bridges()
    if found is None:
        return {"ok": False, "error": "the bridges plugin (orchestra-bridges) is not installed: there is no identity map"}
    identity, _ = found
    key = str(a.get("key") or "").strip()
    if not key:
        raise StoreError("key names the person to unjoin, as the identity map lists it (alice_example)")
    prov = provenance(store, a)
    o = project.orchestra
    target = identity.state_map(o.state_dir)
    have = identity.entries_of(target)
    if key not in have:
        own = identity.people_file(o.config.raw, o.repo_root)
        if own is not None and key in identity.entries_of(own):
            raise StoreError(f"{key} is in the owner's own identity map ({own}), which is never written here: say so")
        raise StoreError(f"no {key} in the identity map ({target}): its keys are {', '.join(sorted(have)) or 'none'}")
    rec = joins(store).get(key) or {}
    if rec.get("joined_by") != "supervisor" and prov.get("author") != OWNER:
        how = "on the owner's yes" if rec.get("joined_by") == OWNER else "with no record of who joined it"
        raise StoreError(f"{key} was joined {how}: unjoin it only on the owner's quoted words; ask them in one sentence")
    _rewrite(identity, target, {k: v for k, v in have.items() if k != key})
    store.update_agent_state(MACHINE, JOINS, lambda d: {k: v for k, v in (d or {}).items() if k != key})
    if rec.get("candidate"):
        store.update_agent_state(MACHINE, UNJOINED, lambda d: {**(d or {}), rec["candidate"]: {
            "key": key, "by": prov.get("author"), "reason": prov.get("reason"), "at": time.time()}})
    accounts = {s_: v for s_, v in have[key].items() if s_ not in ("name", "team", "bot")}
    return {"ok": True, "unjoined": key, "accounts": accounts, "map": str(target),
            "line": (f"Unjoined {key}: {', '.join(f'{s_} {v}' for s_, v in sorted(accounts.items()))} are accounts again, "
                     "each poller from its next restart" + ("" if prov.get("author") == OWNER else
                                                            "; it is not joined again on your own reason") + ".")}


def _rewrite(identity, target, entries: dict) -> None:
    """The map with only ``entries``, written through the bridges' own ``join`` (to a new file, which
    then replaces the map whole), so the format stays theirs."""
    tmp = target.with_name(target.name + ".unjoin")
    tmp.unlink(missing_ok=True)
    rows = []
    for key, row in entries.items():
        if not isinstance(row, dict):
            continue
        listed = {s_: (v if isinstance(v, list) else [v]) for s_, v in row.items() if s_ not in ("name", "team", "bot")}
        first = {s_: v[0] for s_, v in listed.items() if v}
        rows.append({"key": key, "name": row.get("name"), "team": row.get("team"), "accounts": first})
        rows += [{"key": key, "accounts": {s_: x}} for s_, v in listed.items() for x in v[1:]]
    identity.join(tmp, rows)
    os.replace(tmp, target)
