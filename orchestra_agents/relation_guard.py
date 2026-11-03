"""A workflow's relations are the owner's goal: ``declare_workflow`` never drops one on the
Supervisor's own judgement (bootstrap run 5, step 8).

The owner's goal was "each ticket from assignment to Done as its pull requests are reviewed,
labelled SAIL, and come back SAILED or REJECTED": the ticket's steps waited on its pull requests'
events (``of: children``). No pull request was ever made a ticket's child (nothing published
``item.related``), so the tickets waited at ``open_pr``. The Supervisor re-declared the workflow to
follow Jira's status only, with a false reason ("each carrying the ticket key"): the visible gap
became an invisible one, and the goal was gone. The waiting was the true signal: the data lacks the
link, and the fix is the small program that makes it (a linker reading the key from each pull
request's branch or title and publishing ``item.related {kind: parent}``, the Workflows plugin's
SPEC §2.2), asked for or proposed in one sentence.

So a re-declaration that drops a relation the definition in place holds (a step's ``of: children``
or ``of: parent``, a ``depends``) is refused unless the owner's quoted words say so, in words about
the link ("follow Jira's status only", "drop the pull request link"): a go-ahead such as "proceed
as you think best" is not that."""

from __future__ import annotations

import json
import re

RELATION_WORDS = re.compile(r"\b(link\w*|relat\w*|pull requests?|prs?|child\w*|parent\w*|without|status only|"
                            r"drop\w*|depend\w*)\b", re.IGNORECASE)


def relations(definition) -> set[str]:
    """What a definition relates its items by: each ``of`` (with the event it waits for) and each
    ``depends``, anywhere in its steps."""
    out: set[str] = set()

    def walk(x) -> None:
        if isinstance(x, dict):
            if isinstance(x.get("of"), str):
                out.add(f"{x.get('event') or 'a count'} of its {x['of']}")
            if x.get("depends"):
                out.add(f"depends on {json.dumps(x['depends'], sort_keys=True)}")
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk((definition or {}).get("steps") if isinstance(definition, dict) else None)
    return out


def current(p, o, name: str) -> dict | None:
    """The definition in place under ``name`` (the first root that holds it, as the loader reads them)."""
    for _, root in p.roots(o):
        f = root / "workflows" / str(name) / "workflow.json"
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def check(before: dict | None, after: dict, prov: dict, owner: str) -> None:
    """Refuse a re-declaration that drops a relation ``before`` holds, unless the owner's quote is
    about the link. Raises ``ValueError`` with what to do instead."""
    dropped = sorted(relations(before) - relations(after))
    if not dropped:
        return
    quote = prov.get("quote") or ""
    if prov.get("author") == owner and RELATION_WORDS.search(quote):
        return
    raise ValueError(
        f"declare_workflow refused, nothing written: the workflow {after.get('name')} in place relates its items "
        f"({'; '.join(dropped)}) and this definition drops that. The relation is the owner's goal: when items wait at "
        "such a step, the data lacks the link (nothing publishes item.related), not the definition. Keep the definition, "
        "so the gap stays visible (list_items shows who waits), and ask the owner in one sentence for the small program "
        "that makes the link, or propose it: a linker machine that reads each item's key (a ticket's key from a pull "
        "request's branch or title) and publishes item.related {kind: parent}, the Workflows plugin's relating. Drop the "
        "relation only on the owner's quoted words saying so (about the link, not a general go-ahead), and never claim "
        "a field the items do not carry: explain_item shows what they hold")
