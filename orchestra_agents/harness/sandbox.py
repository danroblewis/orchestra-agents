"""Claude Code's sandbox and Orchestra's tool command (``[agents] tools_via = "bash"``).

A session whose tools come from its shell runs ``orchestra agents tool``, which calls Orchestra's own
servers on 127.0.0.1: the host's events endpoint (``[events] listen``; a port the system picks
unless given) and the web server (``orchestra web --port``). With the sandbox on
(``sandbox.enabled``, in the owner's or the organisation's settings), a Bash command reaches the
network only through the sandbox's proxy, which refuses an address on the machine unless it is
listed with its port (https://code.claude.com/docs/en/sandboxing, "Hostnames that resolve to local
addresses": ``"allowedDomains": ["127.0.0.1:8080"]``). So the session's settings (``--settings``,
above the user's and the project's files and below managed settings,
https://code.claude.com/docs/en/settings, "Settings precedence") list exactly those ports, and
nothing broader: not ``allowLocalBinding`` (every port on localhost, macOS only), not an
``excludedCommands`` entry (the command would run with no sandbox at all). They also turn off the
unsandboxed retry (``allowUnsandboxedCommands: false``), which in this harness's
``--dangerously-skip-permissions`` mode would run without a prompt.

An organisation's managed settings win over the session's: with ``allowManagedDomainsOnly`` only
their list counts, and the call is still refused. The tool command says so (``local_http.MARK``);
the harness stops the turn at that answer and tells the owner, once, the setting to ask IT for
(``Blocked``), so the model never gets to try the command outside the sandbox."""

from __future__ import annotations

import re
import urllib.parse

from ..local_http import MARK, allowed, setting

PORT = re.compile(re.escape(MARK) + r" at 127\.0\.0\.1:(\d+)")


def ports(repo: str | None) -> list[int]:
    """The ports of Orchestra's servers for the project on 127.0.0.1, as its runtime record has them
    now: the host's events endpoint and the web server. [] when there is no project or record."""
    try:
        from orchestra import endpoint, runtime

        from ..config import load_project
        core = load_project(repo or None).orchestra
        out: list[int] = []
        rec = endpoint.recorded(core)
        if rec and (u := urllib.parse.urlsplit(str(rec.get("url") or ""))).hostname == "127.0.0.1" and u.port:
            out.append(u.port)
        web = runtime.read(core).get("web") or {}
        if web.get("port") and web.get("pid") and runtime.alive(web["pid"]):
            out.append(int(web["port"]))
        return sorted(set(out))
    except Exception:  # noqa: BLE001 - no project to read: nothing to allow
        return []


def settings(listed: list[int]) -> dict:
    """The session settings' ``sandbox`` part: exactly these addresses, and no unsandboxed retry."""
    return {"sandbox": {"network": {"allowedDomains": [allowed(p) for p in listed]}, "allowUnsandboxedCommands": False}}


def result_text(content) -> str:
    if isinstance(content, list):
        return "".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return str(content or "")


class Blocked:
    """The sandbox refused the tool command: said once in full (per port), then in one line."""

    def __init__(self) -> None:
        self.said: set[str] = set()

    def found(self, text: str) -> str | None:
        """The port the tool command's answer says the sandbox blocked; None when it says no such thing."""
        m = PORT.search(text)
        return m.group(1) if m else (None if MARK not in text else "?")

    def report(self, port: str) -> str:
        if port in self.said:
            return (f"Orchestra's tools are still blocked by Claude Code's sandbox at {allowed(port)}; the turn was stopped "
                    f"again. The managed settings need {setting([port])}: ask IT, as said before.")
        self.said.add(port)
        return (f"Claude Code's sandbox blocked Orchestra's tool command from reaching Orchestra's local server at "
                f"{allowed(port)}, so the turn was stopped instead of the command being run outside the sandbox. "
                f"The session's own settings allow that address, so this machine's managed settings refuse it "
                f"(sandbox.network.allowManagedDomainsOnly: only their allowedDomains count). Ask IT to add it to the "
                f"managed settings: {setting([port])}. Keep the port fixed so the entry stays right: "
                f"[events] listen = \"{allowed(port)}\" in orchestra.toml for the host's endpoint, or "
                f"orchestra web --port for the web server.")


BLOCKED = Blocked()
ESCAPE = ("The Supervisor tried to run Orchestra's tool command outside Claude Code's sandbox "
          "(dangerouslyDisableSandbox); the turn was stopped before it could. ")
