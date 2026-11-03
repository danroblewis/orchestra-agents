"""The Supervisor's branch as a pull request, through orchestra-scm's provider (the owner,
2026-11-02: "plain branch plu PR"; "the provider can push to the remote, but not to the main or
master branch").

After every change ``side_branch`` commits, ``propose``:

- **with a remote and ``[scm]``** (read through Orchestra's configuration): pushes
  ``orchestra/supervisor`` (the provider refuses main, master, the base and the default branch) and
  opens its pull request at once, the description from the provider's template (the packaged one has
  Summary, Testing and Jira ID). **Both describe the whole branch, never its last commit alone**
  (run 4: PR #53 was titled "Supervisor: outbox.kinds set" over four commits): the title lists
  what the branch changes, each commit's ``Supervisor-For`` label once, oldest first, in at most
  ``TITLE_MAX`` characters ("Supervisor: draft expiry, [scm], an Outbox kind"); the Summary is one
  line per commit with its reason (``branch_story``); Testing is what the Supervisor verified, the
  Jira ID the owner's words when they name a ticket key (``ABC-123``), else "none". A pull request
  already open for the branch is updated by the push, never opened twice, and **its title and body
  are rewritten through the provider** (``update_pr``) when the branch gained a commit; a provider
  without ``update_pr`` leaves them as opened, and the answer says so.
- **with the Outbox installed** (the coordinator's decision, 2026-11-02): opening a pull request is an
  outbound act its configuration approves, so the branch is pushed and the pull request is an Outbox
  draft of the kind ``supervisor.pull_request`` (sender ``github.pr_open``) instead.
- **without a remote, ``[scm]`` or orchestra-scm**: the branch is made and nothing is refused; the
  pull request is said pending that configuration.
- **once ``[scm]`` is valid** (``set_setting`` checks it against orchestra-scm's schema,
  ``scm_settings``), the commits that waited for it go in one push and one pull request: carried by
  the next change, or by ``catch_up`` when no commit carries them (the section set in the overlay, the
  review round, the digest before it names the branch); the answer says "the branch's N pending
  commits are now pushed as PR #…". A refused push (no token, auth, a protected branch) is said in the
  provider's and git's own words, and tried again when the tip or ``[scm]`` changes, else after
  ``RETRY_S``.

Merging is the merge gate's or the owner's, never this module's. What was proposed is kept in the
Supervisor's state (``pull_request``), which the digest's "what changed" reads (``digest_line``).
orchestra-scm and the Outbox are optional: each is imported only here, only when used."""

from __future__ import annotations

import contextlib
import importlib.util
import time

from . import live_layers, side_branch
from .config import Project
from .side_branch import SIDE, git, ref
from .store import AgentStore

MACHINE = "supervisor"
KEY = "pull_request"
KIND = "supervisor.pull_request"
CONFIG_HINT = '[outbox.kinds."supervisor.pull_request"]\nsender = "github.pr_open"'
RETRY_S = 1500.0       # a refused push with the same tip and [scm] is tried again after this (each review round)
TITLE_MAX = 60         # a pull request's title, at most
FOR = "Supervisor-For"  # the trailer naming what a commit is for, in a few words (the title's parts)
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


def unpushed(project: Project) -> int:
    """How many of the branch's commits the remote does not have: those past ``origin/orchestra/supervisor``
    as last pushed or fetched, else those past the base (local and ``origin/<base>``); 0 with no branch."""
    root = project.repo_root
    if not ref(root, f"refs/heads/{SIDE}"):
        return 0
    there = [r for r in (f"refs/remotes/origin/{SIDE}", f"refs/heads/{project.config.base_branch}",
                         f"refs/remotes/origin/{project.config.base_branch}") if ref(root, r)]
    if f"refs/remotes/origin/{SIDE}" in there:
        there = there[:1]
    p = git(root, "rev-list", "--count", f"refs/heads/{SIDE}", *(f"^{r}" for r in there))
    return int(p.stdout.strip() or 0) if p.returncode == 0 else 0


