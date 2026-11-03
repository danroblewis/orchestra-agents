"""The project's CI as its workflow files say it (``.github/workflows/*.yml``, read-only): each
file's name, what triggers it and its jobs, for the Supervisor's first look (``check_setup``'s
``ci``; bootstrap run 3 said "CI is defined but has never run" without naming GitHub Actions or
``ci.yml``). Read from the files in the project's tree only, never run and never fetched; a file
that cannot be read is named with why.

The files are read line by line (no YAML library is a dependency here): the top-level ``name``,
``on`` (a word, a ``[a, b]`` list, a ``- a`` list or a mapping's keys) and ``jobs``' keys, each
with its own ``name`` when it has one."""

from __future__ import annotations

import re
from pathlib import Path

MAX_FILES = 20
TOP = re.compile(r"^([A-Za-z_][\w-]*|\"on\"|'on')\s*:\s*(.*?)\s*$")
KEY = re.compile(r"^(\s+)([\w.-]+)\s*:\s*(.*?)\s*$")
ITEM = re.compile(r"^\s+-\s*([\w.-]+)\s*$")


def _words(value: str) -> list[str]:
    value = value.split(" #")[0].strip()
    if value.startswith("[") and value.endswith("]"):
        return [w.strip().strip("'\"") for w in value[1:-1].split(",") if w.strip()]
    return [value.strip("'\"")] if value else []


def _blocks(text: str) -> dict[str, tuple[str, list[str]]]:
    """Each top-level key: its value on the line, and the indented lines under it."""
    out: dict[str, tuple[str, list[str]]] = {}
    key = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace() and (m := TOP.match(line)):
            key = m.group(1).strip("'\"")
            out[key] = (m.group(2), [])
        elif key is not None:
            out[key][1].append(line)
    return out


def _children(lines: list[str]) -> list[tuple[str, str, list[str]]]:
    """The keys one level in (the shallowest indent), each with its value and its own lines."""
    keys = [(len(m.group(1)), m) for ln in lines if (m := KEY.match(ln))]
    if not keys:
        return []
    depth = min(d for d, _ in keys)
    out: list[tuple[str, str, list[str]]] = []
    for ln in lines:
        m = KEY.match(ln)
        if m and len(m.group(1)) == depth:
            out.append((m.group(2), m.group(3), []))
        elif out:
            out[-1][2].append(ln)
    return out


def read(path: Path) -> dict:
    """One workflow file: ``{file, name, on, jobs}`` (each job ``id`` or ``id (its name)``)."""
    blocks = _blocks(path.read_text(encoding="utf-8", errors="replace"))
    name = _words(blocks.get("name", ("", []))[0])
    value, lines = blocks.get("on") or ("", [])
    on = _words(value) or [m.group(1) for ln in lines if (m := ITEM.match(ln))] or [k for k, _, _ in _children(lines)]
    jobs = []
    for jid, _, sub in _children((blocks.get("jobs") or ("", []))[1]):
        own = next((_words(v) for k, v, _ in _children(sub) if k == "name"), [])
        jobs.append(f"{jid} ({own[0]})" if own and own[0] != jid else jid)
    return {"file": str(path), "name": name[0] if name else path.stem, "on": on, "jobs": jobs}


def workflows(repo_root: Path) -> list[dict]:
    """Every workflow file under ``.github/workflows`` (``.yml`` and ``.yaml``), relative paths."""
    d = Path(repo_root) / ".github" / "workflows"
    if not d.is_dir():
        return []
    out = []
    for p in sorted([*d.glob("*.yml"), *d.glob("*.yaml")])[:MAX_FILES]:
        rel = p.relative_to(repo_root)
        try:
            out.append({**read(p), "file": str(rel)})
        except OSError as e:
            out.append({"file": str(rel), "error": str(e)})
    return out


def line(found: list[dict]) -> str:
    """The sentence the first answer names CI with."""
    if not found:
        return "No CI workflow in .github/workflows: say the project's CI is not defined in the repository."
    each = [f"{w['file']} (\"{w['name']}\": on {', '.join(w['on']) or 'nothing named'}; jobs {', '.join(w['jobs']) or 'none'})"
            if "error" not in w else f"{w['file']} (unreadable: {w['error']})" for w in found]
    return ("The project's CI is GitHub Actions: " + "; ".join(each)
            + ". Name it in your first answer as this says; whether it has run is GitHub's to say, not the files'.")


def summary(repo_root: Path) -> dict:
    found = workflows(repo_root)
    return {"workflows": found, "line": line(found)}
