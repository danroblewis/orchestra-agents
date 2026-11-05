"""One decision: the cache, the daily cap, the call, the answer judged against the declaration.

The outcome is the model's label when it is one of the declared labels at ``min_confidence`` or
above; otherwise the declared ``fallback``, with why in the reason: the label was not one of the
declared ones, the confidence was below the bound, the model could not be reached or refused the
token, the token file is missing or readable by others (it must be chmod 600), or the daily cap is
reached. Each decision is one line of the valve's log (``ledger.py``)."""

from __future__ import annotations

import stat
import time
from dataclasses import dataclass
from pathlib import Path

from . import apis, http
from .declaration import Valve
from .inputs import fingerprint
from .ledger import Ledger


@dataclass
class Decision:
    outcome: str
    label: str | None
    score: float | None
    reason: str
    cached: bool = False
    fell_back: str = ""            # why the outcome is the fallback, when it is

    def fact(self, v: Valve) -> dict:
        """The item's field ``valve_<name>``: what the Items drawer shows of why."""
        out = {"outcome": self.outcome, "label": self.label, "score": self.score, "reason": self.reason,
               "cached": self.cached, "valve": v.name}
        if self.fell_back:
            out["fallback"] = self.fell_back
        return out

    def line(self) -> str:
        score = "" if self.score is None else f" ({self.score:.2f})"
        return f"{self.outcome}{score}: {self.reason}"[:300]


class TokenError(Exception):
    pass


def token_of(v: Valve) -> str:
    """The token from its file, which only its owner may read; raises ``TokenError`` saying why not
    (never with the token)."""
    if not v.token_file:
        return ""
    path = Path(v.token_file).expanduser()
    try:
        st = path.stat()
    except OSError:
        raise TokenError(f"the token file {path} is not there") from None
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise TokenError(f"the token file {path} is readable by others: chmod 600 it") from None
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise TokenError(f"the token file {path} cannot be read") from None
    if not token:
        raise TokenError(f"the token file {path} is empty")
    return token


def _fallback(v: Valve, why: str, label=None, score=None, reason: str = "", cached=False) -> Decision:
    said = why if not reason else f"{why}; the model said: {reason}"
    return Decision(v.fallback, label, score, said[:200], cached, why)


def judge(v: Valve, label: str, score: float, reason: str, cached: bool) -> Decision:
    if label not in v.labels:
        return _fallback(v, f"the model answered {label[:40]!r}, not one of {', '.join(v.labels)}", label, score, cached=cached)
    if score < v.min_confidence:
        return _fallback(v, f"{label} at {score:.2f} is below the declared {v.min_confidence:.2f}", label, score, reason,
                         cached)
    return Decision(label, label, score, reason or f"the model chose {label}", cached)


def decide(v: Valve, inputs: dict, ledger: Ledger, sleep=time.sleep) -> Decision:
    fp = fingerprint(v, inputs)
    line: dict = {"at": time.time(), "fingerprint": fp[:16], "valve": v.name, "api": v.api}
    with ledger.locked():
        hit = ledger.cached(fp)
        if hit is not None:
            d = judge(v, str(hit.get("label")), float(hit.get("score") or 0.0), str(hit.get("reason") or ""), True)
            line["cached"] = True
        else:
            d = _ask(v, inputs, fp, ledger, line, sleep)
        ledger.tally(d.outcome)
        line.update({"label": d.label, "score": d.score, "outcome": d.outcome, "fell_back": d.fell_back or None})
        ledger.log({k: x for k, x in line.items() if x is not None})
    return d


def _ask(v: Valve, inputs: dict, fp: str, ledger: Ledger, line: dict, sleep) -> Decision:
    if ledger.calls_today() >= v.max_calls_per_day:
        return _fallback(v, f"the daily cap of {v.max_calls_per_day} calls is reached")
    try:
        token = token_of(v)
    except TokenError as e:
        return _fallback(v, str(e))
    url, body, headers = apis.request(v, inputs, token)
    reply = http.post(url, body, headers, timeout_s=v.timeout_s, retries=v.retries, backoff_s=v.backoff_s, sleep=sleep,
                      on_attempt=lambda: ledger.spend(v.max_calls_per_day))
    line.update({"status": reply.status, "attempts": reply.attempts, "latency_s": round(reply.latency_s, 3)})
    if not reply.ok:
        why = reply.why
        if reply.status in (401, 403):
            why = f"the model refused the token ({reply.status})"
        elif reply.attempts > 1:
            why = f"the model {why} ({reply.attempts} tries)"
        else:
            why = f"the model {why}"
        return _fallback(v, why)
    line.update(apis.usage(reply.body))
    got, why = apis.read(v, reply.body)
    if got is None:
        return _fallback(v, f"the model {why}")
    label, score, reason = got
    if token:                       # a model that echoes the token never puts it on the item
        reason = reason.replace(token, "[the token]")
    if label in v.labels:
        ledger.keep(fp, label, score, reason)
    return judge(v, label, score, reason, False)
