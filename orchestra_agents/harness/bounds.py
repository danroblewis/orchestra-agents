"""The Supervisor's bounds in Claude Code's own settings (SPEC §10, "The Supervisor's bounds";
bootstrap run 2, 2026-11-02): the settings file the harness writes and passes with ``--settings``.

Claude Code enforces ``permissions.deny`` in every mode, ``--dangerously-skip-permissions``
included, and runs PreToolUse hooks before the permission rules. What the settings can hold:

- **reads outside the project and the state directory**: a PreToolUse hook (``bounds_hook.py``)
  denies Read, Grep, Glob and NotebookRead outside the session's roots; Read rules deny the usual
  credential stores outright, which Claude Code also applies to ``cat``, ``head`` and the like in
  Bash;
- **the home directory and the whole disk from the shell**: ``cd ~``, ``ls ~``, ``ls ~/.config``, ``find /``,
  ``find ~``, and ``env``/``printenv`` (listing variable names to find a token's);
- **probing a port or a host**: ``curl``, ``wget``, ``nc``, ``nmap``, ``telnet``, ``lsof -i``,
  ``netstat``, ``ss``, and WebFetch to localhost;
- **running the project's code, build or tests**: the usual build and test tools.

Claude Code says its Bash rules are no security boundary (``sh -c '…'``, ``/usr/bin/curl`` or a
script that opens files itself are not matched), so the prompt carries every bound too
(``prompts/supervisor_core.md``); what only the prompt holds: other repositories by a relative path,
a port read by a program it writes, a system the owner has not named, and credential files it
might list or offer."""

from __future__ import annotations

import json
import shlex
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).with_name("bounds_hook.py")
CREDENTIAL_STORES = ("~/.config/**", "~/.ssh/**", "~/.aws/**", "~/.gnupg/**", "~/.docker/**", "~/.kube/**",
                     "~/.netrc", "~/.git-credentials", "~/.npmrc", "~/.pypirc", "~/Library/Keychains/**")
# the home directory itself and its dot-directories (a project under the home directory is reached by its full path)
SHELL_WIDE = ("cd ~", "cd ~/", "cd ~/.*", "cd $HOME", "cd $HOME/.*", "ls ~", "ls ~/", "ls ~/.*", "ls * ~", "ls * ~/.*",
              "find /", "find / *", "find ~", "find ~ *", "find ~/", "find ~/ *", "env", "printenv", "printenv *")
NETWORK = ("curl *", "wget *", "nc *", "ncat *", "nmap *", "telnet *", "lsof -i*", "netstat *", "ss *")
PROJECT_CODE = ("pytest *", "pytest", "python -m pytest *", "python3 -m pytest *", "py.test *", "uv run *", "uv sync *",
                "uv pip install *", "pip install *", "cargo *", "just *", "just", "make *", "make", "npm *", "pnpm *",
                "yarn *", "npx *", "bun *", "go test *", "go build *", "go run *", "tox *", "nox *", "gradle *",
                "./gradlew *", "mvn *", "bazel *")
WEB_LOCAL = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")


def deny_rules() -> list[str]:
    return ([f"Read({p})" for p in CREDENTIAL_STORES]
            + [f"Bash({c})" for c in SHELL_WIDE + NETWORK + PROJECT_CODE]
            + [f"WebFetch(domain:{h})" for h in WEB_LOCAL])


def roots(bounds: tuple[str, ...], claude_dir: Path) -> list[str]:
    """The directories a bounded session may read in: its bounds (the project's tree and the state
    directory), the installed packages (a plugin is read before it is overridden), Claude Code's
    directory for this working directory's sessions (``claude_dir``: where it saves a long tool
    answer for the session to read; no other project's) and the temporary one."""
    extra = [sys.prefix, sys.base_prefix, str(claude_dir), tempfile.gettempdir(), "/tmp", "/private/tmp"]
    seen: list[str] = []
    for r in [*bounds, *extra]:
        if r and r not in seen:
            seen.append(r)
    return seen


def settings(roots_file: Path) -> dict:
    hook = f"{shlex.quote(sys.executable)} -I {shlex.quote(str(HOOK))} {shlex.quote(str(roots_file))}"
    return {"permissions": {"deny": deny_rules()},
            "hooks": {"PreToolUse": [{"matcher": "Read|Grep|Glob|NotebookRead",
                                      "hooks": [{"type": "command", "command": hook, "timeout": 10}]}]}}


def write(directory: Path, session_id: str, bounds: tuple[str, ...], claude_dir: Path) -> Path:
    """The session's settings file (and its roots beside it); its path, for ``--settings``."""
    roots_file = directory / f"{session_id}.bounds.json"
    roots_file.write_text(json.dumps(roots(bounds, claude_dir), indent=1) + "\n", encoding="utf-8")
    p = directory / f"{session_id}.settings.json"
    p.write_text(json.dumps(settings(roots_file), indent=1) + "\n", encoding="utf-8")
    return p
