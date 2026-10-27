"""Edits to the text of ``orchestra.toml`` that keep everything they do not change: a key set in a
table, an entry of an array of tables (``[[machines]]``, ``[[gate.unit]]``) added, changed or
removed. Keys and entries are set through ``tomlkit``, which keeps the file's comments and layout;
a key whose value changes loses its own end-of-line comment, which described the old value. An
entry is removed from the text itself, its header through its last key, so the comments and blank
lines after it, which introduce what follows, stay where they are; the result is checked against
the parsed file with only that entry gone."""

from __future__ import annotations

import re
import tomllib
from typing import Any

import tomlkit
from tomlkit.items import AoT
from tomlkit.toml_document import TOMLDocument

from .store import StoreError


def table(doc: TOMLDocument, key: str):
    """The top-level table ``key``, added at the end when there is none."""
    if key not in doc:
        t = tomlkit.table()
        t.trivia.indent = "\n"
        doc[key] = t
    return doc[key]


def set_key(tbl, key: str, value: Any) -> None:
    if key in tbl and tbl[key] == value:
        return
    tbl[key] = value
    tbl.item(key).trivia.comment = ""
    tbl.item(key).trivia.comment_ws = ""


def entries(doc: TOMLDocument, section: str, create: bool = False):
    """The array of tables ``[[section]]`` (``gate.unit``: ``unit`` under ``[gate]``), or None.
    Raises StoreError when the key holds something else (``[gate]`` with ``unit = "make test"``):
    an entry cannot be added to it or found in it."""
    holder, key = (doc.get("gate"), "unit") if section == "gate.unit" else (doc, section)
    if holder is not None and not isinstance(holder, dict):
        raise StoreError(f"{section.split('.')[0]} is not a table; write [{section.split('.')[0]}] with [[{section}]] entries")
    if holder is None or key not in holder:
        if not create:
            return None
        holder = table(doc, "gate") if section == "gate.unit" else doc
        holder[key] = tomlkit.aot()
    if not isinstance(holder[key], AoT):
        where = "[gate] unit" if section == "gate.unit" else section
        raise StoreError(f"{where} is not an array of tables; write each {'test' if section == 'gate.unit' else 'entry'} "
                         f"as [[{section}]]")
    return holder[key]


def find(items, name: str) -> int | None:
    return next((i for i, e in enumerate(items or []) if isinstance(e, dict) and e.get("name") == name), None)


def upsert(doc: TOMLDocument, section: str, name: str, fields: dict[str, Any]) -> dict | None:
    """Set ``fields`` on the ``[[section]]`` entry named ``name``, adding it (after a blank line)
    when there is none; fields not given are left as they were. Returns the entry as it was, or
    None when it was added."""
    items = entries(doc, section, create=True)
    i = find(items, name)
    if i is None:
        t = tomlkit.table()
        t.trivia.indent = "\n"
        t["name"] = name
        for k, v in fields.items():
            t[k] = v
        items.append(t)
        return None
    before = dict(items[i])
    for k, v in fields.items():
        set_key(items[i], k, v)
    return before


def _parsed_entries(raw: dict, section: str) -> list:
    return list(((raw.get("gate") or {}).get("unit") if section == "gate.unit" else raw.get(section)) or [])


def remove(text: str, section: str, name: str) -> str:
    """The text without the ``[[section]]`` entry named ``name``. Raises StoreError when there is none."""
    raw = tomllib.loads(text)
    items = _parsed_entries(raw, section)
    i = find(items, name)
    if i is None:
        raise StoreError(f"there is no [[{section}]] entry named {name!r}")
    rest = items[:i] + items[i + 1:]                            # the file as it should parse: an emptied array is gone
    holder = {**raw["gate"]} if section == "gate.unit" else raw
    want = {k: v for k, v in holder.items() if k != section.split(".")[-1]} | ({section.split(".")[-1]: rest} if rest else {})
    if section == "gate.unit":
        want = {**raw, "gate": want}
    lines = text.splitlines(keepends=True)
    pattern = r"\s*\.\s*".join(map(re.escape, section.split(".")))
    mine = [k for k, ln in enumerate(lines) if re.match(rf"\s*\[\[\s*{pattern}\s*\]\]\s*(#.*)?$", ln)]
    if len(mine) == len(items):
        start = mine[i]
        nxt = next((k for k in range(start + 1, len(lines)) if lines[k].lstrip().startswith("[")), len(lines))
        end = max(k for k in range(start, nxt) if lines[k].strip() and not lines[k].lstrip().startswith("#"))
        out = lines[:start] + lines[end + 1:]
        if start > 0 and not out[start - 1].strip() and (start == len(out) or not out[start].strip()):
            del out[start - 1]                                  # no doubled blank line where it was
        new = "".join(out)
        try:
            if tomllib.loads(new) == want:
                return new
        except tomllib.TOMLDecodeError:
            pass
    doc = tomlkit.parse(text)                                   # an unusual layout: let tomlkit remove it
    del entries(doc, section)[i]
    return tomlkit.dumps(doc)
