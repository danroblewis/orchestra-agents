"""The Supervisor's branch as a pull request, through orchestra-scm's provider (the owner,
2026-11-02: "plain branch plu PR"; "the provider can push to the remote, but not to the main or
master branch").

After every change ``side_branch`` commits, ``propose``:

- **with a remote and ``[scm]``** (read through Orchestra's configuration): pushes
  ``orchestra/supervisor`` (the provider refuses main, master, the base and the default branch) and
  opens its pull request at once, the description from the provider's template (the packaged one has
  Summary, Testing and Jira ID): Summary from the change's reason, Testing from what the Supervisor
  verified, the Jira ID from the owner's words when they name a ticket key (``ABC-123``), else
  "none". A pull request already open for the branch is updated by the push, never opened twice.
- **with the Outbox installed** (the coordinator's decision, 2026-11-02): opening a pull request is an
  outbound act its configuration approves, so the branch is pushed and the pull request is an Outbox
  draft of the kind ``supervisor.pull_request`` (sender ``github.pr_open``) instead.
- **without a remote, ``[scm]`` or orchestra-scm**: the branch is made and nothing is refused; the
  pull request is said pending that configuration.

Merging is the merge gate's or the owner's, never this module's. What was proposed is kept in the
Supervisor's state (``pull_request``), which the digest's "what changed" reads (``digest_line``).
orchestra-scm and the Outbox are optional: each is imported only here, only when used."""

from __future__ import annotations

import contextlib
import importlib.util
import time

from . import side_branch
from .config import Project
from .side_branch import SIDE, git, ref
from .store import AgentStore

MACHINE = "supervisor"
KEY = "pull_request"
KIND = "supervisor.pull_request"
CONFIG_HINT = '[outbox.kinds."supervisor.pull_request"]\nsender = "github.pr_open"'
SCM_HINT = '[scm]\nprovider = "branch_pr"\nrepo = "<owner>/<name>"\nbase = "main"'


def outbox_installed() -> bool:
    return importlib.util.find_spec("orchestra_outbox") is not None


def outbox_client(project: Project, drafter: str):
    """The Outbox's client as ``drafter`` (tests replace this)."""
    from orchestra_outbox.client import OutboxClient
    return OutboxClient.for_project(project.orchestra, drafter=drafter)


def provider(project: Project, section: dict):
    """orchestra-scm's provider for ``[scm]`` (tests may replace this). ImportError when it is not installed."""
    from orchestra_scm.settings import provider_for
    return provider_for(project.orchestra, section=section)


def jira_id(*texts: str | None) -> str:
    """The first ticket key in the owner's words, else "none"."""
    try:
        from orchestra_scm.naming import ticket_keys
    except ImportError:
        import re
        keys = [k for t in texts for k in re.findall(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,9}-[0-9]{1,7})(?![0-9])", t or "")]
    else:
        keys = ticket_keys(*texts)
    return keys[0] if keys else "none"


def owner_words(store: AgentStore | None, a: dict | None = None) -> str | None:
    """The owner's words behind a call: its ``quote``, else this turn's message (None on a wake-up)."""
    quote = ((a or {}).get("quote") or "").strip()
    if quote or store is None:
        return quote or None
    return ((store.agent_state(MACHINE).get("turn") or {}).get("message") or "").strip() or None


def _remote(project: Project) -> str | None:
    p = git(project.repo_root, "remote", "get-url", "origin")
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def _keep(store: AgentStore | None, rec: dict) -> None:
    """What was proposed, for the digest; the change stands whatever happens to this record."""
    if store is None:
        return
    with contextlib.suppress(Exception):     # the record unwritable: the branch and its pull request are still made
        store.set_agent_state(MACHINE, **{KEY: rec})


def propose(project: Project, store: AgentStore | None, *, title: str, reason: str, verified: str,
            quote: str | None = None) -> dict:
    """The branch's pull request after a change: ``{branch, commit, number, url, draft, pending, said}``."""
    side_branch.settle(project)
    sha = ref(project.repo_root, f"refs/heads/{SIDE}")
    rec = {"branch": SIDE, "commit": sha, "title": title, "at": time.time(), "number": None, "url": None,
           "draft": None, "pending": None}
    section = dict((project.orchestra.config.raw or {}).get("scm") or {})
    why = (None if section and _remote(project) else
           f"the repository has no remote (origin) to push {SIDE} to" if section else
           "the provider is not configured ([scm] in orchestra.toml or the owner's overlay)")
    scm = None
    if why is None:
        try:
            scm = provider(project, section)
        except ImportError:
            why = "orchestra-scm is not installed"
        except Exception as e:  # noqa: BLE001 - the library's own sentence says what is wrong with [scm]
            why = str(e)
    if scm is not None:
        rec.update(_through(project, store, scm, section, sha, title=title, reason=reason, verified=verified, quote=quote))
        why = rec.get("pending")
    rec["pending"] = why
    rec["said"] = said(rec)
    _keep(store, rec)
    return rec


