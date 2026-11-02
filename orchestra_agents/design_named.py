"""The events the owner's words name (``owner_named`` on ``declare_machine`` and
``refine_machine``): each phrase as they said it ("the SAIL check results"), with the event types
the Supervisor matched it to. Every matched type goes into the machine's receives; the answer says
what each phrase was matched to and what could not be. A phrase that names a family heard on the
bus (``sail`` in "the SAIL check results", heard as ``sail.check.completed``) and has none of its
types is said as not matched, with the family's types: the T2b walkthrough's Supervisor left the
SAIL results out of the nudger's receives because another event (a SAILED label) carried the news.

A family is a type's first word (``sail`` of ``sail.check.completed``), matched as a whole word of
the phrase. Nothing else is guessed."""

from __future__ import annotations

import re

from .store import AgentStore, StoreError

WORD = re.compile(r"[a-z0-9_]+")


def _entries(named) -> list[tuple[str, list[str]]]:
    if named in (None, []):
        return []
    if not isinstance(named, list):
        raise StoreError("owner_named is a list of {said, types}: each phrase of the owner's naming events, with the types "
                         "you matched it to")
    out = []
    for x in named:
        if isinstance(x, str):
            x = {"said": x}
        if not isinstance(x, dict) or not str(x.get("said") or "").strip():
            raise StoreError("owner_named: each entry is {said: the owner's words, types: [event types]}")
        types = x.get("types") or []
        out.append((str(x["said"]).strip(), [types] if isinstance(types, str) else [str(t) for t in types]))
    return out


def resolve(project, store: AgentStore, named, but: str | None = None) -> tuple[list[str], list[str]]:
    """The known types the phrases were matched to (for receives), and the answer's lines: what
    each phrase was matched to, and what could not be."""
    from .bus_reads import known_types
    entries = _entries(named)
    if not entries:
        return [], []
    known = known_types(project, store, but)
    families: dict[str, list[str]] = {}
    for t in sorted(known):
        families.setdefault(t.split(".")[0], []).append(t)
    types, matched, missed = [], [], []
    for said, given in entries:
        ok = [t for t in given if t in known]
        types += [t for t in ok if t not in types]
        if ok:
            matched.append(f"'{said}' -> {', '.join(ok)}")
        if bad := [t for t in given if t not in known]:
            missed.append(f"'{said}': {', '.join(bad)} not heard on the bus or announced (read_events' by_type)")
        words = set(WORD.findall(said.lower()))
        for fam in sorted(f for f in families if f in words and not any(t.split(".")[0] == f for t in ok)):
            missed.append(f"'{said}' names {fam}, heard as {', '.join(families[fam])}: none is in its types; add the one "
                          "it means to receives, in this turn (another event carrying the same news does not stand for it)")
        if not ok and not bad and not any(f in words for f in families):
            missed.append(f"'{said}': no type matched; read_events lists the types heard, or ask the owner which they mean")
    lines = []
    if matched:
        lines.append("the owner's words matched, in receives: " + "; ".join(matched))
    if missed:
        lines.append("could not match: " + "; ".join(missed))
    return types, lines