def message(label: str, what: str, reason: str, *trailers: str) -> str:
    """A commit's message on the branch: its subject names what it is for and what it does, the reason
    is its body, and the ``Supervisor-For`` trailer keeps the label the pull request's title lists."""
    label = " ".join((label or what).split())
    head = f"Supervisor: {what}" if label.casefold() in what.casefold() else f"Supervisor: {label}: {what}"
    return f"{head}\n\n{reason.strip()}\n\n" + "".join(f"{t}\n" for t in trailers if t) + f"{FOR}: {label}\n"


def label_of(subject: str, body: str) -> str:
    """What a commit is for: its ``Supervisor-For`` trailer, else its subject cut to the thing done."""
    for line in reversed(body.splitlines()):
        if line.startswith(f"{FOR}:"):
            return line.split(":", 1)[1].strip()
    s = subject.removeprefix("Supervisor:").strip()
    for cut in (" — ", ": ", " ("):
        s = s.split(cut, 1)[0]
    return s.strip() or subject


def _commits(project: Project) -> list[tuple[str, str]]:
    """The branch's commits the base does not have, oldest first: (subject, body)."""
    root, base = project.repo_root, project.config.base_branch
    there = [r for r in (f"refs/heads/{base}", f"refs/remotes/origin/{base}") if ref(root, r)]
    if not ref(root, f"refs/heads/{SIDE}"):
        return []
    p = git(root, "log", "--reverse", "--format=%s%x1f%b%x1e", f"refs/heads/{SIDE}", *(f"^{r}" for r in there))
    if p.returncode != 0:
        return []
    out = []
    for rec in p.stdout.split("\x1e"):
        if "\x1f" in rec:
            subj, body = rec.strip("\n").split("\x1f", 1)
            out.append((subj.strip(), body.strip()))
    return out


def title_of(labels: list[str]) -> str:
    """"Supervisor: a, b, c" with each label once, in at most ``TITLE_MAX`` characters (the rest counted)."""
    seen = list(dict.fromkeys(x for x in labels if x))
    if not seen:
        return "Supervisor: changes to the project's configuration"
    for k in range(len(seen), 0, -1):
        more = len(seen) - k
        t = "Supervisor: " + ", ".join(seen[:k]) + (f" +{more} more" if more else "")
        if len(t) <= TITLE_MAX:
            return t
    t = f"Supervisor: {len(seen)} changes"
    return t if len(seen) > 1 else ("Supervisor: " + seen[0])[:TITLE_MAX - 1].rstrip() + "…"


def branch_story(project: Project) -> tuple[str, str] | None:
    """The branch's pull request title and Summary: what the branch changes, and one line per commit
    with its reason, oldest first; None when the branch holds nothing the base lacks."""
    commits = _commits(project)
    if not commits:
        return None
    lines = []
    for subj, body in commits:
        reason = next((ln.strip() for ln in body.split("\n\n", 1)[0].splitlines() if ln.strip()), "")
        what = subj.removeprefix("Supervisor:").strip()
        lines.append(f"- {what}" + (f": {reason}" if reason and reason not in what else ""))
    return title_of([label_of(s, b) for s, b in commits]), "\n".join(lines)


def propose(project: Project, store: AgentStore | None, *, title: str, reason: str, verified: str,
            quote: str | None = None) -> dict:
    """The branch's pull request after a change: ``{branch, commit, number, url, draft, pending, said}``;
    ``caught_up``: how many commits this push carried when more than this change's one, or when they
    waited on ``[scm]`` (the answer then says so)."""
    live_layers.settle(project)
    sha = ref(project.repo_root, f"refs/heads/{SIDE}")
    if story := branch_story(project):              # the whole branch, never the last commit alone
        title, reason = story
    before = (store.agent_state(MACHINE).get(KEY) or {}) if store is not None else {}
    waiting = unpushed(project)
    rec = {"branch": SIDE, "commit": sha, "title": title, "at": time.time(), "number": None, "url": None,
           "draft": None, "pending": None, "refused": None, "title_body": before.get("title_body")}
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
    if rec.get("number") and (waiting > 1 or (before and not before.get("number"))):
        rec["caught_up"] = waiting
    elif why and before.get("number") and not rec.get("number"):        # the pull request stays the one opened
        rec.update(number=before["number"], url=before.get("url"))
    if why:
        rec["tried"] = _tried(project, sha)
    rec["said"] = said(rec)
    _keep(store, rec)
    return rec


