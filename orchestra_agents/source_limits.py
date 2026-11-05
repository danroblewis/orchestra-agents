"""The owner's limits on another store, which must be given before any agents tool reads it.

Bootstrap run 6 (2026-11-03): told only "read our Prometheus", the Supervisor read it at once with
a scope it chose from the code (the three services' ``job``) and a series cap of its own, before
the owner had said "only our namespace hackriff, at most 200 series". So a tool that reads a store's
series (``source_schema``, ``preview_query``, ``infer_source``, ``find_balance``) and declaring a
store's ``match`` (``set_setting`` key ``sources``) are refused until one of these holds:

- the store has a ``[[sources]]`` entry with a ``match`` (the owner's scope, declared), or
- an owner's message this session gave limits: a scope (a namespace, a job, a selector or a label
  matcher) and a series cap ("at most 200 series", ``max_series``), or said to use the defaults.

``check_sources`` is not refused: it asks whether the store answers, and reads no series. The
words are matched loosely, across the session's messages, and not tied to one store: a message
that gave one store's limits counts for another. A refusal says why and the one question to ask.

On a turn with no owner's message (a wake-up, such as an explore round), ``check_scopes`` holds the
explore loop's standing rule: a declared source's ``match`` may narrow and its ``split``, ``kinds``
and ``names`` change, but its ``match`` never widens and its ``explore`` (the owner's alone) is
never written; those wait for the owner's yes in a turn of theirs. Under ``[agents] ask = "outside"``
(the default, ``ask.py``) a ``match`` may also widen, in any turn, as far as the source's ``explore``
and no further: ``explore`` is the owner's scope for exactly that (Orchestra's guide, "Exploring
beyond the match"), and it is still never written here. A source with no ``explore`` widens only on
the owner's words; a match-everything selector and the store's rate and daily caps are Orchestra's
refusals (``orchestra.sources``), in every mode."""

from __future__ import annotations

import re

from .store import AgentStore, StoreError

READS = frozenset({"source_schema", "preview_query", "infer_source", "find_balance"})

SCOPE = re.compile(r"\bnamespaces?\b|\bjobs?\b|\bselectors?\b|\blabels?\b|\{[^}]*[A-Za-z_]\w*\s*(=~|!~|!=|=)\s*\"", re.IGNORECASE)
CAP = re.compile(r"\b\d[\d,]*\s+(time[- ])?series\b|\bmax_series\b|\bseries\s+(cap|limit)\b", re.IGNORECASE)
DEFAULTS = re.compile(r"\b(use|take|keep|with|go with)\s+(the\s+|its\s+|your\s+)?defaults?\b|\bdefault\s+limits\b", re.IGNORECASE)

ASK = ("Which part of the store may I read (a namespace or a selector), and at most how many series, or should I use "
       "the defaults?")


def gave_limits(messages: list[str]) -> bool:
    """Whether the owner's messages gave a store's limits (a scope and a series cap) or said to use defaults."""
    text = "\n".join(m for m in messages if m)
    return bool(DEFAULTS.search(text) or (SCOPE.search(text) and CAP.search(text)))


def _messages(store: AgentStore | None) -> list[str]:
    if store is None:
        return []
    from .design import said_in_session, turn_of
    return [r.get("message") or "" for r in said_in_session(store)] + [turn_of(store).get("message") or ""]


def _url(u: str) -> str:
    return (u or "").strip().rstrip("/")


def _bounded(declared, ref) -> bool:
    """Whether a store the call names (a source's name, a url, or ``{"url": ...}``) has a declared match."""
    from .source_bounds import bound_of
    if isinstance(ref, dict):
        ref = ref.get("url") or ref.get("name") or ref.get("source") or ""
    ref = str(ref or "").strip()
    return any(bound_of(s) and (s.name == ref or _url(getattr(s, "url", "")) == _url(ref)) for s in declared)


def _named(command: str, a: dict, declared) -> list:
    if command == "find_balance":
        refs = [a["url"] if a.get("url") else a.get("source")] + list(a.get("sources") or [])
        refs = [r for r in refs if r] or [s.name for s in declared]
        return refs
    return [r for r in (a.get("url"), a.get("source")) if r]      # neither: the project's own store, not another's


def check(project, store: AgentStore | None, command: str, a: dict) -> None:
    """Refuse a read of a store whose limits the owner has not given (module docstring)."""
    if command not in READS:
        return
    from orchestra.sources.spec import for_project
    declared = for_project(project.orchestra).sources
    unbounded = [r for r in _named(command, a or {}, declared) if not _bounded(declared, r)]
    if not unbounded or gave_limits(_messages(store)):
        return
    raise StoreError(refusal(", ".join(str(r.get("url") if isinstance(r, dict) else r) for r in unbounded)))


