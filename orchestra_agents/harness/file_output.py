"""Orchestra's actions as a file the session writes, for an organisation whose policy allows an agent
neither an MCP server nor a command of Orchestra's (``[agents] tools_via = "file"``).

The session gets no Orchestra tools at all: no MCP server, no tool groups, and not its token, so
nothing it runs can reach Orchestra while it runs. The harness names one file in its environment,
``ORCHESTRA_OUTPUT`` (and the prompt says its path), and after each turn has ended it reads that
file and does what it asks, as the session, through the same path as a tool call
(``caller.Caller``: the schema check, the session by its token, ``tooling.handle``), so a line is
refused exactly as the tool would refuse it.

The file is GitHub Actions' ``$GITHUB_OUTPUT`` format, the one the Workflows program host reads
(orchestra-workflows SPEC §5.3, ``programs/step.outputs``; ``read`` here is a copy of it, checked
against it in the tests): ``name=value`` lines and ``name<<DELIM`` blocks ending at a line
``DELIM``; a value that parses as JSON is that value, else the text; a later line of a name wins.
The reserved names (``RESERVED``), acted on in the order of their lines:

- ``events``: a JSON list of events, each ``{type, subject?, data?}`` (``data`` a string or
  ``{note}``: the note ``emit_event`` carries), published one by one as ``emit_event`` publishes;
- ``prediction``: one ``predict_fix`` call's arguments (``{expect, horizon, note, subject?}``),
  published as ``orchestra.prediction.made``;
- ``outcome`` with ``summary``: the turn's result, given to the session's exit tool
  (``report_outcome``, ``report_ticket`` or ``report_review``, whichever it holds);
- ``fields``: a JSON object, the item's fields set with the finish (``facts.set`` on
  ``offer.finished``; only with ``report_outcome``).

Any other name is an output, kept with the run record. What was done, what was refused and why
(an invalid line or event is refused, never a crash, and the rest still go), and the outputs are
the session record's ``output`` (``store.set_session``)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from pathlib import Path

from .base import AgentSession, OnEvent, SessionSpec, TurnResult

MODE = "file"
ENV = "ORCHESTRA_OUTPUT"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")      # the program host's
OUTPUT_MAX = 64_000                                   # bytes read; the rest is ignored (the program host's)
RESERVED = ("events", "prediction", "outcome", "summary", "fields")
EXIT_TOOLS = ("report_outcome", "report_ticket", "report_review")
EVENT_KEYS = {"type", "subject", "data"}
ALIASES = {"report_ticket": {"ready": "READY_FOR_REVIEW", "blocked": "BLOCKED"},
           "report_review": {"approved": "APPROVED", "rejected": "REJECTED"}}


def path_for(session_dir: Path, session_id: str) -> Path:
    return Path(session_dir) / "outputs" / f"{session_id}.out"


def read(text: str) -> tuple[list[tuple[int, str, object]], list[dict]]:
    """The file's lines as (line number, name, value) in file order, and the lines of neither shape
    (refused, each with why). The parse is the program host's ``outputs``, line for line."""
    out: list[tuple[int, str, object]] = []
    bad: list[dict] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line, at = lines[i], i + 1
        i += 1
        name, value = None, None
        if "<<" in line and NAME.match(line.split("<<", 1)[0]) and ("=" not in line or line.index("<<") < line.index("=")):
            name, delim = line.split("<<", 1)
            body = []
            while i < len(lines) and lines[i] != delim:
                body.append(lines[i])
                i += 1
            i += 1
            value = "\n".join(body)
        elif "=" in line and NAME.match(line.split("=", 1)[0]):
            name, value = line.split("=", 1)
        if name is None:
            if line.strip():
                bad.append({"line": at, "reason": "neither name=value nor name<<DELIM: ignored"})
            continue
        try:
            value = json.loads(value)
        except ValueError:
            pass
        out.append((at, name, value))
    return out, bad


def outputs(text: str) -> dict:
    """Every name's value, a later line winning: what the program host makes of the same file."""
    return {name: value for _, name, value in read(text)[0]}


def exit_tool(groups) -> str | None:
    return next((t for t in EXIT_TOOLS if t in tuple(groups or ())), None)


