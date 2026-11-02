"""The events the owner's words name (``owner_named`` on ``declare_machine`` and
``refine_machine``): each phrase as they said it ("the SAIL check results"), with the event types
the Supervisor matched it to. Only a type **heard on the bus** (an event of it read) goes into the
machine's receives: a type only announced, declared or emitted by a declared machine is plausible
but never on the bus, and is left out and said so. The T2c walkthrough's Supervisor gave "the
GitHub pull request events" ``github.pr.approved`` and ``github.pr.changes_requested``, which the
Workflows plugin's proposals name but the stand-in reviewer never published. The answer says which
heard types each phrase became, which plausible types were not on the bus, and which heard types
of the phrase's family it did not name.

A phrase's family is the longest type prefix whose every word is in the phrase (``github.pr`` in
"the GitHub pull request events", "pull request" read as ``pr``; ``sail.check`` in "the SAIL check
results"). The family only names what the answer says; nothing is added from it."""

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


ALIASES = {("pull", "request"): "pr", ("pull", "requests"): "pr", ("prs",): "pr"}


def words_of(said: str) -> set[str]:
    """The phrase's words, a plural also as its singular, and the aliases its words make."""
    ws = WORD.findall(said.lower())
    out = set(ws) | {w[:-1] for w in ws if len(w) > 3 and w.endswith("s")}
    for i in range(len(ws)):
        for k, v in ALIASES.items():
            if tuple(ws[i:i + len(k)]) == k:
                out.add(v)
    return out


def family(said: str, types) -> str | None:
    """The longest prefix of a known type whose every segment is a word of the phrase."""
    words, best = words_of(said), None
    for t in types:
        segs = t.split(".")
        k = 0
        while k < len(segs) - 1 and segs[k] in words:
            k += 1
        if k and (best is None or k > best.count(".") + 1):
            best = ".".join(segs[:k])
    return best


def resolve(project, store: AgentStore, named, but: str | None = None) -> tuple[list[str], list[str], set[str]]:
    """The heard types the phrases were matched to (for receives), the answer's lines, and the
    types left out (named, or of a phrase's family, and not heard on the bus): never in receives."""
    from .bus_reads import known_types
    entries = _entries(named)
    if not entries:
        return [], [], set()
    known = known_types(project, store, but)
    heard = {t for t, how in known.items() if how.startswith("heard")}
    types, matched, missed, dropped = [], [], [], set()
    for said, given in entries:
        ok = [t for t in given if t in heard]
        types += [t for t in ok if t not in types]
        fam = family(said, [*known, *given])
        prefix = f"{fam}." if fam else None

        def under(t: str, prefix=prefix) -> bool:
            return prefix is not None and t.startswith(prefix)
        plausible = list(dict.fromkeys([*(t for t in given if t not in heard),
                                        *(t for t in sorted(known) if under(t) and t not in heard)]))
        dropped |= set(plausible)
        if ok:
            matched.append(f"'{said}' -> {', '.join(ok)}")
        if plausible:
            missed.append(f"'{said}': {', '.join(plausible)} never heard on the bus (plausible, but only announced, "
                          "declared or guessed): left out of receives")
        if fam and (other := [t for t in sorted(heard) if under(t) and t not in ok]):
            missed.append(f"'{said}' names {fam}, heard as {', '.join(other)}: "
                          + ("none is in its types; add" if not ok else "not in its types; add") + " each it means to "
                          "owner_named's types, in this turn (another event carrying the same news does not stand for it)")
        if not ok and not plausible and not fam:
            missed.append(f"'{said}': no type matched; read_events lists the types heard, or ask the owner which they mean")
    lines = []
    if matched:
        lines.append("the owner's words matched, in receives (heard on the bus): " + "; ".join(matched))
    if missed:
        lines.append("could not match: " + "; ".join(missed))
    return types, lines, dropped
