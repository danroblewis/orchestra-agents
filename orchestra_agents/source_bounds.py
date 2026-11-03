"""A store's bound, which every agents tool that lists, counts or reads another store's series stays
inside: the source's ``match`` as ``[[sources]]`` declares it (the owner's scope, e.g.
``{namespace="hackriff"}``), and its ``max_series`` (the owner's cap on what one listing returns).

Run 4 (2026-11-03): the owner said "only our namespace hackriff, at most 200 series; never ask it
for the list of every metric", the source said so, and ``find_balance`` and ``source_schema`` still
listed all 337 names of the shared store twice and listed 327 and 351 series of one name across
every namespace. So here:

- every selector a tool sends is ANDed with the bound (``within``): its matchers added inside the
  selector's braces; a bound of several selectors is their union (``or``);
- a store with no bound (``match`` absent or the match-everything default, ``{__name__=~".+"}``)
  is never asked for every metric name: the tool refuses, says why and what to give (``Unbounded``);
- a listing asks for at most ``max_series`` series (``cap``).

Run 5 (2026-11-03): three bare ``/api/v1/metadata`` calls still listed every metric name of the store
(Orchestra's ``metadata`` asks for the whole store's when given more than a few names), and 27
queries a tool sent on a store given by ``url`` named only the bound passed (``job``), not the
namespace the declared source of that url keeps. So here too:

- metadata is asked name by name, each a ``metric`` and ``limit_per_metric=1``, for at most
  ``METADATA_NAMES`` names (``metadata``): never without a name, whatever the count;
- a store given by url that a ``[[sources]]`` entry declares is read within that entry's ``match``
  (``trial_bound``: the given bound ANDed into it); one no entry declares is read within the
  ``bound`` given, and is listed whole never (``unbounded``)."""

from __future__ import annotations

import re
from collections.abc import Callable

from .store import StoreError


class Unbounded(StoreError):
    """A listing of every metric name of a store that has no bound: refused."""


def everything(match: list[str] | None) -> bool:
    from orchestra.sources.listing import everything as core_everything
    return not match or core_everything(list(match))


def bound_of(spec) -> list[str] | None:
    """The source's bound (its ``match``), or None when it selects everything."""
    match = list(getattr(spec, "match", None) or [])
    return None if everything(match) else match


def cap(spec, limit: int | None = None) -> int:
    """At most this many series in one listing: the source's ``max_series``, or ``limit`` when lower."""
    most = int(getattr(spec, "max_series", 0) or 0) or (limit or 0)
    return min(most, limit) if limit else most


def _inner(selector: str) -> str:
    return selector.partition("{")[2].rpartition("}")[0].strip().strip(",").strip()


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def within(selector: str, bound: str | None) -> str:
    """``selector`` ANDed with ``bound``'s matchers (added first inside its braces; nothing added
    when the selector has them already, or the bound selects everything)."""
    if not bound or everything([bound]):
        return selector
    add = _inner(bound)
    if not add:
        return selector
    s = selector.strip()
    if "{" not in s:
        return f"{s}{{{add}}}"
    head, _, rest = s.partition("{")
    body, _, tail = rest.rpartition("}")
    body = body.strip().strip(",").strip()
    if _squash(add) in _squash(body):
        return s
    return f"{head}{{{add}{',' + body if body else ''}}}{tail}"


def selectors(bound: list[str] | None, given: list[str]) -> list[str]:
    """Each given selector within each of the bound's (their union)."""
    if not bound:
        return list(given)
    return list(dict.fromkeys(within(g, b) for g in given for b in bound))


def expr(bound: list[str] | None, fn: Callable[[str], str], selector: str) -> str:
    """``fn`` of the selector within the bound: ``fn(s)`` for one bound selector, the union
    ``(fn(s1)) or (fn(s2))`` for several."""
    sels = selectors(bound, [selector])
    return fn(sels[0]) if len(sels) == 1 else " or ".join(f"({fn(s)})" for s in sels)


def holds(bound: list[str] | None, query: str) -> bool:
    """Whether a PromQL query names the matchers of one of the bound's selectors (a preview the
    owner's scope covers); True with no bound."""
    if not bound:
        return True
    q = _squash(query)
    for b in bound:
        parts = [p for p in re.findall(r'[A-Za-z_][A-Za-z0-9_]*\s*(?:=~|!~|!=|=)\s*"(?:[^"\\]|\\.)*"', _inner(b))]
        if parts and all(_squash(p) in q for p in parts):
            return True
    return False


def unbounded(name: str, what: str) -> Unbounded:
    return Unbounded(f"{name} has no bound (its [[sources]] match selects every metric), so {what} would ask the store for "
                     "every metric name it holds, which is never done: declare the owner's scope as the source's match "
                     "(e.g. {namespace=\"team\"}, with max_series), or, for a store given by url, pass it as bound")


METADATA_NAMES = 40      # the most names one tool asks the store's metadata of (one request each); the rest are typed by name


def metadata(src, names, most: int = METADATA_NAMES) -> dict[str, dict]:
    """The store's metadata of at most ``most`` of ``names`` (in their order), asked one name per
    call so that each request names its metric: Orchestra's ``metadata`` asks for the whole store's
    when given more than a few, and that is never done. Names past ``most`` are left out (the
    tools then guess their type from the name)."""
    out: dict[str, dict] = {}
    for n in list(dict.fromkeys(x for x in names or [] if x))[:max(0, most)]:
        out.update(src.metadata([n]))
    return out


def _url(u: str) -> str:
    return (u or "").strip().rstrip("/")


def trial_bound(declared, url: str, given) -> list[str] | None:
    """The bound of a store given by ``url``: the given ``bound`` (a selector or a list) within the
    ``match`` of a declared source that reads the same url (the configured scope holds for every
    read of that store, run 5), or the given bound alone when no source declares it."""
    mine = [given] if isinstance(given, str) else list(given or [])
    decl = next((bound_of(s) for s in declared if _url(getattr(s, "url", "")) == _url(url) and bound_of(s)), None)
    if not decl:
        return mine or None
    return list(dict.fromkeys(within(g, d) for d in decl for g in mine)) if mine else list(decl)