def within_explore(project, match: list[str], explore: list[str] | None) -> bool:
    """Whether a widened ``match`` stays inside the source's ``explore``, under ``ask = "outside"`` only:
    then the Supervisor widens it as its own decision (``ask.py``); never past ``explore``."""
    from .ask import acts
    if not acts(project) or not explore or not match:
        return False
    from orchestra.sources.within import outside
    return not outside(match, explore)


def check_declared(project, store: AgentStore | None, value) -> None:
    """Refuse declaring a ``[[sources]]`` match the owner's words did not give (a scope picked by the
    Supervisor, which would then pass ``check``); an entry whose match is already declared is kept."""
    from orchestra.sources.within import outside
    raw = project.orchestra.config.raw.get("sources") or []
    had = {str(e.get("name")): e.get("match") for e in raw if isinstance(e, dict)}

    explores = {str(e.get("name")): _scopes(e)[1] for e in raw if isinstance(e, dict)}

    def narrowed(e: dict) -> bool:                # within the match the owner gave: the explore loop's standing rule
        old = had.get(str(e.get("name")))
        return bool(old) and (not outside(_scopes(e)[0], _scopes({"match": old})[0])
                              or within_explore(project, _scopes(e)[0], explores.get(str(e.get("name")))))
    new = [e for e in (value if isinstance(value, list) else [])
           if isinstance(e, dict) and e.get("match") and e.get("match") != had.get(str(e.get("name"))) and not narrowed(e)]
    if new and not gave_limits(_messages(store)):
        raise StoreError(refusal(", ".join(str(e.get("name") or e.get("url")) for e in new), declaring=True))


def refusal(what: str, declaring: bool = False) -> str:
    act = "declaring a match for it" if declaring else "reading it"
    return (f"{what}: the owner has not given this store's limits (a namespace or selector, and a series cap) nor said to "
            f"use the defaults, so {act} is refused: a scope chosen from the code is not theirs, and a general go-ahead "
            f"is not their limits. Ask them in one sentence (\"{ASK}\"); check_sources may still say whether it answers. "
            "Nothing was read")


def _scopes(entry: dict) -> tuple[list[str], list[str] | None]:
    m = entry.get("match")
    e = entry.get("explore")
    return ([m] if isinstance(m, str) else list(m or [])), (None if e is None else [e] if isinstance(e, str) else list(e))


def check_scopes(project, store: AgentStore | None, value) -> None:
    """The explore loop's standing rule, held in code (Orchestra's guide, "Exploring beyond the match":
    nothing in the core stops a plugin's setting tool from writing ``explore``). On a turn with no
    owner's message (a wake-up: an explore round, a duty), a ``[[sources]]`` change may narrow a
    source's ``match`` and change its ``split``, ``kinds`` and ``names``, but never widen its ``match``
    (a selector outside every one it had; under ``ask = "outside"``, outside its ``explore``) nor write
    its ``explore``, which is the owner's alone: those are asked in one sentence, and made in the turn
    of their yes."""
    if store is None or not isinstance(value, list):
        return
    from .design import turn_of
    if (turn_of(store).get("message") or "").strip():
        return                                                   # the owner's turn: their words carry it (check_declared)
    from orchestra.sources.within import outside
    had = {str(e.get("name")): e for e in project.orchestra.config.raw.get("sources") or [] if isinstance(e, dict)}
    wider, explored = [], []
    for e in value:
        if not isinstance(e, dict):
            continue
        old = had.get(str(e.get("name")))
        if old is None:
            continue                                             # a new store: check_declared holds it to the owner's limits
        (new_match, new_explore), (old_match, old_explore) = _scopes(e), _scopes(old)
        if new_explore != old_explore:
            explored.append(str(e.get("name")))
        if new_match and old_match and outside(new_match, old_match) and not within_explore(project, new_match, old_explore):
            wider.append(str(e.get("name")))
    if explored or wider:
        why = "; ".join(x for x in (
            f"its explore ({', '.join(explored)}) is the owner's alone: never written or widened by you" if explored else "",
            f"its match widens ({', '.join(wider)}): a selector outside every one it had" if wider else "") if x)
        from .ask import acts
        then = ("widening match as far as its explore applies too ([agents] ask = \"outside\"); past explore it is the "
                "owner's words, in a turn of theirs" if acts(project) else
                "ask the owner in one sentence for this one (the change and its expected effect), and make it in the turn of "
                "their yes")
        raise StoreError(f"refused on a turn with no owner's message, so nothing was written: {why}. Narrowing match and "
                         f"changing split, kinds or names apply on the standing rule; {then}")
