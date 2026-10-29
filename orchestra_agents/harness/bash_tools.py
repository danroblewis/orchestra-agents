"""Orchestra's tools called from the session's shell, for a harness whose MCP server is blocked or
not wanted (``[agents] tools_via``).

Some organisations allow Claude Code only the MCP servers their policy lists, and getting
Orchestra's listed is often harder than it is worth; every session can run commands. So a session
can call each of its tools with ``orchestra agents tool <name> '<json>'`` (``cli.cmd_tool``), which
goes through exactly the MCP server's path (``caller.Caller``). This module is what a harness needs
for that: the command as the session should type it, and the part of the system prompt that says
how to use it. Every harness that offers the bash transport uses these, so the prompt and the
command are the same whichever product runs the agent."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path

MODES = ("mcp", "bash", "auto")
LINE_MAX = 140          # a tool's line in the prompt: its first sentence, cut here (`--help` has the rest)


def command(path: str | None = None) -> str:
    """How the session runs the command: ``orchestra agents tool`` when the ``orchestra`` on the
    session's PATH is this installation's, else this interpreter by its absolute path, so the call
    never depends on the session's PATH or its working directory (``-P``: a checkout in the working
    directory is never imported in place of the installed package)."""
    found = shutil.which("orchestra", path=path if path is not None else os.environ.get("PATH"))
    if found and Path(found).resolve().parent == Path(sys.executable).parent.resolve():
        return "orchestra agents tool"
    return f"{shlex.quote(sys.executable)} -P -m orchestra agents tool"


def _line(description: str) -> str:
    first = description.split(". ")[0].strip().rstrip(".")
    return first if len(first) <= LINE_MAX else first[:LINE_MAX - 1].rstrip() + "…"


def prompt(groups: list[str] | tuple[str, ...], path: str | None = None) -> str:
    """The system prompt's section for a session whose tools are called from the shell: the
    command, how to pass arguments, and each tool by name with one line (schemas on ``--help``)."""
    # the catalogue is imported here, not at the top: the harness package loads without it
    from .tools import tools_for
    cmd = command(path)
    tools = tools_for(groups)
    listed = "\n".join(f"- `{t.name}`: {_line(t.description)}" for t in tools) or "- (none)"
    return f"""## Orchestra's tools: run them with your shell

In this session Orchestra's tools are not among the tools you are given: you call each one by
running a command with your Bash (shell) tool. Wherever this prompt or a message says to use your
tools or to call one (call `report_ticket`, call `hand_to_coordinator`, ...), run:

    {cmd} <tool> '<arguments as one JSON object>'

For example: `{cmd} {tools[0].name if tools else "<tool>"} '{{}}'`. For long arguments, give `-`
and the JSON on stdin: `{cmd} <tool> - <<'JSON'` ... `JSON`. `{cmd} <tool> --help` prints a tool's
arguments and what each means; `{cmd} --list` lists your tools. The answer is printed on stdout;
a call that is refused or fails prints `{{"ok": false, "error": ...}}` and exits non-zero: read the
error, fix the call, and try again. The command knows who you are from this session's environment:
run it as given, never set, change or print any ORCHESTRA_ variable, and never use another task or
todo list in its place.

Your tools:
{listed}
"""


def parse(cmd: str) -> tuple[str, dict] | None:
    """The tool call a shell command makes, as (tool, arguments), when it runs ``orchestra agents
    tool``; None otherwise. So a transcript shows a call from the shell as the tool it called. The
    arguments are {} when they came on stdin or cannot be read."""
    if "agents" not in cmd or "tool" not in cmd:
        return None
    from .tools import BY_NAME
    words = None
    for text in (cmd, cmd.split("\n", 1)[0]):        # the whole command; or its first line, before a heredoc
        try:
            words = shlex.split(text)
            break
        except ValueError:
            continue
    for i in range(len(words or []) - 2):
        if words[i] == "agents" and words[i + 1] == "tool" and words[i + 2] in BY_NAME:
            rest = words[i + 3:i + 4]
            if rest and rest[0] in ("--help", "-h", "--list"):
                return None                             # a question about the tool, not a call
            try:
                args = json.loads(rest[0]) if rest and rest[0] != "-" else {}
            except ValueError:
                args = {}
            return words[i + 2], args if isinstance(args, dict) else {}
    if words and words[-1] != cmd:                      # a shell running the command (bash -lc '...', as Codex does)
        return parse(words[-1])
    return None
