"""One JSON POST to a model, with the standard library: a timeout per attempt, retries with a
doubling back-off on a timeout, a connection failure, a 429 or a 5xx, and none on another 4xx (a
401 is the token's fault, and asking again spends nothing but time). The environment's proxies are
honoured (``HTTPS_PROXY``, ``HTTP_PROXY``, ``NO_PROXY``: the valve's ``machine.json`` passes them,
``spec.env``). What fails is said by its status or its kind, never with the response's body or the
request's headers, so the token never reaches a message."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

RETRIED = {408, 425, 429, 500, 502, 503, 504, 529}
MAX_BODY = 1_000_000


@dataclass
class Reply:
    ok: bool
    body: dict | None = None
    status: int | None = None
    why: str = ""              # what failed, in words, when not ok
    attempts: int = 0
    latency_s: float = 0.0


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler(urllib.request.getproxies_environment()))


def _once(url: str, payload: bytes, headers: dict, timeout_s: float) -> tuple[int | None, dict | None, str]:
    req = urllib.request.Request(url, data=payload, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json", **headers})
    try:
        with opener().open(req, timeout=timeout_s) as r:
            raw = r.read(MAX_BODY + 1)
            status = r.status
    except urllib.error.HTTPError as e:
        return e.code, None, f"answered {e.code}"
    except TimeoutError:
        return None, None, f"timed out after {timeout_s:g} s"
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, TimeoutError | socket.timeout):
            return None, None, f"timed out after {timeout_s:g} s"
        return None, None, f"could not be reached ({type(reason).__name__})"
    except (OSError, ValueError) as e:
        return None, None, f"could not be reached ({type(e).__name__})"
    if len(raw) > MAX_BODY:
        return status, None, "answered more than a megabyte"
    try:
        body = json.loads(raw)
    except ValueError:
        return status, None, "answered something that is not JSON"
    if not isinstance(body, dict):
        return status, None, "answered JSON that is not an object"
    return status, body, ""


def post(url: str, body: dict, headers: dict, *, timeout_s: float, retries: int, backoff_s: float,
         sleep=time.sleep, on_attempt=None) -> Reply:
    """The reply, after at most ``retries`` more attempts; ``on_attempt()`` is called before each
    (the daily cap counts attempts: each may cost)."""
    payload = json.dumps(body).encode("utf-8")
    began = time.monotonic()
    status, why = None, ""
    for n in range(retries + 1):
        if n:
            sleep(backoff_s * 2 ** (n - 1))
        if on_attempt is not None and on_attempt() is False:
            return Reply(False, status=status, why="the daily cap was reached between retries", attempts=n,
                         latency_s=time.monotonic() - began)
        status, got, why = _once(url, payload, headers, timeout_s)
        if got is not None and status is not None and 200 <= status < 300:
            return Reply(True, got, status, attempts=n + 1, latency_s=time.monotonic() - began)
        if status is not None and status not in RETRIED:
            break
    return Reply(False, status=status, why=why or f"answered {status}", attempts=n + 1, latency_s=time.monotonic() - began)
