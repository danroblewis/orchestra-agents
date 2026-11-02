"""What a machine's workflow sends through the Outbox, in a sentence for ``declare_machine``,
``refine_machine`` and ``confirm`` (their answer's ``sends``): "each comment on a pull request
(live.nudge) is drafted to the Outbox and waits for the owner's approval". The T2i walkthrough's
step 10 answer left out that each reminder waits for the owner (step 11's said it), since nothing
in the data said so.

A machine's workflows are those whose steps name it as their program (``workflow_programs``), or the
one of its name. Each of their outbox steps (``who: {outbox: ...}``) drafts a kind; the kind's
``approval`` in ``[outbox.kinds.<kind>]`` says whether its drafts wait for the owner (``always``,
the Outbox's default) or are sent by configuration (``auto``). Read from the merged configuration as
Orchestra gives it; nothing here imports the outbox."""

from __future__ import annotations

from . import machines_running
from .config import Project

WHAT = {"github.pr_comment": "comment on a pull request", "github.label": "label on a pull request",
        "github.pr_open": "pull request", "slack.message": "Slack message"}


def lines(project: Project, mid: str) -> list[str]:
    """One sentence per outbox kind the machine's workflows draft; empty when they draft none."""
    names = set(machines_running.workflow_programs(project).get(mid) or []) | {mid}
    try:
        from .workflow_tools import plugin
        p = plugin()
        defs = p.define.load(project.orchestra) if p is not None else None
    except Exception:  # noqa: BLE001 - a definition that does not load sends nothing here
        defs = None
    outbox = (project.orchestra.config.raw or {}).get("outbox")
    kinds = (outbox.get("kinds") if isinstance(outbox, dict) else None) or {}
    out: list[str] = []
    for w in getattr(defs, "workflows", None) or []:
        if w.name not in names:
            continue
        for st in w.steps:
            draft = getattr(st, "draft", None)
            if getattr(getattr(st, "actor", None), "kind", None) != "outbox" or draft is None:
                continue
            conf = kinds.get(draft.kind) if isinstance(kinds.get(draft.kind), dict) else {}
            what = f"{WHAT.get(conf.get('sender'), 'draft')} ({draft.kind})"
            line = (f"each {what} is sent by configuration, without waiting for the owner's approval"
                    if conf.get("approval") == "auto" else
                    f"each {what} is drafted to the Outbox and waits for the owner's approval")
            if line not in out:
                out.append(line)
    return out


def sends(project: Project, mid: str) -> str | None:
    """The answer's ``sends``: the sentences joined, or None when nothing is sent through the Outbox."""
    said = lines(project, mid)
    return "; ".join(said) if said else None
