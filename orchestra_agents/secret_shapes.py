"""What looks like a secret in a line of ``orchestra.toml``, and the file's text with them hidden.

``orchestra.toml`` is committed to the base branch and every change to it is announced on the bus,
so a password, a token or a key written into it is published twice. The Supervisor's setup tools
refuse a change that adds one (``toml_edit.change``), and ``read_config`` hides any already there
before the text reaches a session. What counts is a shape, not a proof: a URL with a user and a
password in it (``https://admin:hunter2@host``), a query parameter named for a secret (``?token=``,
``&api_key=``), a value given after ``--token``, ``--password``, ``--secret`` or ``--api-key``, a
``Bearer`` header, and the shapes of common tokens (GitHub's ``ghp_``/``gho_``/``github_pat_``,
Slack's ``xoxb-``, AWS's ``AKIA``, ``sk-`` API keys). A value read from the environment
(``--token $GITHUB_TOKEN``) is not one: that is how a secret should be passed."""

from __future__ import annotations

import re

_NAMED = r"(?:access_|api_?|client_|auth_)?(?:token|key|secret|password|passwd)"
# each shape with the part kept when it is hidden (a URL's scheme, a parameter's or option's name)
SHAPES: list[tuple[str, re.Pattern]] = [
    ("a user and password in a URL", re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@'\"]+:[^/\s@'\"]+(?=@)", re.IGNORECASE)),
    ("a secret in a URL's query", re.compile(rf"([?&]{_NAMED}=)(?!\$)[^&\s#'\"]+", re.IGNORECASE)),
    ("a value after a secret's option", re.compile(r"(--(?:token|password|passwd|secret|api-key|api_key|apikey)(?:=|\s+))(?![$-])[^\s'\"]+", re.IGNORECASE)),
    ("a bearer token", re.compile(r"(\bBearer\s+)(?!\$)[A-Za-z0-9._~+/=-]{8,}")),
    ("a GitHub token", re.compile(r"()\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("a Slack token", re.compile(r"()\bxox[bpasr]-[A-Za-z0-9-]{10,}")),
    ("an AWS access key", re.compile(r"()\bAKIA[0-9A-Z]{16}\b")),
    ("an API key (sk-…)", re.compile(r"()\bsk-[A-Za-z0-9_-]{16,}")),
]
# a key in the file whose value is a secret by its name (``password = ...``, ``api_key = ...``, ``bearer = ...``),
# not a path to one or the name of a variable holding one (``token_file``, ``password_env``)
_KEY = re.compile(r"""(?P<pre>^\s*|[{,]\s*)(?P<k>[\w-]*(?:password|passwd|token|secret|bearer)[\w-]*|[\w-]+[_-]key|apikey)"""
                  r"""(?P<eq>\s*=\s*)(?P<v>"(?:[^"\\]|\\.)*"|'[^']*'|[^\s,}\]]+)""", re.IGNORECASE | re.MULTILINE)
_KEPT = ("_file", "_path", "_env", "_var", "-file", "-path")


def secret_in(text: str) -> str | None:
    """What in ``text`` looks like a secret, with only its start shown, or None."""
    for what, pattern in SHAPES:
        if m := pattern.search(text):
            return f"{what}: {m.group(0)[:len(m.group(1)) + 4]}…"
    return None


def redact(text: str) -> str:
    """``text`` with every secret shape and secret-named key's value hidden, and a URL's user."""
    def key(m: re.Match) -> str:
        if m.group("k").lower().endswith(_KEPT):
            return m.group(0)
        return f'{m.group("pre")}{m.group("k")}{m.group("eq")}"<hidden>"'
    text = _KEY.sub(key, text)
    text = re.sub(r"(\b[a-z][a-z0-9+.-]*://)[^/\s@'\"]+@", r"\1<hidden>@", text, flags=re.IGNORECASE)
    for _, pattern in SHAPES[1:]:
        text = pattern.sub(r"\1<hidden>", text)
    return text
