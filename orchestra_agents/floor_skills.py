"""What an agent knows how to do on a Workflows offer, by what the offer is, never by which workflow
it came from (the Supervisor composes the workflows; these hold for any of them): ``code-change``
when it hands a worktree to change code, ``review-change`` when it asks for a review (its outcomes
include ``approved``). Each is ``prompts/floor_skills/<name>.md``, appended to the turn's prompt
(floor.task_text), with the item's fields the offer carries, where rework's notes are."""

from __future__ import annotations

import json
from pathlib import Path

DIR = Path(__file__).parent / "prompts" / "floor_skills"
FIELD_CHARS = 4000          # one field's text in the prompt; the rest is cut, said
FIELDS_CHARS = 16000        # all of them


def name_for(offer: dict, path: str | None) -> str | None:
    outcomes = {str(o) for o in offer.get("outcomes") or []}
    if "approved" in outcomes:
        return "review-change"
    if path:
        return "code-change"
    return None


def text(name: str) -> str:
    return (DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def fields_text(offer: dict) -> str:
    """The item's fields as the offer carries them, each cut to ``FIELD_CHARS``."""
    fields = offer.get("fields") if isinstance(offer.get("fields"), dict) else {}
    out, total = [], 0
    for k in sorted(fields):
        v = fields[k]
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        if len(s) > FIELD_CHARS:
            s = s[:FIELD_CHARS] + " … (cut)"
        if total + len(s) > FIELDS_CHARS:
            out.append(f"- `{k}`: (left out: the fields are long; explain_item has it)")
            continue
        total += len(s)
        out.append(f"- `{k}`:\n\n  " + s.replace("\n", "\n  ") if "\n" in s else f"- `{k}`: {s}")
    return "\n".join(out)


def for_offer(offer: dict, path: str | None) -> str:
    """The fields section and the skill for this offer, as prompt text ("" when neither applies)."""
    parts = []
    if f := fields_text(offer):
        parts.append("## The item's fields\n\n" + f)
    if n := name_for(offer, path):
        parts.append(text(n))
    return "\n\n".join(parts)
