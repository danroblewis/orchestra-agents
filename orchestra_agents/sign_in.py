"""A session that could not sign in to its model's provider, said plainly (the owner, 2026-11-04: a
delegated job "got a 401 error from my LLM service, but I am still talking to the Supervisor").

A turn whose error or text reads as the provider's refusal of the credential (401 or 403, "invalid
api key", an OAuth failure, "please run /login") is an authentication failure. Its report says where the
session was started (the agents host, with the host's environment, or another process), which of the
harness's sign-in variables (``Harness.sign_in``) and ``[agents] pass_env`` names the host's
environment has, by name only, and what to set. A value never appears: the provider's own message is
cut short and every value of those variables is blanked from it."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

FAILED = re.compile(r"\b(401|403)\b|invalid[ _-]?(api[ _-]?key|x-api-key|bearer|token|credentials?)|authentication[ _]"
                    r"(error|failed)|unauthori[sz]ed|oauth|please run /login|not logged in|permission_error|forbidden",
                    re.IGNORECASE)
SAID = 240          # the most of the provider's own message the report quotes


def failed(*said: str | None) -> bool:
    """Whether a turn's error (or its last text) is the provider refusing the session's sign-in."""
    return any(s and FAILED.search(s) for s in said)


def _blank(text: str, env: Mapping[str, str], names) -> str:
    for n in names:
        v = env.get(n) or ""
        if len(v) >= 4:
            text = text.replace(v, "<" + n + ">")
    return text


def report(harness, said: str, *, by_host: bool, pass_env: tuple[str, ...] = (),
           environ: Mapping[str, str] | None = None) -> str:
    """The plain report of a failed sign-in: what the provider said (short, no value), where the
    session was started, which sign-in variables that environment has (names only) and what to set."""
    env = os.environ if environ is None else environ
    known = tuple(dict.fromkeys((*harness.sign_in(dict(env)), *pass_env)))
    have = [n for n in known if env.get(n)]
    quoted = " ".join(_blank(said or "", env, known).split())
    quoted = quoted if len(quoted) <= SAID else quoted[:SAID] + " …"
    where = ("It was started by the agents host, with the host's environment, the same way as the Supervisor's own session"
             if by_host else "It was started by the tool command's own process, not by the agents host, so it may not have "
                             "the host's sign-in")
    has = (f"Of the {harness.name} harness's sign-in variables, the host's environment has: {', '.join(have)}"
           if have else f"The host's environment has none of the {harness.name} harness's sign-in variables, so it "
                        "signs in with the harness's saved login (for Claude Code: the macOS keychain or its config "
                        "directory's settings, an apiKeyHelper among them)")
    return (f"The sub-session could not sign in to the model's provider (it said: {quoted or 'no message'}). {where}. "
            f"{has}. If your provider needs another variable, set it in the environment `orchestra run` starts with; "
            "if it is set there and still does not reach the session, name it in [agents] pass_env in your private "
            "overlay (the name only, as pass_env = [\"MY_GATEWAY_KEY\"]) and restart `orchestra run`. Tell the owner "
            "this, in these words; never ask for or print the value.")
