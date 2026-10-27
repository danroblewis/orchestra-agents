"""The dummy harness's scripts: what a scripted agent does on a turn, as data (SPEC.md section 6).

A script is a TOML file of ``[[rule]]`` tables, named by ``[agents] dummy_script`` in
``orchestra.toml`` (relative to the repository). On each turn the first rule whose conditions all
hold is played; the built-in rules (``dummy_defaults.toml``, one per built-in role) come after the
file's, so a file says only what differs.

    [[rule]]
    role = "reviewer"                 # required: the agent's role (ORCHESTRA_ROLE), or "*"
    ticket = "DEMO-3"                 # a glob on the ticket id
    prompt = "Count the lines"        # a regular expression searched in the turn's prompt
    turn = 1                          # this session's nth turn (or a list of them)
    checkout = true                   # whether the working directory is a git checkout
    steps = [
      { say = "Reading the diff." },
      { tool = "report_review", args = { verdict = "REJECTED", summary = "Not yet.", feedback = "1. Say more." } },
    ]
    reply = "Rejected once."          # the turn's last message

Steps, played in order: ``say`` (a message), ``write`` (a file, with ``text`` and ``append``),
``run`` (a shell command), ``commit`` (everything, with this message), ``tool`` (one of the
agents' tools, with ``args``), ``sleep`` (seconds), ``fail`` (the turn fails, with this error).
In every string ``{ticket}``, ``{role}``, ``{turn}``, ``{instance}`` and ``{message}`` (the
human's message in a chat turn, else the prompt's first line) are replaced."""

from __future__ import annotations

import fnmatch
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULTS = Path(__file__).with_name("dummy_defaults.toml")
ACTIONS = ("say", "write", "run", "commit", "tool", "sleep", "fail")
MODIFIERS = {"write": {"text", "append"}, "tool": {"args"}}
CONDITIONS = ("role", "ticket", "prompt", "turn", "checkout")
HUMAN = "[The human's message]\n"          # prompting.CHAT_SEAM ends with it


class ScriptError(ValueError):
    pass


@dataclass(frozen=True)
class Turn:
    """What a rule is matched against."""
    role: str
    ticket: str = ""
    prompt: str = ""
    turn: int = 1
    checkout: bool = False
    instance: str = ""

    @property
    def message(self) -> str:
        if HUMAN in self.prompt:
            return self.prompt.split(HUMAN, 1)[1].strip()
        return (self.prompt.strip().splitlines() or [""])[0][:200]

    def fill(self, value):
        """``value`` with the placeholders replaced, in every string inside it."""
        if isinstance(value, str):
            for k in ("ticket", "role", "turn", "instance", "message"):
                value = value.replace("{" + k + "}", str(getattr(self, k)))
            return value
        if isinstance(value, list):
            return [self.fill(v) for v in value]
        if isinstance(value, dict):
            return {k: self.fill(v) for k, v in value.items()}
        return value


@dataclass(frozen=True)
class Rule:
    role: str
    steps: tuple[dict, ...] = ()
    reply: str | None = None
    ticket: str | None = None
    prompt: str | None = None
    turn: tuple[int, ...] = ()
    checkout: bool | None = None
    where: str = ""                       # the file and rule number, for errors

    def matches(self, t: Turn) -> bool:
        return ((self.role == "*" or self.role == t.role)
                and (self.ticket is None or fnmatch.fnmatchcase(t.ticket, self.ticket))
                and (self.prompt is None or re.search(self.prompt, t.prompt) is not None)
                and (not self.turn or t.turn in self.turn)
                and (self.checkout is None or self.checkout == t.checkout))


@dataclass
class Script:
    rules: list[Rule] = field(default_factory=list)

    def pick(self, t: Turn) -> Rule:
        for r in self.rules:
            if r.matches(t):
                return r
        return Rule(role="*", reply="Nothing to do.", where="no rule matched")


def _step(raw, where: str) -> dict:
    if not isinstance(raw, dict):
        raise ScriptError(f"{where}: a step is a table, like {{ say = \"hello\" }}")
    actions = [k for k in raw if k in ACTIONS]
    if len(actions) != 1:
        raise ScriptError(f"{where}: a step has exactly one of {', '.join(ACTIONS)}; this one has {actions or 'none'}")
    action = actions[0]
    extra = set(raw) - {action} - MODIFIERS.get(action, set())
    if extra:
        raise ScriptError(f"{where}: a {action} step does not take {', '.join(sorted(extra))}")
    if action == "sleep" and not isinstance(raw[action], int | float):
        raise ScriptError(f"{where}: sleep is a number of seconds")
    if action == "tool" and not isinstance(raw.get("args", {}), dict):
        raise ScriptError(f"{where}: a tool's args are a table")
    if action != "sleep" and not isinstance(raw[action], str):
        raise ScriptError(f"{where}: {action} is a string")
    return dict(raw)


def _rule(raw, where: str) -> Rule:
    if not isinstance(raw, dict):
        raise ScriptError(f"{where}: a rule is a table")
    unknown = set(raw) - set(CONDITIONS) - {"steps", "reply"}
    if unknown:
        raise ScriptError(f"{where}: unknown keys {', '.join(sorted(unknown))}; a rule has {', '.join(CONDITIONS)}, steps and reply")
    if not isinstance(raw.get("role"), str) or not raw["role"]:
        raise ScriptError(f"{where}: role is required (a role's name, or \"*\")")
    if raw.get("prompt") is not None:
        try:
            re.compile(raw["prompt"])
        except re.error as e:
            raise ScriptError(f"{where}: prompt is not a regular expression: {e}") from e
    turn = raw.get("turn", ())
    turn = (turn,) if isinstance(turn, int) else tuple(turn)
    if not all(isinstance(n, int) and n >= 1 for n in turn):
        raise ScriptError(f"{where}: turn is a number from 1, or a list of them")
    steps = raw.get("steps", [])
    if not isinstance(steps, list):
        raise ScriptError(f"{where}: steps is a list")
    return Rule(role=raw["role"], steps=tuple(_step(s, f"{where} step {i}") for i, s in enumerate(steps, 1)),
                reply=raw.get("reply"), ticket=raw.get("ticket"), prompt=raw.get("prompt"), turn=turn,
                checkout=raw.get("checkout"), where=where)


def parse(text: str, name: str = "script") -> list[Rule]:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ScriptError(f"{name}: not TOML: {e}") from e
    unknown = set(raw) - {"rule"}
    if unknown:
        raise ScriptError(f"{name}: unknown tables {', '.join(sorted(unknown))}; a script is [[rule]] tables")
    return [_rule(r, f"{name} rule {i}") for i, r in enumerate(raw.get("rule", []), 1)]


def load(path: Path | None) -> Script:
    """The rules of ``path`` (none when None), then the built-in ones."""
    rules: list[Rule] = []
    if path is not None:
        if not path.is_file():
            raise ScriptError(f"[agents] dummy_script names {path}, which is not a file")
        rules = parse(path.read_text(encoding="utf-8"), path.name)
    return Script(rules + parse(DEFAULTS.read_text(encoding="utf-8"), DEFAULTS.name))
