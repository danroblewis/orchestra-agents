"""The owner's own name for a discovered stock, flow or effect (``name_part``; the owner,
2026-11-02: "We could allow the user to give their own custom name, and use the effective ID name
we have to associate it"). Orchestra shows a declared name on a discovered part once a declaration
is linked to it (its SPEC §4.6, ``sd.intended.display``): a declared stock or effect ``link``ed to
the stock's key, or a declared flow with a name of its own found by its two ends. This tool makes
that declaration, quoting the owner, and answers with what the Dynamics page will show.

- **The part is the one the owner pointed at**, by the name the page shows it by (the generated
  place name, "between write and sail_check", or a name lent already) and the tags that tell it
  apart ("held at write · workflow=hotfix"), or its key. When more than one part answers to the
  owner's words, nothing is declared: the answer is one question (which one), never a guess.
- **Or by the owner's words alone** (``shown`` left out; bootstrap run 3: asked for "the stock that
  matters", the Supervisor said it was not in the model when discovery's v1 held it as "in
  in_progress"): the name's words are matched to each discovered stock's shown name and tags
  ("tickets in flight" answers to "in in_progress · workflow=ticket_delivery" by ``ticket``, and
  ``flight`` reads as ``progress``); the best match is named, two equal ones are one question, and
  none is a refusal that lists the discovered stocks, so the answer never says a stock is missing
  without them.
- **The name is the owner's**: the call needs their quote, and the name must be in it. Nothing
  here makes one up. A stock declared already under that name and not yet linked is linked, never
  declared twice.
- **Renaming** declares the same element again with the new name (Orchestra revises an owner's
  quoted re-declaration at once); **dropping** the name retracts the declaration, when this tool
  made it. One that means more (a declared stock with its measurements) keeps its name: the answer
  says so.
- A flow is named only when it runs between two stocks and no other flow does: Orchestra matches a
  declared flow by its ends, so a flow from or to outside, or one of two between the same stocks,
  cannot carry a name yet.

The name is display only: it binds no series and changes no key, title or equation (Orchestra's
rule 7)."""

from __future__ import annotations

import re

from . import intended_api
from .config import Project
from .design import OWNER, _norm, answer, apply
from .store import AgentStore, StoreError

MARK = "the owner's name for the discovered "        # the note on a declaration this tool made
SEP = re.compile(r"\s+·\s+")


def _tags_of(x) -> list[str]:
    return [str(t) for t in (getattr(x, "evidence", None) or {}).get("tags") or []]


def shown_as(name: str, tags: list[str]) -> str:
    """How running text says a part: its name, then its tags ("held at write · workflow=hotfix")."""
    return " · ".join([name, *tags])


def _parts(model, keys, lent: dict) -> list[dict]:
    """Every stock and flow of the model, with the names it answers to."""
    out = []
    for part, group, items, of_id in (("stock", "stocks", model.stocks, keys.stock_of_id),
                                      ("flow", "flows", model.flows, keys.flow_of_id)):
        for x in items:
            given = (lent.get(group) or {}).get(x.id)
            out.append({"part": part, "id": x.id, "key": of_id.get(x.id), "title": x.name, "tags": _tags_of(x),
                        "lent": given, "x": x})
    return out


def _answers_to(p: dict, said: str) -> bool:
    names = {p["title"], p["key"], p["id"], *((p["lent"] or {}).get("name"),)}
    if p["part"] == "flow":
        names |= {n.replace("→", "->") for n in names if n} | {n.replace("->", "→") for n in names if n}
    return said in {_norm(n) for n in names if n}


def find(model, keys, lent: dict, shown: str, tags: list[str], part: str | None) -> list[dict]:
    """The parts that answer to ``shown`` (a name the page shows, with its tags after " · ", or a
    key) and carry every tag given; only stocks or only flows when ``part`` says which."""
    words = SEP.split((shown or "").strip())
    said, wanted = _norm(words[0]), {_norm(t) for t in [*words[1:], *tags] if t.strip()}
    if not said:
        raise StoreError("shown: the name the page shows the part by (between write and sail_check), or its key")
    hits = [p for p in _parts(model, keys, lent) if _answers_to(p, said) and wanted <= {_norm(t) for t in p["tags"]}]
    if part:
        hits = [p for p in hits if p["part"] == ("flow" if part == "flow" else "stock")]
    return hits


STOP = {"a", "an", "the", "in", "on", "at", "of", "to", "for", "and", "or", "is", "are", "our", "my", "your", "we", "that",
        "this", "it", "its", "stock", "flow", "effect", "call", "name", "matters", "most", "workflow", "step"}
