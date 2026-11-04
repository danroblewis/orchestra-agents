"""``read_outbox``: the Outbox's drafts as its approval page has them (the outbox plugin's state API,
``GET /api/plugins/outbox/state`` on Orchestra's web server, behind its login: the project's web
token as a bearer): the drafts waiting, and the latest decided (sent, rejected, failed), each with
its kind, its target, who drafted it and how it was approved (the owner, by their person id, or the
kind's policy), with the kinds and how each is approved. Read only: nothing is approved, rejected
or sent here, and a draft's text is shortened, never copied whole.

The web server's port is the one it recorded in Orchestra's runtime record (``web``); without a
running web server the answer says so.

With the bus's events (``events``), a waiting nudge about a pull request the bus says merged or
closed is withdrawn (``stale_nudges``): listed under ``withdrawn``, never among the drafts waiting,
so it is not offered to the owner for approval."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from orchestra import runtime
from orchestra.runtime import alive

from .config import Project
from .store import StoreError

PATH = "/api/plugins/outbox/state"
TIMEOUT_S = 10.0
TEXT = 160
RECENT = 20


def fetch(project: Project) -> dict:
    """The outbox's state, as its page reads it (tests replace this)."""
    from orchestra.web import auth
    web = runtime.read(project.orchestra).get("web") or {}
    if not web.get("port") or not web.get("pid") or not alive(web["pid"]):
        raise StoreError("Orchestra's web server is not running (orchestra web), and the Outbox's drafts are read through "
                         "its page's API: read_events with types outbox. has what it published meanwhile")
    req = urllib.request.Request(f"http://127.0.0.1:{int(web['port'])}{PATH}",
                                 headers={"Authorization": f"Bearer {auth.token(project.orchestra)}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise StoreError("the outbox plugin is not installed beside Orchestra's web server: there is no Outbox to read") from None
        raise StoreError(f"the Outbox's state was refused (HTTP {e.code})") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise StoreError(f"the Outbox's state did not answer ({e})") from None


def _short(text) -> str | None:
    if not isinstance(text, str):
        return None
    return text if len(text) <= TEXT else text[:TEXT] + f"… ({len(text)} chars)"


def approved_how(d: dict) -> str | None:
    """How a draft was decided, in a few words: by the owner (their person id) or by its kind's policy."""
    if d.get("state") == "drafted":
        return "waiting for the owner" + (f" ({d['held']})" if d.get("held") else "")
    by = d.get("approved_by")
    who = f"the owner ({d['approver']})" if d.get("approver") else ("the owner" if by == "owner" else "its kind's policy" if by == "policy" else by)
    if d.get("state") == "rejected":
        return f"rejected by {who or 'the owner'}" + (f": {d['reason']}" if d.get("reason") else "")
    return (f"approved by {who}" + (", edited" if d.get("edited") else ", unedited")) if who else None


def row(d: dict) -> dict:
    out = {"id": d.get("id"), "kind": d.get("kind"), "state": d.get("state"), "target": d.get("target"),
           "drafter": d.get("drafter"), "how": approved_how(d), "content": _short(d.get("content")),
           "created_at": d.get("created_at"), "decided_at": d.get("decided_at"), "sent_at": d.get("sent_at"),
           "remote": d.get("remote"), "error": d.get("error")}
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def read_outbox(project: Project, a: dict | None = None, events: list[dict] | None = None) -> dict:
    st = fetch(project)
    if not st.get("ok", True):
        return {"ok": False, "error": st.get("error") or "the Outbox's state did not answer"}
    waiting = [row(d) for d in st.get("waiting") or []]
    withdrawn: list[dict] = []
    if events is not None:
        from .stale_nudges import split
        waiting, withdrawn = split(waiting, events)
    recent = [row(d) for d in (st.get("recent") or [])[:RECENT]]
    kinds = [{"name": k.get("name"), "approval": k.get("approval"), "unedited_streak": k.get("unedited_streak")}
             for k in st.get("kinds") or []]
    count = lambda state: sum(1 for d in recent if d.get("state") == state)
    summary = [f"{len(waiting)} waiting; of the latest {len(recent)} decided: {count('sent')} sent, {count('rejected')} rejected, "
               f"{count('failed')} failed" + ("" if st.get("running", True) else "; the outbox machine is not running")]
    summary += [f"waiting {d['id']} {d.get('kind')} to {json.dumps(d.get('target'))} from {d.get('drafter')}" for d in waiting[:10]]
    summary += [f"withdrawn {d['id']} {d.get('kind')}: #{d['number']} is {d['state']}, so it is never offered for approval; "
                "the owner rejects it on the Outbox page to clear it" for d in withdrawn]
    summary += [f"{d.get('state')} {d['id']} {d.get('kind')} to {json.dumps(d.get('target'))}: {d.get('how')}" for d in recent[:10]]
    return {"ok": True, "summary": summary, "waiting": waiting, **({"withdrawn": withdrawn} if withdrawn else {}),
            "recent": recent, "kinds": kinds,
            "configured": st.get("configured"), "running": st.get("running"), "owner": st.get("owner"),
            "note": "read only: the owner approves or rejects on the Outbox page; nothing here changes a draft"}