def _tried(project: Project, sha: str | None) -> str:
    """What a push was last tried with (the branch's tip and ``[scm]``), so a refused one is tried again
    at once only when either changed, else after ``RETRY_S`` (a token written meanwhile)."""
    section = dict((project.orchestra.config.raw or {}).get("scm") or {})
    return f"{sha}|{sorted((k, repr(v)) for k, v in section.items())}"


def catch_up(project: Project, store: AgentStore | None) -> dict | None:
    """The branch pushed and its pull request opened when it holds commits the remote lacks and
    ``[scm]`` is now set, with no change of its own to carry them: after a change that made no commit
    (``[scm]`` set in the owner's overlay), on the review round, and before the digest names the branch.
    The proposal (``propose``'s record, ``said`` saying how many commits it carried) or None when there is
    nothing to push, no ``[scm]`` or remote yet, or the same push was refused already."""
    from . import scm_settings
    if store is None or not side_branch.pending(project):
        return None
    n = unpushed(project)
    rec = store.agent_state(MACHINE).get(KEY) or {}
    if n == 0 or not scm_settings.valid(project) or not _remote(project):
        return None
    sha = ref(project.repo_root, f"refs/heads/{SIDE}")
    if (rec.get("pending") and rec.get("tried") == _tried(project, sha)
            and time.time() - (rec.get("at") or 0) < RETRY_S):
        return None
    return propose(project, store, title="", reason="", verified="Orchestra's configuration checks passed on "
                   "each change when it was made; each is live through the owner's overlay until this is merged and pulled")


def _through(project, store, scm, section, sha, *, title, reason, verified, quote) -> dict:
    from orchestra_scm import ScmError, Workspace
    ws = Workspace(MACHINE, project.repo_root, SIDE, scm.base, scm.name)
    fields = {"summary": reason.strip(), "jira_id": jira_id(quote), "commit": sha or ""}
    by_hand = {"testing done": (verified or "").strip() or "nothing beyond Orchestra's configuration checks"}
    try:
        opened = (store.agent_state(MACHINE).get(KEY) or {}) if store is not None else {}
        if not outbox_installed():
            change = scm.open_pr(ws, title=title, fields=fields, by_hand=by_hand)
            return _opened(scm, ws, change, opened, title=title, fields=fields, by_hand=by_hand, head=sha)
        change = scm.propose(ws, title=title, open_pr=False)            # pushed; the Outbox opens it
        if change.opened:
            return _opened(scm, ws, change, opened, title=title, fields=fields, by_hand=by_hand, head=sha)
        body = scm.describe(ws, title=title, fields=fields, by_hand=by_hand, head=sha)
    except ScmError as e:
        return {"pending": f"the provider could not propose {SIDE}: {e}", "refused": True}
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


def _opened(scm, ws, change, opened: dict, *, title: str, fields: dict, by_hand: dict, head: str | None) -> dict:
    """The open pull request, its title and body rewritten when the branch it describes changed since."""
    out = {"number": change.number, "url": change.url, "title_body": _stamp(title, fields)}
    if change.number and opened.get("number") == change.number and opened.get("title_body") != out["title_body"]:
        out.update(_update(scm, ws, change, title=title, fields=fields, by_hand=by_hand, head=head))
        if out.get("not_updated"):
            out["title_body"] = opened.get("title_body")
    return out


def _stamp(title: str, fields: dict) -> str:
    """What the open pull request's title and body were made from (its title and Summary)."""
    import hashlib
    return hashlib.sha256(f"{title}\x00{fields.get('summary')}".encode()).hexdigest()[:16]


def _update(scm, ws, change, *, title: str, fields: dict, by_hand: dict, head: str | None) -> dict:
    """The open pull request's title and body rewritten for the whole branch, through the provider's
    ``update_pr``; a provider without it leaves them as opened, and the answer says so."""
    from orchestra_scm import ScmError
    fn = getattr(scm, "update_pr", None)
    if fn is None:
        return {"not_updated": (f"the provider ({scm.name}) cannot edit an open pull request yet, so #{change.number}'s title "
                                "and description stay as opened")}
    try:
        body = scm.describe(ws, title=title, fields=fields, by_hand=by_hand, head=head)
        fn(change, title=title, body=body)
    except ScmError as e:
        return {"not_updated": f"the provider could not update #{change.number}'s title and description: {e}"}
    return {"updated": True}