SAME = {"flight": "progress", "wip": "progress", "active": "progress", "ongoing": "progress", "doing": "progress",
        "underway": "progress", "queue": "waiting", "queued": "waiting", "pending": "waiting", "backlog": "waiting",
        "finished": "done", "completed": "done", "shipped": "done", "closed": "done"}


def words_of(text: str) -> set[str]:
    """The words that tell a part apart: lower case, split on anything not a letter or digit, a plural's
    s taken off, common words left out, and a few everyday words read as the step they mean."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", _norm(text)):
        w = w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
        if w not in STOP:
            out.add(SAME.get(w, w))
    return out


def by_words(model, keys, lent: dict, said: str, part: str | None) -> list[dict]:
    """The stocks (or flows, when ``part`` says) whose shown name and tags share the most words with
    the owner's: every one tied for the best, none when no word is shared."""
    want = words_of(said)
    pool = [p for p in _parts(model, keys, lent) if p["part"] == ("flow" if part == "flow" else "stock")]
    scored = [(len(want & words_of(" ".join([p["title"], *p["tags"]]))), p) for p in pool]
    best = max((n for n, _ in scored), default=0)
    return [p for n, p in scored if best and n == best]


def _which(hits: list[dict]) -> str:
    """One sentence asking which of the parts the owner means, each by what tells it apart."""
    said = [f"{'the flow' if p['part'] == 'flow' else 'the stock'} {shown_as(p['title'], p['tags'])}"
            + (f" (key {p['key']})" if len({shown_as(h['title'], h['tags']) for h in hits}) < len(hits) else "")
            for p in hits]
    return "Which one do you mean: " + ", or ".join(said) + "?"


def _near(model, keys, lent: dict, shown: str) -> list[str]:
    ws = set(re.findall(r"[a-z0-9_]+", _norm(SEP.split(shown.strip())[0])))
    return [shown_as(p["title"], p["tags"]) for p in _parts(model, keys, lent)
            if ws & set(re.findall(r"[a-z0-9_]+", _norm(p["title"])))][:8]


def _free_id(layer: dict, name: str) -> str:
    from orchestra.sd.intended.checks import make_slug
    base = make_slug(name)[:70]
    taken = {*(layer.get("stocks") or {}), *(layer.get("effects") or {}), *(layer.get("flows") or {})}
    eid, n = base, 1
    while eid in taken:
        n += 1
        eid = f"{base}_{n}"
    return eid


def _ends(keys, flow) -> tuple[str | None, str | None]:
    return (keys.stock_of_id.get(flow.src) if flow.src else None, keys.stock_of_id.get(flow.dst) if flow.dst else None)


def _flow_ops(layer: dict, keys, p: dict, name: str, mark: str) -> tuple[list[dict], str]:
    a, b = _ends(keys, p["x"])
    if a is None or b is None:
        raise StoreError(f"the flow {shown_as(p['title'], p['tags'])} runs {'from' if a is None else 'to'} outside, and "
                         "Orchestra matches a declared flow by its two stocks: it cannot carry a name yet. Nothing was declared")
    same = [f for f in keys.model.flows if _ends(keys, f) == (a, b)]
    if len(same) > 1:
        raise StoreError(f"{len(same)} flows run between the same two stocks, and Orchestra matches a declared flow by its "
                         "ends: none of them can carry a name yet. Nothing was declared")
    eid = _free_id(layer, name)
    return [{"op": "declare_flow", "id": eid, "from": a, "to": b, "name": name, "note": mark}], eid


def _quoted(prov: dict) -> str:
    if prov.get("author") != OWNER:
        raise StoreError("a part's name is the owner's: quote their words (quote), the name among them. Nothing was declared")
    return prov["quote"]


