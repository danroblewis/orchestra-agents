"""``draft_upstream_issue`` (group ``patch``, the Supervisor's): an issue for an upstream
repository, with the patch, as an outbox draft the owner approves (docs/proposals/agent-first-machines.md
§10.3; orchestra-outbox's ``github.issue_create`` sender, whose kinds are always the owner's to
approve).

The draft is the kind ``upstream.issue`` (the owner adds it to ``[outbox.kinds]`` with
``sender = "github.issue_create"``). When the outbox is not installed, not running or has no such
kind, nothing is sent anywhere: the issue is kept in the agents' state directory
(``upstream/<slug>.md``) and the answer says what the owner would add. The outbox is optional for
the agents, so it is imported only here, only when used."""

from __future__ import annotations

import re
import time
from pathlib import Path

from .config import Project
from .secret_shapes import secret_in
from .store import AgentStore, StoreError

COMMANDS = frozenset({"draft_upstream_issue"})
KIND = "upstream.issue"
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BODY_MAX = 60_000
CONFIG_HINT = ('[outbox.kinds."upstream.issue"]\nsender = "github.issue_create"    # approval is always the owner\'s')


def outbox_client(project: Project, drafter: str):
    """The outbox's client as ``drafter`` (tests replace this). Raises ImportError when the outbox
    is not installed, and its own error when it is not running."""
    from orchestra_outbox.client import OutboxClient
    return OutboxClient.for_project(project.orchestra, drafter=drafter)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "issue"


def override_section(project: Project, name: str) -> str:
    p = project.repo_root / ".orchestra" / "overrides" / f"{name}.py"
    if not p.exists():
        raise StoreError(f"no override {name!r} under .orchestra/overrides/")
    return f"\n\n## The patch (applied locally as an override)\n\n```python\n{p.read_text(encoding='utf-8').rstrip()}\n```\n"


def handle(project: Project, store: AgentStore, command: str, a: dict) -> dict:
    repo, title = (a.get("repo") or "").strip(), (a.get("title") or "").strip()
    body = (a.get("body") or "").strip()
    if not REPO.match(repo):
        raise StoreError("repo is owner/name")
    if len(title) < 8 or len(body) < 40:
        raise StoreError("give the issue a title and a body that says what broke, why, and how to reproduce it")
    if a.get("override"):
        body += override_section(project, str(a["override"]).strip())
    if len(body) > BODY_MAX:
        raise StoreError(f"the body is over {BODY_MAX} characters; shorten the events or the patch")
    if why := secret_in(title + "\n" + body):
        raise StoreError(f"refused: the issue looks like it holds a secret ({why}); it would be public")
    drafter = store.events.source or "supervisor"
    key = f"upstream:{_slug(a.get('override') or title)}"
    try:
        d = outbox_client(project, drafter).draft(KIND, target={"repo": repo}, fields={"title": title}, content=body, key=key)
        return {"ok": True, "draft": d.get("id"), "state": d.get("state"), "repo": repo, "kind": KIND,
                "next": "The owner approves or rejects it on the Outbox page; nothing is sent before. Tell them it waits."}
    except ImportError:
        why = "the outbox (orchestra-outbox) is not installed"
    except Exception as e:  # noqa: BLE001 - the outbox's own error says why (not running, no such kind)
        why = str(getattr(e, "error", None) or e)
    kept = _keep(project, key, repo, title, body)
    return {"ok": True, "draft": None, "repo": repo, "kept": str(kept), "why_not_drafted": why,
            "next": (f"Nothing was sent. Tell the owner the issue is written at {kept}, and that to have such issues drafted for "
                     f"their approval they add to the outbox's configuration:\n{CONFIG_HINT}")}


def _keep(project: Project, key: str, repo: str, title: str, body: str) -> Path:
    d = project.state_dir / "upstream"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{key.split(':', 1)[1]}.md"
    p.write_text(f"<!-- for {repo}, written {time.strftime('%Y-%m-%d %H:%M')} -->\n# {title}\n\n{body}\n", encoding="utf-8")
    return p
