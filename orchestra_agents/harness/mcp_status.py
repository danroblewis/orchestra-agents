"""Whether Claude Code reached Orchestra's MCP server, and what to do when it did not.

Claude Code reports every MCP server it loaded in the stream's first event (``system``/``init``,
``mcp_servers``: name and status) before the model is called. A turn without the ``orchestra``
server is a turn without Orchestra's tools: the model then reaches for whatever it has (Claude
Code's own todo list) and reports work that never happened. So such a turn is ended there, as a
failure that says why and what to check, and no Claude Code turn starts again for a while
(``Backoff``): a broken server or a policy that blocks it fails the same way every time.

A server can also connect and still be of no use: a permission rule in Claude Code's settings
withholds every one of its tools (the init event's ``tools`` lists none of them), or a hook (an
organisation's whitelist of MCP servers, say) refuses each call as the model makes it, which the
stream shows as the call's ``tool_result``, an error that begins ``PreToolUse:<tool> hook error``
(``refusal``). Both are the server being blocked, found at the init event or at the first call.

That is for ``[agents] tools_via = "mcp"``. With ``auto`` (the default) a turn that finds no server
is not failed: the host's sessions call the tools from their shell from then on (``Fallback``,
``bash_tools.py``), and that turn runs again so: before the model has done anything, or, when a
call was refused, from where it stopped (``CONTINUE``)."""

from __future__ import annotations

import re
import shlex
import sys
import threading
import time
from pathlib import Path

SERVER = "orchestra"
TOOL_PREFIX = f"mcp__{SERVER}__"
REASON_KEPT = 600       # a refusal's reason as the human reads it
# What Claude Code puts in a call's result when it did not run it: a PreToolUse hook that exited 2
# or answered "deny" ("PreToolUse:mcp__orchestra__report_ticket hook error: <its reason>"), or a
# permission rule. An error Orchestra's server answers is its own JSON ({"ok": false, ...}).
REFUSED = re.compile(r"^PreToolUse:\S* hook error|\bhook error\b|\bpermission to use \S+ (has been )?denied|\bdenied by\b", re.IGNORECASE)
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


def withheld(init: dict) -> bool:
    """The server connected, but the session was given none of its tools (a permission rule in the
    settings denies them). Judged only when the init event lists the session's tools."""
    tools = init.get("tools")
    return bool(tools) and isinstance(tools, list) and not any(str(t).startswith(TOOL_PREFIX) for t in tools)


def refusal(content) -> str | None:
    """Why Claude Code did not run a call to one of Orchestra's tools (a hook or a permission rule
    refused it), from the call's ``tool_result`` content; None for a call that ran, one the server
    answered with an error included."""
    if isinstance(content, list):
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    text = str(content or "").strip()
    if not text or text.startswith("{") or not REFUSED.search(text):
        return None
    return text if len(text) <= REASON_KEPT else text[:REASON_KEPT - 1] + "…"


def refused_problem(tool: str, reason: str, *, settings: list[Path]) -> str:
    """What the human reads when the server connected but a call to it was refused."""
    where = ", ".join(f"`{p}`" for p in settings)
    return (f"Orchestra's tools are not usable in Claude Code: the MCP server `{SERVER}` connected, but Claude Code refused "
            f"the call to `{tool.removeprefix(TOOL_PREFIX)}` before it reached Orchestra. It said:\n\n{reason}\n\n"
            "A hook or a permission rule in Claude Code's settings blocks the server's tools: an organisation's whitelist of "
            f"MCP servers, typically. Check the hooks (`PreToolUse`) and `permissions` in {where}. Orchestra stopped the turn "
            "there; what the model had done before the call stands, and nothing was reported.\n\n"
            "Or let the agents call Orchestra's tools from their shell instead of the MCP server: set `tools_via = \"bash\"` "
            "(or `\"auto\"`, which switches when the server is blocked) under `[agents]` in orchestra.toml.")


def problem(init: dict, *, config_dir: Path, try_cmd: str, log_path: Path) -> str | None:
    """None when the server is connected; otherwise what the human reads: what happened, the two
    likely causes and what to check for each."""
    st = status(init)
    denied = st == "connected" and withheld(init)
    if st == "connected" and not denied:
        return None
    files = policy_files(config_dir)
    found = [str(p) for p in files if p.exists()]
    said = (f"its status is `{st}`" if st else "it was not loaded at all (Claude Code did not list it)")
    policy = (f"1. An organisation policy blocks MCP servers. Check `managed-mcp.json` and `allowedMcpServers` / "
              f"`deniedMcpServers` in `managed-settings.json` under `{managed_dir()}`, and `{config_dir / 'settings.json'}`"
              + (f" (present here: {', '.join(f'`{f}`' for f in found)})." if found else " (none of them exists here)."))
    start = (f"2. The server fails to start. Try it by hand: `{try_cmd}` should print one JSON line (its `initialize` "
             f"reply); a traceback is the reason. The server writes why it stopped to `{log_path}`.")
    if denied:
        said = "it connected, but the session was given none of its tools: a `permissions` deny rule in the settings withholds them"
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
SWITCHED = ("Orchestra's MCP server did not connect in Claude Code, or Claude Code refused a call to it (an organisation "
            "policy may block it, or it failed to start), so from now on the agents call Orchestra's tools from their shell (`orchestra agents tool`) instead "
            "([agents] tools_via = \"auto\"). Nothing was lost: the turn ran again that way. To make it permanent, set "
            "tools_via = \"bash\"; to try the MCP server again, restart Orchestra. Why:\n\n")
# The turn run again after a refused call: the session resumes with what it did before the call.
CONTINUE = ("[Orchestra] Your call to Orchestra's `{tool}` through its MCP server was refused by this machine's Claude Code "
            "settings, so that turn was stopped there. What you already did stands: carry on from where you were, starting "
            "with the call that was refused, made from your shell as above. What you were asked:\n\n")
# A resumed session that began with the tools through the MCP server and now calls them from its shell
# (Claude Code keeps a resumed session's first system prompt): told once, before its message.
SHELL_NOW = ("[Orchestra] From now on Orchestra's tools are called from your shell, not through its MCP server: the "
             "`mcp__orchestra__*` tools are gone (ToolSearch will not find them). This replaces what your instructions "
             "said about how to call them:\n\n{section}\n\n---\n\n")