def prompt(path: Path | str, groups) -> str:
    """The system prompt's section for a session in file mode: it holds no Orchestra tools, and what
    it may write to the file instead."""
    from .tools import tools_for
    names = {t.name for t in tools_for(tuple(groups or ()))}
    finish = exit_tool(groups)
    lines = []
    if "emit_event" in names:
        lines.append('- `events`: a JSON list of events to publish, each `{"type": ..., "subject": ..., "data": "a sentence"}` '
                     "(subject and data optional; the type one your declaration lets you emit). This is `emit_event`.")
    if "predict_fix" in names:
        lines.append('- `prediction`: one prediction, `{"expect": [...], "horizon": "30m", "note": "the fix, in a sentence"}` '
                     "(subject optional). This is `predict_fix`.")
    if finish == "report_outcome":
        lines.append("- `outcome` and `summary`: your result, the outcome as your task names it (or `blocked`, with why in "
                     "summary). This is `report_outcome`.")
        lines.append('- `fields`: a JSON object of the item\'s fields to set with your result, `{"commit": "abc123"}`.')
    elif finish == "report_ticket":
        lines.append("- `outcome` and `summary`: `READY_FOR_REVIEW` or `BLOCKED` (with what you need in summary), and what you "
                     "changed. This is `report_ticket`.")
    elif finish == "report_review":
        lines.append("- `outcome` and `summary`: `APPROVED` or `REJECTED` (with the concrete problems in summary). This is "
                     "`report_review`.")
    reserved = "\n".join(lines) or "- (none of the reserved names applies to this session)"
    return f"""## Orchestra in this session: one file, written at the end

In this session you have no Orchestra tools: no MCP server, no command and no tool of Orchestra's.
You cannot read Orchestra or act on it while you work; what you are told here is all you have.
Wherever this prompt or a message says to call one of Orchestra's tools, write what you would have
passed it to this file instead, once, when your work is done:

    {path}

(also `$ORCHESTRA_OUTPUT` in your environment). After your turn has ended Orchestra reads it and
does what it says, as you, in the order of its lines; it checks each line as the tool would, and a
line it refuses is skipped and recorded, the rest still go. The format is GitHub Actions'
`$GITHUB_OUTPUT`: one `name=value` per line, or, for a value over several lines,

```sh
cat >> "$ORCHESTRA_OUTPUT" <<'EOF'
summary<<END
What I did,
in a few lines.
END
EOF
```

A value that parses as JSON is that JSON (write lists and objects on one line as JSON). A later line
of the same name replaces an earlier one. The names Orchestra acts on:
{reserved}

Any other name is kept with this run's record. Writing nothing is ending your turn without a
result. Never write anything else to this file, and write it only with your shell or your editor.
"""


class FileSession(AgentSession):
    """A session of any harness, run without Orchestra's tools: the harness's own session (``inner``)
    has no tool groups and no token, and this one, which holds them, acts on the file after each turn."""

    def __init__(self, inner: AgentSession, spec: SessionSpec, path: Path):
        self.inner, self.spec, self.path = inner, spec, path
        self._caller = None
        self._told = False

    def __getattr__(self, name):              # the harness's own (a test's .proc, Claude Code's .uuid)
        return getattr(self.inner, name)

    @property
    def session_file(self) -> str | None:     # type: ignore[override]
        return self.inner.session_file

    @property
    def harness_name(self) -> str:
        return self.inner.harness_name

    @property
    def pid(self) -> int | None:
        return self.inner.pid

    def open(self) -> None:
        self.inner.open()

    def alive(self) -> bool:
        return self.inner.alive()

    def kill(self) -> None:
        self.inner.kill()

    def close(self) -> None:
        self.inner.close()

    def retire(self) -> str | None:
        return self.inner.retire()

    def compact(self) -> dict | None:
        return self.inner.compact()

    def prompt(self, text: str, timeout: float | None, on_event: OnEvent | None = None,
               idle_timeout: float | None = None) -> TurnResult:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)      # this turn's file only: a stale one is never acted on
        if not self._told:                     # a transcript begun in another mode keeps the prompt it began with
            self._told = True
            f = self.inner.session_file
            if f and Path(f).exists() and Path(f).stat().st_size:
                text = ("[Orchestra: this session now runs without Orchestra's tools. What your instructions say about "
                        f"calling them is replaced by this.]\n\n{prompt(self.path, self.spec.tool_groups).strip()}\n\n{text}")
        res = self.inner.prompt(text, timeout, on_event=on_event, idle_timeout=idle_timeout)
        if self.path.exists():
            self._act(res)
        return res

    def _act(self, res: TurnResult) -> None:
        try:
            with self.path.open("rb") as f:
                written = f.read(OUTPUT_MAX).decode("utf-8", errors="replace")
        except OSError as e:
            written, res_note = "", f"the file could not be read: {e}"
        else:
            res_note = None
        self.path.unlink(missing_ok=True)
        if res.timed_out or res.error:
            record = {"at": time.time(), "done": [], "outputs": {},
                      "refused": [{"reason": "not acted on: the turn did not end on its own "
                                             f"({'timed out' if res.timed_out else 'failed'})"}]}
        elif res_note:
            record = {"at": time.time(), "done": [], "outputs": {}, "refused": [{"reason": res_note}]}
        else:
            record = act(written, self.spec.tool_groups, self._call, self._amend)
        try:
            self._ctx()[1].set_session(self.spec.session_id, output=record)
        except Exception:  # noqa: BLE001, S110 - the record is best kept, never a reason to fail the turn
            pass

    def _ctx(self):
        if self._caller is None:
            from .caller import Caller
            self._caller = Caller(env=self.spec.env, groups=list(self.spec.tool_groups))
        return self._caller._ctx()

    def _call(self, name: str, args: dict):
        self._ctx()
        return self._caller.call(name, args)

    def _amend(self, fields: dict) -> None:
        from .. import access
        _, store = self._ctx()
        _, key = access.identify(store, self.spec.env)
        store.amend_report(key, fields=fields)


