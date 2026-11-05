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
``excludedCommands`` entry (the command would run with no sandbox at all). The tool command also writes under
the project's state directory (``~/.orchestrator/<key>/``: the event log, the agents' records),
outside the working directory the sandbox lets a command write to; the settings list exactly that
directory in ``sandbox.filesystem.allowWrite`` (the docs' "Configure sandboxing": absolute paths,
merged across scopes; ``--settings`` entries apply even when the sandbox is admin-required, unless a
managed lock covers them). The credential files the configuration names, exactly
(``credential_paths.named``: a ``[[sources]]`` entry's ``token_file``, ``[scm]``'s, a kit's), are
listed in ``sandbox.filesystem.allowRead``, so a source's probe in the tool command can open its
token: the bounds' ``Read(~/.config/**)`` deny becomes a sandbox read denial for every sandboxed
command, and the docs' rule is that where read rules overlap the narrower path applies, so an exact
file re-opens inside the denied directory ("Configure sandboxing"). The permission rule itself stays:
the Read tool and ``cat`` are still refused the file, which only the tool command opens.

``[agents] sandbox_escape`` (default true, the owner, 2026-11-04: "it's fine to escape the
sandbox"): the settings leave Claude Code's unsandboxed retry as it is, the session's environment
says so to the tool command (``local_http.ESCAPE_ENV``), whose refusal then tells the model to run the
command again outside the sandbox (``dangerouslyDisableSandbox: true``) and to say what to ask IT for
only if that is refused too, and the harness passes the refusal through. With it false the settings
turn the retry off (``allowUnsandboxedCommands: false``, which in this harness's
``--dangerously-skip-permissions`` mode would otherwise run without a prompt), and an organisation's
refusal (managed settings win: with ``allowManagedDomainsOnly`` only their list counts) stops the
turn: the tool command says so (``local_http.MARK``), the harness stops the turn at that answer and
tells the owner, once, the setting to ask IT for (``Blocked``), so the model never gets to try the
command outside the sandbox. A refused write is said the same way (``sandbox_writes.MARK``), naming
the path."""

from __future__ import annotations

import re
import urllib.parse

from .. import sandbox_writes
from ..local_http import MARK, allowed, setting

PORT = re.compile(re.escape(MARK) + r" at 127\.0\.0\.1:(\d+)")
PATH = re.compile(re.escape(sandbox_writes.MARK) + r" (/[^\s(]*)")


def _core(repo: str | None):
    from ..config import load_project
    return load_project(repo or None).orchestra


def ports(repo: str | None) -> list[int]:
    """The ports of Orchestra's servers for the project on 127.0.0.1, as its runtime record has them
    now: the host's events endpoint, the web server, and the agents host's control endpoint (where
    ``delegate`` asks the host to run a job, delegate_host.py). [] when there is no project or record."""
    try:
        from orchestra import endpoint, runtime
        core = _core(repo)
        out: list[int] = _control(repo)
        rec = endpoint.recorded(core)
        if rec and (u := urllib.parse.urlsplit(str(rec.get("url") or ""))).hostname == "127.0.0.1" and u.port:
            out.append(u.port)
        web = runtime.read(core).get("web") or {}
        if web.get("port") and web.get("pid") and runtime.alive(web["pid"]):
            out.append(int(web["port"]))
        return sorted(set(out))
    except Exception:  # noqa: BLE001 - no project to read: nothing to allow
        return []


def _control(repo: str | None) -> list[int]:
    try:
        from orchestra.runtime import alive

        from ..config import load_project
        from ..store import AgentStore
        rec = AgentStore(load_project(repo or None)).runtime().get("control") or {}
        return [int(rec["port"])] if rec.get("port") and rec.get("pid") and alive(int(rec["pid"])) else []
    except Exception:  # noqa: BLE001 - no agents record: no control port to allow
        return []


def state_dir(repo: str | None) -> str | None:
    """The project's state directory (``~/.orchestrator/<key>``), as the project says it; None without one."""
    try:
        return str(_core(repo).state_dir.resolve())
    except Exception:  # noqa: BLE001 - no project to read: nothing to allow
        return None


def readable(repo: str | None) -> list[str]:
    """The exact credential files the project's configuration names (``credential_paths.named``); [] without a project."""
    try:
        from ..config import load_project
        from ..credential_paths import named
        return named(load_project(repo or None))
    except Exception:  # noqa: BLE001 - no project to read: nothing to allow
        return []


def settings(listed: list[int], writable: str | None = None, escape: bool = True,
             readable: list[str] | tuple[str, ...] = ()) -> dict:
    """The session settings' ``sandbox`` part: exactly these addresses, the state directory to write
    in, exactly these credential files to read, and, unless ``escape``, no unsandboxed retry."""
    out: dict = {"network": {"allowedDomains": [allowed(p) for p in listed]}}
    fs: dict = {}
    if writable:
        fs["allowWrite"] = [writable]
    if readable:
        fs["allowRead"] = list(readable)
    if fs:
        out["filesystem"] = fs
    return {"sandbox": out if escape else out | {"allowUnsandboxedCommands": False}}


def result_text(content) -> str:
    if isinstance(content, list):
        return "".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return str(content or "")


class Blocked:
    """The sandbox refused the tool command: said once in full (per port), then in one line."""

    def __init__(self) -> None:
        self.said: set = set()

    def found(self, text: str) -> tuple[str, str] | None:
        """What the tool command's answer says the sandbox blocked: ("port", its port) or ("path", the
        state directory); None when it says no such thing."""
        if (m := PATH.search(text)) is not None:
            return "path", m.group(1)
        if (m := PORT.search(text)) is not None:
            return "port", m.group(1)
        return ("port", "?") if MARK in text else None

    def report(self, found: tuple[str, str]) -> str:
        kind, value = found
        return self._path(value) if kind == "path" else self._port(value)

    def _path(self, path: str) -> str:
        if ("path", path) in self.said:
            return (f"Orchestra's tools are still blocked by Claude Code's sandbox from writing in {path}; the turn was "
                    f"stopped again. The managed settings need {sandbox_writes.setting(path)}: ask IT, as said before.")
        self.said.add(("path", path))
        return (f"Claude Code's sandbox blocked Orchestra's tool command from writing in Orchestra's state directory "
                f"{path} (its event log and records), so the turn was stopped instead of the command being run outside "
                f"the sandbox. The session's own settings allow writing there (sandbox.filesystem.allowWrite), so this "
                f"machine's managed settings refuse it. Ask IT to allow that path in the managed settings: "
                f"{sandbox_writes.setting(path)}.")

    def _port(self, port: str) -> str:
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
