"""The Supervisor's skills: what it knows how to do, kept out of its system prompt and read on
demand (the owner, 2026-11-04: "we should stay under 35k for a regular system prompt and split the
rest into separate prompt files").

Each skill is ``prompts/skills/<name>.md``: a title, a ``Use when:`` line, then the text. The system
prompt carries only the index (``index``: each skill's name and its "use when"); the Supervisor
reads one with ``read_skill`` (``read``: bounded like ``read_guide``, a long one in pieces), and on
Claude Code the harness also writes them as the session's own skills (``claude_skills``). A skill
whose "use when" begins ``(a delegated job)`` is the whole system prompt of a sub-session
(``delegate.py``) and is left out of the index. A skill's text goes through the same placeholders and
task-system gate as the prompt it came from."""

from __future__ import annotations

import re
from pathlib import Path

from .store import StoreError

SKILLS_DIR = Path(__file__).parent / "prompts" / "skills"
PIECE_CHARS = 12_000         # a skill's piece in one answer: well under answers' trimming bound with the JSON around it
DELEGATED = "(a delegated job)"
WHEN = re.compile(r"^Use when:\s*(.+)$", re.MULTILINE)


def names() -> list[str]:
    return sorted(p.stem for p in SKILLS_DIR.glob("*.md"))


def raw(name: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name or "") or not (SKILLS_DIR / f"{name}.md").is_file():
        raise StoreError(f"no skill {name!r}; the skills are {', '.join(indexed())} (your prompt's index)")
    return (SKILLS_DIR / f"{name}.md").read_text(encoding="utf-8")


def when(name: str) -> str:
    """The skill's "use when" line; "" for one that holds only with the task system, without it."""
    from .tasking_text import gate
    m = WHEN.search(gate(raw(name)))
    return m.group(1).strip() if m else ""


def delegated(name: str) -> bool:
    return when(name).startswith(DELEGATED)


def indexed() -> list[str]:
    """The skills the index lists: every one that holds now (``when``), but a delegated job's."""
    return [n for n in names() if when(n) and not delegated(n)]


def text(project, name: str) -> str:
    """A skill's text as the Supervisor reads it: its placeholders filled and gated as the prompt is."""
    from .prompting import _fill
    from .tasking_text import gate
    return gate(_fill(project, raw(name)))


INDEX_HEAD = """## Skills: read the one a request needs

Your know-how is kept in skills, out of this prompt. Before your first change for a kind of work in
this session, read its skill (`read_skill` with its name; on Claude Code it is also a skill of this
session): its steps, the order of your tools and its pitfalls bind you as this prompt does. When a
request fits none, act from this prompt."""


def index() -> str:
    """The system prompt's index of skills: each by name with its one line on when to use it."""
    return INDEX_HEAD + "\n\n" + "\n".join(f"- `{n}`: {when(n)}" for n in indexed())


def read(project, a: dict) -> dict:
    """``read_skill``: one skill's text, at most PIECE_CHARS from ``offset``, ending at a line's end; a
    longer one says where to read on."""
    name = (a.get("name") or "").strip()
    body = text(project, name)
    offset = max(0, int(a.get("offset") or 0))
    if offset >= len(body) and body:
        raise StoreError(f"the skill {name} is {len(body):,} characters; there is nothing at offset {offset}")
    end = offset + PIECE_CHARS
    if end < len(body):
        nl = body.rfind("\n", offset + PIECE_CHARS // 2, end)
        end = nl + 1 if nl >= 0 else end
    else:
        end = len(body)
    out = {"ok": True, "skill": name, "chars": len(body), "text": body[offset:end]}
    if end < len(body):
        out["next"] = f"read_skill with name {name} and offset {end} for the rest ({len(body) - end:,} more characters)"
    return out


def for_session(project) -> tuple[tuple[str, str, str], ...]:
    """The skills a Supervisor's session is given as (name, use when, text), for a harness that loads
    skills of its own (``SessionSpec.skills``)."""
    return tuple((n, when(n), text(project, n)) for n in indexed())


def write_claude(skills: tuple[tuple[str, str, str], ...], root: Path) -> Path:
    """The skills as Claude Code loads its own: ``<root>/.claude/skills/<name>/SKILL.md``, with the
    front matter it reads (the name, and the "use when" as its description). ``root`` is a directory
    of the session's own, outside the repository, that the harness adds with ``--add-dir``; a skill no
    longer given is removed. Returns ``root``."""
    import shutil
    base = root / ".claude" / "skills"
    base.mkdir(parents=True, exist_ok=True)
    names_ = {n for n, _, _ in skills}
    for d in base.iterdir():
        if d.is_dir() and d.name not in names_:
            shutil.rmtree(d)
    for n, use, body in skills:
        (base / n).mkdir(exist_ok=True)
        desc = use.replace("\\", "").replace('"', "'")
        (base / n / "SKILL.md").write_text(f'---\nname: {n}\ndescription: "Use when: {desc}"\n---\n\n{body}', encoding="utf-8")
    return root

