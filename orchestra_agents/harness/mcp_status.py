"""Whether Claude Code reached Orchestra's MCP server, and what to do when it did not.

Claude Code reports every MCP server it loaded in the stream's first event (``system``/``init``,
``mcp_servers``: name and status) before the model is called. A turn without the ``orchestra``
server is a turn without Orchestra's tools: the model then reaches for whatever it has (Claude
Code's own todo list) and reports work that never happened. So such a turn is ended there, as a
failure that says why and what to check, and no Claude Code turn starts again for a while
(``Backoff``): a broken server or a policy that blocks it fails the same way every time.

That is for ``[agents] tools_via = "mcp"``. With ``auto`` (the default) a turn that finds no server
is not failed: the host's sessions call the tools from their shell from then on (``Fallback``,
``bash_tools.py``), and that turn runs again so, before the model has done anything."""

from __future__ import annotations

import shlex
import sys
import threading
import time
from pathlib import Path

SERVER = "orchestra"
BACKOFF_FIRST_S, BACKOFF_MAX_S = 5 * 60, 60 * 60


def managed_dir() -> Path:
    """Where an organisation's managed Claude Code settings live on this platform."""
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode")
    if sys.platform.startswith("win"):
        return Path("C:/Program Files/ClaudeCode")
    return Path("/etc/claude-code")


def policy_files(config_dir: Path) -> list[Path]:
    """The settings that can block an MCP server: the managed ones (``managed-mcp.json``, and
    ``allowedMcpServers``/``deniedMcpServers`` in ``managed-settings.json``) and the user's."""
    d = managed_dir()
    return [d / "managed-mcp.json", d / "managed-settings.json", config_dir / "settings.json"]


def status(init: dict) -> str | None:
    """The ``orchestra`` server's status in an init event; None when it is not listed at all."""
    for s in init.get("mcp_servers") or []:
        if isinstance(s, dict) and s.get("name") == SERVER:
            return str(s.get("status") or "unknown")
    return None


def problem(init: dict, *, config_dir: Path, try_cmd: str, log_path: Path) -> str | None:
    """None when the server is connected; otherwise what the human reads: what happened, the two
    likely causes and what to check for each."""
    st = status(init)
    if st == "connected":
        return None
    files = policy_files(config_dir)
    found = [str(p) for p in files if p.exists()]
    said = f"its status is `{st}`" if st else "it was not loaded at all (Claude Code did not list it)"
    policy = (f"1. An organisation policy blocks MCP servers. Check `managed-mcp.json` and `allowedMcpServers` / "
              f"`deniedMcpServers` in `managed-settings.json` under `{managed_dir()}`, and `{config_dir / 'settings.json'}`"
              + (f" (present here: {', '.join(f'`{f}`' for f in found)})." if found else " (none of them exists here)."))
    start = (f"2. The server fails to start. Try it by hand: `{try_cmd}` should print one JSON line (its `initialize` "
             f"reply); a traceback is the reason. The server writes why it stopped to `{log_path}`.")
    head = (f"Orchestra's tools are not available to Claude Code: the MCP server `{SERVER}` did not connect ({said}). "
            "Orchestra stopped the turn before the model ran, so nothing was done: no ticket was created, nothing changed, and no other "
            "task list was used.")
    bash = ("Or let the agents call Orchestra's tools from their shell instead of the MCP server: set `tools_via = \"bash\"` "
            "(or `\"auto\"`, which switches when the server is blocked) under `[agents]` in orchestra.toml.")
    return f"{head}\n\nLikely causes:\n{policy}\n{start}\n\n{bash}"


def try_command(python: str, env: dict[str, str]) -> str:
    """The command a human runs to start the server as Claude Code does, and see it answer."""
    init = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
    keep = {k: env[k] for k in ("ORCHESTRA_REPO", "ORCHESTRA_TOOLS") if env.get(k)}
    assign = " ".join(f"{k}={shlex.quote(v)}" for k, v in keep.items())
    return f"echo {shlex.quote(init)} | {assign + ' ' if assign else ''}{shlex.quote(python)} -m orchestra_agents.harness.mcp"


class Backoff:
    """After a turn that found no server, no Claude Code turn starts until the wait is over (five
    minutes, doubling to an hour), the settings that could block the server change, or the host
    restarts (this lives in the host's process). A turn asked for meanwhile fails at once with
    the same reason, without starting Claude Code."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._down: dict | None = None

    @staticmethod
    def _stamp(files: list[Path]) -> tuple:
        out = []
        for p in files:
            try:
                st = p.stat()
                out.append((str(p), st.st_mtime_ns, st.st_size))
            except OSError:
                out.append((str(p), None, None))
        return tuple(out)

    def failed(self, message: str, files: list[Path], now: float | None = None) -> float:
        """Record a failure; returns when the next turn may start."""
        now = time.time() if now is None else now
        with self._lock:
            wait = min(BACKOFF_MAX_S, (self._down or {}).get("wait", BACKOFF_FIRST_S / 2) * 2)
            self._down = {"message": message, "stamp": self._stamp(files), "until": now + wait, "wait": wait}
            return now + wait

    def ok(self) -> None:
        with self._lock:
            self._down = None

    def refusal(self, files: list[Path], now: float | None = None) -> str | None:
        """The reason a turn may not start now, or None."""
        now = time.time() if now is None else now
        with self._lock:
            d = self._down
            if d is None or now >= d["until"] or self._stamp(files) != d["stamp"]:
                return None
            return f"{d['message']}\n\n{until(d['until'])}"


def until(at: float) -> str:
    return (f"Orchestra will not start another Claude Code turn until {time.strftime('%H:%M', time.localtime(at))}, "
            "or until those settings change or Orchestra restarts.")


BACKOFF = Backoff()


class Fallback:
    """With ``tools_via = "auto"``: whether the MCP server was found not connected in this host's
    lifetime, after which every Claude Code session calls the tools from its shell (a policy that
    blocks the server for one session blocks it for all) until the host restarts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.why: str | None = None

    def switch(self, why: str) -> bool:
        """Record that the server is not reachable; True the first time, so it is said once."""
        with self._lock:
            first = self.why is None
            self.why = self.why or why
            return first

    @property
    def on(self) -> bool:
        return self.why is not None


FALLBACK = Fallback()
SWITCHED = ("Orchestra's MCP server did not connect in Claude Code (an organisation policy may block it, or it failed to "
            "start), so from now on the agents call Orchestra's tools from their shell (`orchestra agents tool`) instead "
            "([agents] tools_via = \"auto\"). Nothing was lost: the turn ran again that way. To make it permanent, set "
            "tools_via = \"bash\"; to try the MCP server again, restart Orchestra. Why the server did not connect:\n\n")
