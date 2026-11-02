"""The events the owner's words name (``owner_named`` on ``declare_machine`` and
``refine_machine``): each phrase as they said it ("the SAIL check results"), with the event types
the Supervisor matched it to. Only a type **heard on the bus** (an event of it read) goes into the
machine's receives: a type only announced, declared or emitted by a declared machine is plausible
but never on the bus, and is left out and said so. The T2c walkthrough's Supervisor gave "the
GitHub pull request events" ``github.pr.approved`` and ``github.pr.changes_requested``, which the
Workflows plugin's proposals name but the stand-in reviewer never published. The answer says which
heard types each phrase became, which plausible types were not on the bus, and which heard types
of the phrase's family it did not name.

The exception is a relay's: a type a poller scaffolded by ``integrate_system`` declares in its
``[[publishes]]`` is the kit's, and is kept in receives before it is heard, marked "the kit's, not
heard yet" (a freshly approved Jira poller has published nothing; R3 e, T2i s10). It is never
dropped for not being heard.

A phrase's family is the longest type prefix whose every word is in the phrase (``github.pr`` in
"the GitHub pull request events", "pull request" read as ``pr``; ``sail.check`` in "the SAIL check
results"). The family only names what the answer says; nothing is added from it.

The Workflows floor's protocol types (``floor.PROTOCOL``: ``offer.made``, ``offer.withdrawn``, …) are
the plugin's, never the owner's: a phrase never adds or drops one, and the answer lists those a
machine receives apart (``protocol_line``), allowed whether or not heard (T2d s10)."""

from __future__ import annotations

import re

from .floor import PROTOCOL
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


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


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


def relay_types(store: AgentStore) -> dict[str, str]:
    """Each type a scaffolded poller declares in its ``[[publishes]]``, with the poller's name."""
    import tomllib
    from pathlib import Path

    from . import design_integrate, integrate_kits
    out: dict[str, str] = {}
    for name, rec in design_integrate.integrations(store).items():
        if not (rec.get("events") and rec.get("dir")):
            continue
        try:
            types = integrate_kits.publishes(Path(rec["dir"]))
        except (ImportError, OSError, ValueError, tomllib.TOMLDecodeError, StoreError):
            continue
        for t in types:
            out.setdefault(t, str(rec.get("name") or name))
    return out


def resolve(project, store: AgentStore, named, but: str | None = None) -> tuple[list[str], list[str], set[str]]:
    """The heard types the phrases were matched to (for receives), the answer's lines, and the
    types left out (named, or of a phrase's family, and not heard on the bus): never in receives."""
    from .bus_reads import known_types
    entries = _entries(named)
    if not entries:
        return [], [], set()
    known = known_types(project, store, but)
    heard = {t for t, how in known.items() if how.startswith("heard")}
    relay = {t: p for t, p in relay_types(store).items() if t not in heard}
    types, matched, missed, dropped, kits = [], [], [], set(), []
    for said, given in entries:
        given = [t for t in given if t not in PROTOCOL]              # the plugin's: never added or dropped here
        ok = [t for t in given if t in heard]
        kit = [t for t in given if t in relay]
        types += [t for t in [*ok, *kit] if t not in types]
        if kit:
            pollers = sorted({relay[t] for t in kit})
            kits.append(f"'{said}' -> {', '.join(kit)} (the kit's, not heard yet: {_and(pollers)} declares "
                        f"{'it' if len(kit) == 1 else 'them'} in [[publishes]])")
        fam = family(said, [*known, *given])
        prefix = f"{fam}." if fam else None

        def under(t: str, prefix=prefix) -> bool:
            return prefix is not None and t.startswith(prefix)
        plausible = list(dict.fromkeys([*(t for t in given if t not in heard and t not in relay),
                                        *(t for t in sorted(known) if under(t) and t not in heard and t not in PROTOCOL
                                          and t not in relay)]))
        dropped |= set(plausible)
        if ok:
            matched.append(f"'{said}' -> {', '.join(ok)}")
        if plausible:
            missed.append(f"'{said}': {', '.join(plausible)} never heard on the bus (plausible, but only announced, "
                          "declared or guessed): left out of receives")
        if fam and (other := [t for t in sorted(heard) if under(t) and t not in ok and t not in PROTOCOL]):
            missed.append(f"'{said}' names {fam}, heard as {', '.join(other)}: "
                          + ("none is in its types; add" if not ok else "not in its types; add") + " each it means to "
                          "owner_named's types, in this turn (another event carrying the same news does not stand for it)")
        if not ok and not kit and not plausible and not fam:
            missed.append(f"'{said}': no type matched; read_events lists the types heard, or ask the owner which they mean")
    lines = []
    if matched:
        lines.append("the owner's words matched, in receives (heard on the bus): " + "; ".join(matched))
    if kits:
        lines.append("kept in receives, a relay's declared types not heard yet (kept until heard): " + "; ".join(kits))
    if missed:
        lines.append("could not match: " + "; ".join(missed))
    return types, lines, dropped


def protocol_line(receives) -> str | None:
    """The floor's protocol types a machine receives, said apart from the owner's: the plugin's."""
    got = [t for t in receives or [] if t in PROTOCOL]
    return (f"the floor's protocol (the plugin's), not yours: {', '.join(got)}, in receives whether or not heard yet; "
            "the owner's words neither add nor drop them") if got else None