def act(text: str, groups, call, amend) -> dict:
    """What the file asks, done through ``call(tool, args)`` (which raises with the reason a call is
    refused) in the order of the lines, each name's last line counting. Returns the run record's
    ``output``: what was done, what was refused and why, and the other names' values."""
    entries, refused = read(text)
    last: dict[str, tuple[int, object]] = {}
    for at, name, value in entries:
        last.pop(name, None)                   # its place is its last line's
        last[name] = (at, value)
    done: list[str] = []
    kept: dict[str, object] = {}

    def refuse(at: int, name: str, why: str) -> None:
        refused.append({"line": at, "name": name, "reason": why})

    for name, (at, value) in last.items():
        try:
            if name == "events":
                if not isinstance(value, list):
                    refuse(at, name, "events is a JSON list of {type, subject?, data?}")
                    continue
                for n, ev in enumerate(value):
                    try:
                        call("emit_event", event_args(ev))
                        done.append(f"published {ev.get('type')}")
                    except Exception as e:  # noqa: BLE001 - refused, and the rest still go
                        refuse(at, f"events[{n}]", str(e))
            elif name == "prediction":
                if not isinstance(value, dict):
                    refuse(at, name, "prediction is a JSON object: {expect, horizon, note, subject?}")
                    continue
                out = call("predict_fix", value)
                done.append(f"predicted (change {(out or {}).get('change') if isinstance(out, dict) else '?'})")
            elif name == "outcome":
                done.append(finish(groups, value, last, call, amend))
            elif name in ("summary", "fields"):
                if "outcome" not in last:
                    refuse(at, name, f"{name} goes with an outcome, and the file has none")
            else:
                kept[name] = value
        except Exception as e:  # noqa: BLE001 - refused with why, never a crash
            refuse(at, name, str(e))
    return {"at": time.time(), "done": done, "refused": sorted(refused, key=lambda r: r.get("line", 0)), "outputs": kept}


def event_args(ev) -> dict:
    """An event of the file as ``emit_event``'s arguments: its data is the note emit_event carries."""
    if not isinstance(ev, dict):
        raise TypeError("an event is a JSON object {type, subject?, data?}")
    extra = sorted(set(ev) - EVENT_KEYS)
    if extra:
        raise ValueError(f"an event has type, subject and data; not {', '.join(extra)}")
    data = ev.get("data")
    if isinstance(data, dict):
        if set(data) - {"note"}:
            raise ValueError("an event's data is a sentence (a string, or {\"note\": ...}): emit_event carries only a note")
        data = data.get("note")
    if data is not None and not isinstance(data, str):
        raise ValueError("an event's data is a sentence (a string, or {\"note\": ...})")
    args = {"type": ev.get("type") if isinstance(ev.get("type"), str) else ""}
    if ev.get("subject") not in (None, ""):
        args["subject"] = str(ev["subject"])
    if data:
        args["note"] = data
    return args


def finish(groups, outcome, last: dict, call, amend) -> str:
    """The turn's result through the session's exit tool, with ``summary`` and, for a Workflows offer,
    ``fields``; raises with why it was refused."""
    tool = exit_tool(groups)
    if tool is None:
        raise ValueError("this session has no result to report (it holds no exit tool)")
    outcome = str(outcome if not isinstance(outcome, dict | list) else json.dumps(outcome)).strip()
    summary = last.get("summary", (0, ""))[1]
    summary = summary if isinstance(summary, str) else json.dumps(summary)
    fields = last.get("fields", (0, None))[1]
    if fields is not None:
        if tool != "report_outcome":
            raise ValueError(f"fields go with a Workflows offer's result; {tool} carries none")
        if not isinstance(fields, dict) or not all(isinstance(k, str) and NAME.match(k) for k in fields):
            raise ValueError("fields is a JSON object of {name: value}, each name a letter or _ then letters, digits, _ or -")
    if tool == "report_outcome":
        call(tool, {"outcome": outcome, "summary": summary})
        if fields:
            amend(fields)
    else:
        value = ALIASES[tool].get(outcome.lower(), outcome)
        key, why = ("state", "blocked_reason") if tool == "report_ticket" else ("verdict", "feedback")
        args = {key: value, "summary": summary}
        if value in ("BLOCKED", "REJECTED"):
            args[why] = summary
        call(tool, args)
    return f"reported {outcome} ({tool})" + (f" with fields {', '.join(sorted(fields))}" if fields else "")


def start(harness, spec: SessionSpec) -> AgentSession:
    """The session as the harness opens it, in file mode: the harness's own session without tool
    groups, without the token and with ``ORCHESTRA_OUTPUT``; the token stays here."""
    from ..access import TOKEN_ENV
    path = path_for(spec.session_dir, spec.session_id)
    env = {k: v for k, v in spec.env.items() if k != TOKEN_ENV} | {ENV: str(path)}
    inner = harness.start(replace(spec, tool_groups=(), env=env))
    return FileSession(inner, spec, path)


__all__ = ["ENV", "MODE", "RESERVED", "FileSession", "act", "outputs", "path_for", "prompt", "read", "start"]
