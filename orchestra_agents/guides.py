"""The operator's guides, for the agents that configure the project: Orchestra's own and each
installed plugin's (``Plugin.guide``), as Orchestra collects them (``orchestra.guides.collect``, also
``GET /api/guides``). Together they are about 100 KB, so an agent first lists them, each with what it
is about, its size and how many sections (``list_guides``), then the one it needs: its contents first, then one section at a time
(``read_guide``). Read only; the text is the plugins', never interpreted here, except that the
agents' own is gated on the task system (``tasking_text.gate``: no Coordinator without it, T2d s13)."""

from __future__ import annotations

import re

from .answers import first
from .config import Project
from .store import StoreError
from .tasking_text import gate

SUMMARY_CHARS = 400
SECTION_CHARS = 16_000      # a piece of a guide in one answer: under answers.BOUND with the answer's JSON around it
_CACHE: dict[str, list[dict]] = {}     # project key -> the guides, once per process: what is installed does not change while it runs


def _own() -> list[dict]:
    from pathlib import Path
    p = Path(__file__).with_name("guide.md")
    return [{"name": "agents", "text": p.read_text(encoding="utf-8")}]


def collect(project: Project) -> list[dict]:
    """``[{name, text}]``, Orchestra's first; this package's own alone, with a note, when the
    installed Orchestra serves no guides."""
    if project.key in _CACHE:
        return _CACHE[project.key]
    try:
        from orchestra.guides import collect as orchestra_guides
    except ImportError:
        got = [{**g, "note": "the installed Orchestra serves no guides; only the agents' own is here"} for g in _own()]
    else:
        got = orchestra_guides(project.orchestra)
        if not any(g["name"] == "agents" for g in got):     # the plugin not loaded in this process's view
            got += _own()
    _CACHE[project.key] = got
    return got


def _text(g: dict) -> str:
    """A guide's text as it holds now: the agents' own with its task-system words kept or dropped."""
    text = g.get("text") or ""
    return gate(text) if g.get("name") == "agents" else text


HEADING = re.compile(r"^(#{2,3}) (.+)$", re.MULTILINE)


def sections(text: str) -> list[str]:
    return [m.group(2).strip() for m in HEADING.finditer(text)]


def _parts(text: str) -> list[dict]:
    """Each ``##`` or ``###`` section: its heading, level, where it starts and ends (a ``##`` takes its
    ``###`` with it) and its size in characters."""
    heads = list(HEADING.finditer(text))
    out = []
    for i, m in enumerate(heads):
        level = len(m.group(1))
        end = next((n.start() for n in heads[i + 1:] if len(n.group(1)) <= level), len(text))
        out.append({"heading": m.group(2).strip(), "level": level, "start": m.start(), "end": end, "chars": end - m.start()})
    return out


def contents(text: str) -> list[str]:
    """The guide's table of contents: each heading with its section's size, a ``###`` indented."""
    return [("  " if p["level"] == 3 else "") + f"{p['heading']} ({p['chars']:,} chars)" for p in _parts(text)]


def _piece(text: str, offset: int) -> tuple[str, int | None]:
    """At most SECTION_CHARS of ``text`` from ``offset``, ending at a line's end; and where the next
    piece starts (None when this one is the last)."""
    end = offset + SECTION_CHARS
    if end >= len(text):
        return text[offset:], None
    nl = text.rfind("\n", offset + SECTION_CHARS // 2, end)
    end = nl + 1 if nl >= 0 else end
    return text[offset:end], end


def summary(text: str) -> str:
    """The guide's first paragraph after its title."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip() and not p.lstrip().startswith("#")]
    first = " ".join(paras[0].split()) if paras else ""
    return first if len(first) <= SUMMARY_CHARS else first[:SUMMARY_CHARS].rsplit(" ", 1)[0] + " …"


def list_guides(project: Project) -> dict:
    out = []
    for g in collect(project):
        text = _text(g)
        row = {"name": g["name"], "chars": len(text), "sections": len(sections(text)), "about": summary(text)}
        if g.get("error") or g.get("note"):
            row["note"] = g.get("error") or g.get("note")
        out.append(row)
    return {"ok": True, "guides": out,
            "next": "read_guide with a name gives that guide's contents (each section's heading and size) and its first "
                    "section; then read_guide with a section for each part you need, before you change a setting it covers"}


def read_guide(project: Project, a: dict) -> dict:
    """Without ``section``: the guide's contents and its opening (the text before its second ``##``
    heading); with it, that section (a ``##`` with its ``###``). A piece is at most SECTION_CHARS: a
    longer one ends with where to read on (``offset``) and, for a ``##``, the subsections to ask for."""
    name, want = (a.get("name") or "").strip(), (a.get("section") or "").strip()
    offset = max(0, int(a.get("offset") or 0))
    guides = {g["name"]: g for g in collect(project)}
    if name not in guides:
        raise StoreError(f"no guide {name!r}; the guides are {', '.join(guides) or 'none'} (list_guides)")
    text = _text(guides[name])
    if not text:
        raise StoreError(guides[name].get("error") or f"the guide {name} is empty")
    parts = _parts(text)
    if not want:
        tops = [p for p in parts if p["level"] == 2]
        body, heading, subs = text[:tops[1]["start"] if len(tops) > 1 else len(text)], None, []
        out = {"ok": True, "name": name, "chars": len(text), "contents": contents(text)}
    else:
        hit = next((p for p in parts if p["heading"].lower() == want.lower()), None) or \
            next((p for p in parts if want.lower() in p["heading"].lower()), None)
        if hit is None:
            raise StoreError(f"the guide {name} has no section {want!r}; its sections: "
                             + "; ".join(first(sections(text), 60)))
        body, heading = text[hit["start"]:hit["end"]].strip(), hit["heading"]
        subs = [p for p in parts if hit["start"] < p["start"] < hit["end"]]
        out = {"ok": True, "name": name, "section": heading}
    if offset >= len(body) and body:
        raise StoreError(f"offset {offset:,} is past the end ({len(body):,} chars)")
    piece, more = _piece(body, offset)
    out["text"] = piece
    if offset or more is not None:
        out["chars"] = len(body) if want else out["chars"]
        out["shown"] = f"characters {offset:,} to {offset + len(piece):,} of {len(body):,}"
    if more is not None:
        ask = f"name {name!r}" + (f", section {heading!r}" if want else "")
        out["more"] = (f"{len(body) - more:,} more characters: read_guide with {ask} and offset {more}"
                       + (", or one of its subsections by its heading: " + "; ".join(
                           f"{p['heading']} ({p['chars']:,} chars)" for p in subs) if subs else ""))
    if not want:
        out["next"] = "read_guide with a section (its heading, or part of it) for each other part you need"
    return out
