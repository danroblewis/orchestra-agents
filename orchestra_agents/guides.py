"""The operator's guides, for the agents that configure the project: Orchestra's own and each
installed plugin's (``Plugin.guide``), as Orchestra collects them (``orchestra.guides.collect``, also
``GET /api/guides``). Together they are about 100 KB, so an agent first lists them, each with what it
is about and its sections (``list_guides``), and then reads the one it needs, whole or one section
(``read_guide``). Read only; the text is the plugins', never interpreted here."""

from __future__ import annotations

import re

from .config import Project
from .store import StoreError

SUMMARY_CHARS = 400
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


def sections(text: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"^#{2,3} (.+)$", text, re.MULTILINE)]


def summary(text: str) -> str:
    """The guide's first paragraph after its title."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip() and not p.lstrip().startswith("#")]
    first = " ".join(paras[0].split()) if paras else ""
    return first if len(first) <= SUMMARY_CHARS else first[:SUMMARY_CHARS].rsplit(" ", 1)[0] + " …"


def list_guides(project: Project) -> dict:
    out = []
    for g in collect(project):
        row = {"name": g["name"], "chars": len(g.get("text") or ""), "about": summary(g.get("text") or ""),
               "sections": sections(g.get("text") or "")}
        if g.get("error") or g.get("note"):
            row["note"] = g.get("error") or g.get("note")
        out.append(row)
    return {"ok": True, "guides": out,
            "next": "read_guide with a name (and a section, to read only that part) before you change a setting it covers"}


def read_guide(project: Project, a: dict) -> dict:
    name, want = (a.get("name") or "").strip(), (a.get("section") or "").strip()
    guides = {g["name"]: g for g in collect(project)}
    if name not in guides:
        raise StoreError(f"no guide {name!r}; the guides are {', '.join(guides) or 'none'} (list_guides)")
    text = guides[name].get("text") or ""
    if not text:
        raise StoreError(guides[name].get("error") or f"the guide {name} is empty")
    if not want:
        return {"ok": True, "name": name, "text": text}
    heads = list(re.finditer(r"^(#{2,3}) (.+)$", text, re.MULTILINE))
    hit = next((i for i, m in enumerate(heads) if m.group(2).strip().lower() == want.lower()), None)
    if hit is None:
        hit = next((i for i, m in enumerate(heads) if want.lower() in m.group(2).lower()), None)
    if hit is None:
        raise StoreError(f"the guide {name} has no section {want!r}; its sections: {'; '.join(sections(text))}")
    level = len(heads[hit].group(1))
    end = next((m.start() for m in heads[hit + 1:] if len(m.group(1)) <= level), len(text))
    return {"ok": True, "name": name, "section": heads[hit].group(2).strip(), "text": text[heads[hit].start():end].strip()}
