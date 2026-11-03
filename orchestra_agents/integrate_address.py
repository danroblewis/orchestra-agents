"""Where a connected system's address comes from: never a guess (bootstrap run 3: the Supervisor
approved Slack with the template's ``https://slack.com``, which the owner never gave, and the first
poll sent the token there).

``integrate_system`` and ``approve_integration`` take a ``base_url`` only from

- **the owner's words in this session**: its host (and port, when one is given) is in one of their
  messages (``design.said_in_session``, and this turn's), the quote's message among them; or
- **this machine**: a loopback address (the bridges' ``is_local``: a kit's fake, or a service the
  kit's probe found running here), which no token leaves the machine for.

A template's default, an adopted collector's file and the Supervisor's own knowledge of a vendor's
usual host are none of these: the answer asks for the address in one sentence, naming the
template's value for the owner to confirm in their words, and nothing is approved."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .design import said_in_session, turn_of
from .store import AgentStore

OWNER, HERE = "the owner's words", "this machine"


def _local(url: str) -> bool:
    try:
        from orchestra_bridges.local import is_local
    except ImportError:
        host = (urlsplit(url).hostname or "").lower()
        return host in ("localhost", "::1") or host.startswith("127.")
    return is_local(url)


def _messages(store: AgentStore) -> list[str]:
    return [r.get("message") or "" for r in said_in_session(store)] + [turn_of(store).get("message") or ""]


def given(store: AgentStore, url: str | None) -> str | None:
    """Where ``url`` came from (``OWNER`` or ``HERE``), or None when it is a guess."""
    if not url:
        return None
    if _local(url):
        return HERE
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        return None
    said = host + (f":{parts.port}" if parts.port else "")
    pat = re.compile(rf"(?<![\w.-]){re.escape(said)}(?![\w-]|\.\w)", re.IGNORECASE)
    return OWNER if any(pat.search(m) for m in _messages(store)) else None


def ask(title: str, default: str | None) -> str:
    """The one sentence that asks for the address."""
    named = f" (the template's default is {default}: say it in your words if that is yours)" if default else ""
    return f"What is {title}'s address, its API's base URL{named}? I never guess one, so nothing is approved until you say it."


def refused(title: str, url: str) -> str:
    return (f"base_url {url} is not in the owner's words this session and is not on this machine: an address is never guessed "
            f"(a template's default, a vendor's usual host). Ask the owner in one sentence (\"{ask(title, None)}\"), naming "
            f"{url} for them to confirm in their words if you think it is theirs; nothing was written")
