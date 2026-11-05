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

**Writing is the owner's, in both ``ask`` modes, exact candidates included** (the choice, 2026-11-04):
joining accounts is a claim about people. A matching display name is evidence about strings, not
people: namesakes are common among hundreds of people, a Slack or GitHub name is whatever its owner
typed, and a wrong join merges two people's acts into one machine under a name that looks confirmed.
So ``join`` needs the owner's quoted yes (``quote``), never the Supervisor's own reason under ``ask =
"outside"``. The exact candidates may be confirmed together (the owner's yes to "all", "them",
"these", or naming them); a weak one only when the owner's words name it (its id, its proposed key or
name, or one of its accounts), so it is asked on its own. The entries go to the identity map in the
state directory (``<state>/bridges/people.toml``, written through the bridges' ``identity.join``),
never to a file in the repository; an owner's own ``[bridges] people`` file is read with it, its
fields winning, and never written here. The pollers read the map when they start: a join takes effect
for a poller at its next restart."""

from __future__ import annotations

import re

from . import bus_reads
from .ask import OWNER
from .config import Project
from .confirm_words import ALL, YES
from .design import provenance, quoted_message, turn_of
from .store import AgentStore, StoreError

LISTED = 40               # candidates in one answer, the strongest first
NONE = "no candidates: no two unjoined accounts on two systems match"


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
        return {"ok": True, "dry_run": True, "candidates": [c.to_dict() for c in candidates[:LISTED]],
                "lines": [table.line(c) for c in candidates[:LISTED]] or [NONE],
                "more": max(0, len(candidates) - LISTED), "map": str(target),
                "ask": ("Nothing is written. Joining accounts is a claim about people, so every join is the owner's yes, "
                        "exact ones included, in both ask modes: put the "
                        + (f"{len(exact)} exact candidate{'s' if len(exact) != 1 else ''} to them in one sentence and "
                           if exact else "") + "each weak one on its own, then call again with join and their quote.")}
    prov = provenance(store, a)
    if prov.get("author") != OWNER:
        raise StoreError("joining accounts into a person is the owner's yes, in both ask modes, exact candidates included: "
                         "ask them in one sentence, then pass join with their words as quote")
    said = quoted_message(store, a.get("quote") or "", turn_of(store).get("message") or "") or ""
    if not YES.search(said):
        raise StoreError("the owner's words do not say yes: ask them in one sentence whether to join these accounts")
    by_id = {c.id: c for c in candidates}
    unknown = [x for x in join if x not in by_id]
    if unknown:
        raise StoreError(f"no candidate {', '.join(unknown)} now (joined already, or not proposed): call propose_people "
                         "without join for the current list")
    chosen = [by_id[x] for x in join]
    unnamed = [c.id for c in chosen if not _names(said, c) and not (c.strength == "exact" and ALL.search(said))]
    if unnamed:
        raise StoreError(f"the owner's words do not name {', '.join(unnamed)}: a weak candidate is joined only when they "
                         "name it (its id, its name or one of its accounts), and exact ones by name or with 'all' or "
                         "'these'; ask again in one sentence")
    keys = identity.join(target, [c.entry() for c in chosen])
    return {"ok": True, "joined": [{"id": c.id, "key": c.key, "accounts": c.entry()["accounts"]} for c in chosen],
            "keys": keys, "map": str(target),
            "line": (f"Joined {len(keys)} {'person' if len(keys) == 1 else 'people'} in the identity map ({', '.join(keys)}), "
                     "on the owner's yes; each poller uses it from its next restart.")}