def _merged(project: Project, rec: dict) -> bool:
    """Whether the forge says the pull request merged (``learn``), when the provider can ask; a squash
    merge leaves the branch's commit out of the base, so the branch alone cannot tell."""
    section = dict((project.orchestra.config.raw or {}).get("scm") or {})
    if not section:
        return False
    try:
        from orchestra_scm import Change
        scm = provider(project, section)
        return scm.learn(Change(scm.name, MACHINE, SIDE, scm.base, rec.get("commit") or "", rec["number"],
                                rec.get("url"))).state == "merged"
    except Exception:  # noqa: BLE001 - the forge unreachable: not known to be merged
        return False


def said(rec: dict) -> str:
    """One sentence for the Supervisor's answer: the branch, and the pull request or why it waits."""
    n = rec.get("caught_up")
    if rec.get("number") and rec.get("pending"):
        return (f"On the branch {SIDE}, pull request #{rec['number']} ({rec.get('url')}); the latest commits are not "
                f"pushed: {rec['pending']}.")
    if rec.get("number") and rec.get("not_updated"):
        return (f"On the branch {SIDE}, pull request #{rec['number']} ({rec.get('url')}), pushed; {rec['not_updated']}.")
    if rec.get("number") and n:
        return (f"The branch's {n} pending commit{'s are' if n != 1 else ' is'} now pushed as PR #{rec['number']} "
                f"({rec.get('url')}), on the branch {SIDE}.")
    if rec.get("number"):
        return f"On the branch {SIDE}, pull request #{rec['number']} ({rec.get('url')})."
    if rec.get("draft"):
        return (f"On the branch {SIDE}, pushed; its pull request is the Outbox draft {rec['draft']}, opened when the "
                "owner approves it.")
    if rec.get("refused"):
        return (f"On the branch {SIDE}, not pushed and no pull request opened: the provider refused it, in its and "
                f"git's own words: {rec.get('pending')}.")
    return f"On the branch {SIDE}; a pull request is pending: {rec.get('pending')}."


def carried(project: Project, store: AgentStore | None, sha: str | None, *, title: str, reason: str, verified: str,
            quote: str | None = None) -> dict:
    """What a tool answers about a change ``side_branch`` committed (``sha``; None: nothing new)."""
    if not sha:
        if (pr := catch_up(project, store)) is not None:          # nothing new, but [scm] set since: the waiting commits
            return {"branch": SIDE, "pull_request": {k: pr[k] for k in ("number", "url", "draft", "pending")},
                    "said": pr["said"]}
        return {"branch": SIDE, "pull_request": None, "said": f"Nothing new on {SIDE}."}
    pr = propose(project, store, title=title, reason=reason, verified=verified, quote=quote)
    return {"branch": SIDE, "pull_request": {k: pr[k] for k in ("number", "url", "draft", "pending")},
            "said": pr["said"]}


def digest_line(project: Project, store: AgentStore) -> str | None:
    """The Supervisor's open pull request, for "what changed": None when it has none open."""
    live_layers.settle(project)
    with contextlib.suppress(Exception):      # [scm] set since: the branch's waiting commits pushed first
        catch_up(project, store)
    rec = store.agent_state(MACHINE).get(KEY) or {}
    if not rec.get("commit") or not side_branch.pending(project):
        return None
    if rec.get("number") and _merged(project, rec):
        return (f"The Supervisor's pull request #{rec['number']} is merged; its live copies retire once the checkout "
                "pulls it.")
    if rec.get("number"):
        return f"The Supervisor's pull request: #{rec['number']} open on {SIDE} ({rec.get('url')}), \"{rec.get('title')}\"."
    if rec.get("draft"):
        return f"The Supervisor's pull request: the Outbox draft {rec['draft']} for {SIDE}, waiting on the owner."
    return f"The Supervisor's pull request: pending for {SIDE} ({rec.get('pending')})."
