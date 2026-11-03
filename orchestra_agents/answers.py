"""How big a tool's answer may be, set once here: every answer a model reads (through the MCP server,
`orchestra agents tool` or the Pi extension, which all answer through ``harness/caller.Caller``) is at
most ``BOUND`` characters. Claude Code keeps an answer larger than about 30,000 characters out of the
context and spools it to a file the model then parses with its shell (bootstrap run 1, 2026-11-02:
seven guide sections in one command, 34 KB, spooled, read back, and the context thrashed); an answer
under the bound is never spooled.

A tool that lists (series, events, processes, files) gives the count and its first ``LISTED`` with
"and N more" (``first``); a guide is read by its contents and one section at a time
(``guides.py``). What is still over the bound is cut here (``cut``) with a line saying how much was
left out and how to ask for the rest: a read tool takes ``page`` (the same call again, the next
page); a tool that changed something is never called again for its answer, so its line says the
change is done and only the answer was cut."""

from __future__ import annotations

from collections.abc import Iterable

BOUND = 24_000            # characters: the most any tool answer holds, its cut line included
NOTE_ROOM = 400           # kept for the cut line
LISTED = 40               # a list in an answer: its first this many, then "and N more"


def first(items: Iterable, n: int = LISTED, hint: str = "") -> list:
    """The first ``n`` items; when there are more, a last item saying how many more and how to see them."""
    items = list(items)
    if len(items) <= n:
        return items
    return items[:n] + [f"… and {len(items) - n} more" + (f" ({hint})" if hint else "")]


def pages(text: str, size: int = BOUND - NOTE_ROOM) -> list[str]:
    """The text in pages of at most ``size`` characters, each ending at a line's end when one is in
    its second half."""
    out, i = [], 0
    while i < len(text):
        j = min(len(text), i + size)
        if j < len(text):
            nl = text.rfind("\n", i + size // 2, j)
            j = nl + 1 if nl >= 0 else j
        out.append(text[i:j])
        i = j
    return out or [""]


def cut(text: str, tool: str, page: int = 1, pageable: bool = True) -> str:
    """The answer as the model reads it: whole when it is under the bound; otherwise the page asked
    for, with a last line saying what was left out and how to read the rest."""
    if len(text) <= BOUND and page == 1:
        return text
    parts = pages(text)
    if page < 1 or page > len(parts):
        raise ValueError(f"{tool}'s answer has {len(parts)} page{'s' if len(parts) != 1 else ''}; there is no page {page}")
    if not pageable:
        return (parts[0] + f"\n[answer cut at {BOUND:,} characters: {len(text) - len(parts[0]):,} more left out. The call is "
                "done; its answer is not paged: a read tool gives the detail.]")
    left = len(text) - sum(len(p) for p in parts[:page])
    if page == len(parts):
        return parts[-1] + f"\n[answer cut at {BOUND:,} characters: page {page} of {len(parts)}, the last.]"
    return parts[page - 1] + (f"\n[answer cut at {BOUND:,} characters: page {page} of {len(parts)}; {left:,} more "
                              f"characters left out. For the rest, call {tool} again with the same arguments and "
                              f"page: {page + 1}.]")
