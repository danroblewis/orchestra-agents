"""Calls to Orchestra's own servers on 127.0.0.1 (the host's events endpoint, the web server), made by
the tool command a session runs from its shell, which may be inside Claude Code's sandbox.

In the sandbox a command has no direct route to a server on the machine: Claude Code sets
``NO_PROXY`` so ``localhost`` is reached directly, and the sandbox refuses that connection (macOS:
``Operation not permitted``; Linux: the command's own ``localhost``, where nothing listens). What
the sandbox allows is its proxy, in ``HTTP_PROXY``, which refuses an address on the machine unless
the session's settings list it with its port (``sandbox.network.allowedDomains``:
``"127.0.0.1:<port>"``; https://code.claude.com/docs/en/sandboxing, "Hostnames that resolve to
local addresses"). The Claude Code harness lists exactly Orchestra's ports there
(``harness/sandbox.py``) and sets ``ORCHESTRA_SANDBOX_PROXY`` for the session; with it set, a direct
call that fails is made again through the proxy. A call the sandbox still refuses (an
organisation's managed settings ignore the session's list: ``allowManagedDomainsOnly``) fails with
``MARK`` and the setting it needs. With ``[agents] sandbox_escape`` (the default; the harness sets
``ESCAPE_ENV``) the answer tells the session to run the command again outside the sandbox, and to
tell the owner what to ask IT for only if that is refused too; without it, the harness reads the
answer to stop the turn and tell the owner, and the answer never gives a reason to try the command
outside the sandbox."""

from __future__ import annotations

import errno
import io
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

ENV = "ORCHESTRA_SANDBOX_PROXY"
ESCAPE_ENV = "ORCHESTRA_SANDBOX_ESCAPE"   # "1": a command the sandbox blocks may run again outside it ([agents] sandbox_escape)
MARK = "Claude Code's sandbox blocked the call to Orchestra's local server"
RERUN = ("Run this same command again outside the sandbox (the Bash tool's dangerouslyDisableSandbox: true) and say so "
         "to the owner in one line. ")
GATEWAY = (502, 503, 504)       # the proxy's own answer when nothing listens behind it


def allowed(port: int | str) -> str:
    return f"127.0.0.1:{port}"


def setting(ports) -> str:
    """The setting the sandbox needs for these ports, as it is written in a settings file."""
    listed = ", ".join(f'"{allowed(p)}"' for p in ports)
    return f'"sandbox": {{"network": {{"allowedDomains": [{listed}]}}}}'


def escape_allowed(env: Mapping[str, str] | None = None) -> bool:
    """Whether the session may run a blocked command outside the sandbox (the harness says so: ``ESCAPE_ENV``)."""
    return (os.environ if env is None else env).get(ESCAPE_ENV) == "1"


def blocked(port: int | str | None, env: Mapping[str, str] | None = None) -> str:
    if escape_allowed(env):
        return (f"{MARK} at {allowed(port)}. {RERUN}If it is refused outside the sandbox too (the organisation's managed "
                f"settings forbid running outside it, or allow only their own addresses: allowManagedDomainsOnly), "
                f"stop and tell the owner to ask IT for {setting([port])} in the managed settings.")
    return (f"{MARK} at {allowed(port)}. Never rerun this command outside the sandbox: stop, and tell the owner "
            f"that Claude Code's sandbox needs {setting([port])}. Orchestra's harness sets it for the session; when it "
            f"is blocked anyway, the organisation's managed settings decide the allowed addresses "
            f"(allowManagedDomainsOnly) and only IT can add it there.")


class SandboxBlocked(OSError):
    pass


def _proxy(env: Mapping[str, str]) -> str | None:
    if env.get(ENV) != "1":
        return None
    return env.get("HTTP_PROXY") or env.get("http_proxy") or None


def _denied(reason) -> bool:
    return isinstance(reason, PermissionError) or getattr(reason, "errno", None) in (errno.EPERM, errno.EACCES)


def urlopen(req: urllib.request.Request, timeout: float, env: Mapping[str, str] | None = None):
    """``urllib.request.urlopen`` for a URL on 127.0.0.1: directly, then, in the sandbox, through its
    proxy. Raises as urlopen does; a refusal by the sandbox is a URLError whose reason says ``MARK``."""
    env = os.environ if env is None else env
    port = urllib.parse.urlsplit(req.full_url).port
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        proxy = _proxy(env)
        if proxy is None:
            if _denied(reason):
                raise urllib.error.URLError(SandboxBlocked(blocked(port, env))) from None
            raise
        first = e
    return _via(req, proxy, timeout, port, first, env)


def _via(req: urllib.request.Request, proxy: str, timeout: float, port, first: BaseException,
         env: Mapping[str, str] | None = None):
    hop = urllib.parse.urlsplit(proxy if "://" in proxy else f"http://{proxy}")
    again = urllib.request.Request(req.full_url, data=req.data, headers=dict(req.header_items()), method=req.get_method())
    again.set_proxy(hop.netloc, "http")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        return opener.open(again, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read() or b""
        if e.code == 403 and (e.headers.get("X-Proxy-Error") or (b"blocked" in body.lower() and not body.lstrip().startswith(b"{"))):
            raise urllib.error.URLError(SandboxBlocked(blocked(port, env))) from None
        if e.code in GATEWAY and not body.lstrip().startswith(b"{"):
            raise first from None          # the proxy reached nothing: the server is not there, as the direct call said
        raise urllib.error.HTTPError(e.url, e.code, e.msg, e.headers, io.BytesIO(body)) from None
    except (urllib.error.URLError, OSError):
        raise first from None