def name_part(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    from orchestra.sd.intended.keys import Keys
    quote = _quoted(prov)
    part = a.get("part") or None
    if part not in (None, "stock", "effect", "flow"):
        raise StoreError("part is stock, effect or flow (leave it out when the owner's words do not say)")
    drop = bool(a.get("drop"))
    name = " ".join((a.get("name") or "").split())
    if not drop and not name:
        raise StoreError("name: the owner's name for the part, exactly in their words (or drop=true to take a name off)")
    if name and _norm(name) not in _norm(quote):
        raise StoreError(f"the name {name!r} is not in the owner's words you quoted: name a part only with the name they "
                         "gave, never one you made. Nothing was declared")
    model = intended_api.current_model(project)
    if model is None:
        raise StoreError("there is no model yet (no discovery saved): nothing to name. Nothing was declared")
    keys = Keys(model)
    lay = intended_api.layer(project)
    layer = lay.read()["layer"]
    lent = intended_api.declared_names(layer, keys)
    shown = (a.get("shown") or "").strip()
    tags = [str(t) for t in a.get("tags") or []]
    if not shown and not drop:                               # the owner's words alone: matched to what discovery found
        hits = by_words(model, keys, lent, " ".join([name, *tags]), part)
        if not hits:
            stocks = [shown_as(p["title"], p["tags"]) for p in _parts(model, keys, lent) if p["part"] == "stock"]
            raise StoreError(f"no discovered stock answers to {name!r} by its shown name or tags; the model's stocks now: "
                             + ("; ".join(stocks) if stocks else "none") + ". Tell the owner so, listing them, and ask which "
                             "they mean (or name it by what the page shows: shown). Nothing was declared")
    else:
        hits = find(model, keys, lent, shown, tags, part)
    if not hits:
        near = _near(model, keys, lent, shown) if shown else []
        raise StoreError(f"no {'flow' if part == 'flow' else 'stock or flow' if part is None else 'stock'} is shown as "
                         f"{shown!r}" + (f" with {', '.join(tags)}" if tags else "") + " in the model now"
                         + (f"; the page shows {'; '.join(near)}" if near else "") + ". Nothing was declared")
    if len(hits) > 1:
        q = _which(hits)
        return {"ok": True, "asked": q, "changes": [], "candidates": [{k: p[k] for k in ("part", "key", "title", "tags")}
                                                                       for p in hits],
                "said": [q, "nothing was declared: ask the owner this, in one sentence, and call again with their answer"]}
    p = hits[0]
    title = shown_as(p["title"], p["tags"])
    old = p["lent"]
    if drop:
        return _drop(project, layer, p, old, title, prov)
    if old is not None:                                      # renaming: the same declaration, a new name
        el = (layer.get(f"{old['part']}s") or {}).get(old["id"]) or {}
        if old["name"] == name:
            return {"ok": True, "changes": [], "said": [f"{name}, over '{title}': already its name, nothing changed"]}
        op = ({"op": "declare_flow", "id": old["id"], "from": el.get("from"), "to": el.get("to"), "name": name}
              if old["part"] == "flow" else {"op": f"declare_{old['part']}", "id": old["id"], "name": name})
        done = apply(project, [op], prov)
        return answer(project, done, [f"{name}, over '{title}', named by you (was {old['name']})",
                                      "the Dynamics page shows it by that name, the generated one beneath it"],
                      id=old["id"], part=old["part"], key=p["key"], shown=title)
    mark = f"{MARK}{p['part']} {p['key']}"
    if p["part"] == "flow":
        ops, eid = _flow_ops(layer, keys, p, name, mark)
        decl = "flow"
    else:
        decl = "effect" if part == "effect" else "stock"
        had = _declared_unlinked(layer, f"{decl}s", name)
        eid = had or _free_id(layer, name)
        ops = ([] if had else [{"op": f"declare_{decl}", "id": eid, "name": name, "note": mark}]) + [
            {"op": "link", "part": decl, "id": eid, "found_as": p["key"]}]
    done = apply(project, ops, prov)
    return answer(project, done, [f"{name}, over '{title}', named by you",
                                  "the Dynamics page shows it by that name, the generated one beneath it as its identifier"],
                  id=eid, part=decl, key=p["key"], shown=title)


def _plain(s: str) -> str:
    return re.sub(r"[_\s]+", " ", _norm(s)).strip()


def _declared_unlinked(layer: dict, group: str, name: str) -> str | None:
    """A declared element of that name not linked to anything yet (the owner's earlier declaration)."""
    return next((k for k, el in (layer.get(group) or {}).items()
                 if _plain(el.get("name") or k) == _plain(name) and not el.get("found_as")
                 and el.get("state") not in ("retracted", "discarded")), None)


def _drop(project: Project, layer: dict, p: dict, old: dict | None, title: str, prov: dict) -> dict:
    if old is None:
        return {"ok": True, "changes": [], "said": [f"'{title}' carries no name of yours: nothing to drop"]}
    el = (layer.get(f"{old['part']}s") or {}).get(old["id"]) or {}
    if not str(el.get("note") or "").startswith(MARK):
        raise StoreError(f"'{title}' is shown as {old['name']} because it is the declared {old['part']} {old['id']} (linked to "
                         "it), which is more than a name: dropping it would retract that declaration. Rename it (name_part "
                         "with a new name), or take the link off (link unlink=true), as the owner says. Nothing was changed")
    done = apply(project, [{"op": "retract", "part": old["part"], "id": old["id"]}], prov)
    said = f"'{title}' no longer carries the name {old['name']}: the Dynamics page shows the generated name again"
    return answer(project, done, [said], id=old["id"], part=old["part"], key=p["key"], shown=title)


COMMANDS = {"name_part": name_part}
