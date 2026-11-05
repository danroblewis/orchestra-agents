"""``delegate`` run by the agents host (the owner, 2026-11-04: a delegated job "got a 401 error from my
LLM service, but I am still talking to the Supervisor so it has a valid key").

The tool command runs inside the Supervisor's shell, often in Claude Code's sandbox, where a nested
session may not reach the owner's sign-in: the macOS keychain the sandbox blocks, a gateway's variable
``session_env`` strips, the product's own scrubbing of its Bash commands' environment. The host started
the Supervisor with its own environment, which is why the Supervisor signs in. So every ``delegate``
call (the tool command, the MCP server, the Pi extension: ``harness/caller.py``) is a request to the
host's control endpoint (``POST /delegate``, control.py), with the calling session's token, through the
sandbox's proxy when it must be (``local_http``). The host checks the token's grant (the Supervisor's,
never a delegated sub-session's: it never delegates again), runs ``delegate.delegate`` in its own
process, and streams NDJSON: ``started`` once the sub-session is open, ``tool`` for each tool it calls,
``wait`` every ``BEAT`` seconds, then ``done`` with the answer or the refusal.

The caller waits at most the delegated turn's own bound and a margin (``delegate.turn_s``), inside the
tool call's own (``TOOL_CALL_S``). On a timeout it returns what it knows: the sub-session, its
transcript and the tools it called; the host still ends the sub-session, revokes its token and counts its
spend. With no host running, the call is refused with that reason: a job never runs anywhere else."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping

from orchestra.runtime import alive

from . import access, delegate, ledger, local_http
from .answers import first
from .config import Project
from .store import AgentStore, StoreError

PATH = "/delegate"
BEAT = 15.0             # the host's wait line: the caller checks its deadline at least this often
QUIET_S = 4 * BEAT      # no line for this long: the host is gone
MARGIN_S = 30.0         # past the turn's bound: the host closes the session and sends its report
DOWN = ("not delegated: the agents host is not running ({why}). A delegated job runs in the host, with the host's "
        "sign-in, never in the tool command; tell the owner the host is down (it starts with `orchestra run`)")


def wait_s(project: Project) -> float:
    return delegate.turn_s(project) + MARGIN_S


def _endpoint(store: AgentStore) -> str:
    rec = store.runtime().get("control") or {}
    port, pid = rec.get("port"), rec.get("pid")
    if not port or not pid:
        raise StoreError(DOWN.format(why="no control endpoint is recorded"))
    if not alive(int(pid)):
        raise StoreError(DOWN.format(why=f"the host that recorded the control endpoint, pid {pid}, has ended"))
    return f"http://127.0.0.1:{port}{PATH}"


def ask(project: Project, store: AgentStore, a: dict, role: str, env: Mapping[str, str],
        wait: float | None = None) -> dict:
    """The ``delegate`` call, made by the host: its answer, or what is known when no answer came in time."""
    job, brief = delegate.check(a, role, env)
    url = _endpoint(store)
    wait = wait_s(project) if wait is None else wait
    req = urllib.request.Request(url, data=json.dumps({"job": job, "brief": brief}).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {env.get(access.TOKEN_ENV) or ''}"})
    known: dict = {"ok": False, "job": job, "started_by": "host", "tools_called": []}
    deadline = time.monotonic() + wait
    heard = False
    try:
        with local_http.urlopen(req, min(QUIET_S, wait), env) as r:
            for line in r:
                heard = True
                ev = json.loads(line or b"{}")
                t = ev.get("type")
                if t == "done":
                    if ev.get("error"):
                        raise StoreError(ev["error"])
                    return ev.get("result") or {}
                if t == "started":
                    known |= {k: ev.get(k) for k in ("model", "session", "transcript")}
                elif t == "tool":
                    known["tools_called"].append(ev.get("name"))
                if time.monotonic() > deadline:
                    break
    except urllib.error.HTTPError as e:
        raise StoreError(DOWN.format(why=f"its control endpoint answered {e.code}")) from None
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, local_http.SandboxBlocked):
            raise StoreError(str(reason)) from None
        if not heard:
            raise StoreError(DOWN.format(why=f"its control endpoint did not answer: {reason}")) from None
    known["tools_called"] = first(known["tools_called"])        # a long sub-session's calls: the first, and how many more
    known["error"] = (f"no report from the agents host within {int(wait)}s; the sub-session may still be running there "
                      "(the host ends it at its own bound, revokes its token and counts its spend). What is known is "
                      "here: its session, its transcript and the tools it called")
    return known


def serve(project: Project, store: AgentStore, token: str, body: dict, emit: Callable[[dict], None]) -> dict:
    """In the host (the control endpoint's ``POST /delegate``): the calling session's ``delegate``, run
    here from the host's environment. Refused unless the token is a live Supervisor session's, not a
    delegated sub-session's."""
    grant = access.grant_of(store, {access.TOKEN_ENV: token})
    if grant is None:
        raise StoreError(f"refused: {access.TOKEN_ENV} was not issued by the host, or its session has ended")
    if grant.get("delegated"):
        raise StoreError(delegate.AGAIN)
    role = grant["role"]
    a = {"job": body.get("job"), "brief": body.get("brief")}
    access.authorize(project, "delegate", role)
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(BEAT):
            emit({"type": "wait"})
    threading.Thread(target=beat, daemon=True, name="delegate-wait").start()
    try:
        with store.events.acting_as(role):
            out = delegate.delegate(project, store, a, role, env={}, by_host=True, on_event=emit)
            return ledger.record(store, role, "delegate", a, out)
    finally:
        stop.set()