def _through(project, store, scm, section, sha, *, title, reason, verified, quote) -> dict:
    from orchestra_scm import ScmError, Workspace
    ws = Workspace(MACHINE, project.repo_root, SIDE, scm.base, scm.name)
    fields = {"summary": reason.strip(), "jira_id": jira_id(quote), "commit": sha or ""}
    by_hand = {"testing done": (verified or "").strip() or "nothing beyond Orchestra's configuration checks"}
    try:
        if not outbox_installed():
            change = scm.open_pr(ws, title=title, fields=fields, by_hand=by_hand)
            return {"number": change.number, "url": change.url}
        change = scm.propose(ws, title=title, open_pr=False)            # pushed; the Outbox opens it
        if change.opened:
            return {"number": change.number, "url": change.url}
        body = scm.describe(ws, title=title, fields=fields, by_hand=by_hand, head=sha)
    except ScmError as e:
        return {"pending": f"the provider could not propose {SIDE}: {e}"}
    base_at = git(project.repo_root, "merge-base", SIDE, f"refs/heads/{scm.base}").stdout.strip()[:12] or "new"
    try:
        d = outbox_client(project, (store.events.source if store else None) or MACHINE).draft(
            KIND, target={"repo": section.get("repo"), "head": SIDE, "base": scm.base}, fields={"title": title},
            content=body, key=f"supervisor-pr:{base_at}")
        return {"draft": d.get("id"), "pushed": True}
    except Exception as e:  # noqa: BLE001 - the Outbox's own error says why (not running, no such kind)
        return {"pushed": True, "pending": (f"{SIDE} is pushed; the Outbox could not take the pull request as a draft "
                                            f"({getattr(e, 'error', None) or e}): the owner adds to its configuration "
                                            f"{CONFIG_HINT!r}")}


def said(rec: dict) -> str:
    """One sentence for the Supervisor's answer: the branch, and the pull request or why it waits."""
    if rec.get("number"):
        return f"On the branch {SIDE}, pull request #{rec['number']} ({rec.get('url')})."
    if rec.get("draft"):
        return (f"On the branch {SIDE}, pushed; its pull request is the Outbox draft {rec['draft']}, opened when the "
                "owner approves it.")
    return f"On the branch {SIDE}; a pull request is pending: {rec.get('pending')}."


def carried(project: Project, store: AgentStore | None, sha: str | None, *, title: str, reason: str, verified: str,
            quote: str | None = None) -> dict:
    """What a tool answers about a change ``side_branch`` committed (``sha``; None: nothing new)."""
    if not sha:
        return {"branch": SIDE, "pull_request": None, "said": f"Nothing new on {SIDE}."}
    pr = propose(project, store, title=title, reason=reason, verified=verified, quote=quote)
    return {"branch": SIDE, "pull_request": {k: pr[k] for k in ("number", "url", "draft", "pending")},
            "said": pr["said"]}


def digest_line(project: Project, store: AgentStore) -> str | None:
    """The Supervisor's open pull request, for "what changed": None when it has none open."""
    side_branch.settle(project)
    rec = store.agent_state(MACHINE).get(KEY) or {}
    if not rec.get("commit") or not side_branch.pending(project):
        return None
    base = project.config.base_branch
    tip = ref(project.repo_root, f"refs/remotes/origin/{base}")
    if tip and git(project.repo_root, "merge-base", "--is-ancestor", rec["commit"], tip).returncode == 0:
        return None                                       # merged upstream, not pulled yet
    if rec.get("number"):
        return f"The Supervisor's pull request: #{rec['number']} open on {SIDE} ({rec.get('url')}), \"{rec.get('title')}\"."
    if rec.get("draft"):
        return f"The Supervisor's pull request: the Outbox draft {rec['draft']} for {SIDE}, waiting on the owner."
    return f"The Supervisor's pull request: pending for {SIDE} ({rec.get('pending')})."
