"""A machine's spec as files in the project (docs/proposals/agent-first-machines.md §3–4; the owner,
2026-10-28: "just machines", in ``.orchestra/machines/<name>/``).

A machine spec is **a role plus an agent record, given a contract**, in one directory, which the
agents read as both. It does not define a second kind of agent: the agent it belongs to is the
built-in one (the Supervisor) or a custom agent record of the same name (``org_store``); a spec
whose name is no agent is ignored. What it adds to that agent (``apply``):

- ``machine.toml``: its description, engagement, **model** (per machine; ``[agents] model`` is
  the default for the rest), its review round (``[review] every``) and its **duties**, each with
  what wakes it (``listens``: wake-up entries, added to the agent's ``wake_on``) and the tool
  groups it needs (checked against what the agent holds; a spec never grants a tool);
- ``role.md``: the project's own words on how this machine works a wake-up;
- ``duties/<duty>.md``: one standing duty each: what it keeps true, its signals, its fixes and
  the prediction each fix carries;
- ``ledger.md``: why each duty exists: who asked, when, the commit (``duties.py`` appends);
- ``proposals/``: what its review rounds proposed about itself (a split, ``split_proposal.py``).

A **built-in duty** (``builtin = true``) names a part of the plugin's own prompt
(``prompts/supervisor.md`` and ``operator.md``): its text ships with the plugin and changes with
it, so it is listed here and never copied, edited or removed. The Supervisor's **fixed core**
(``prompts/supervisor_core.md``) is not in the spec at all: it is prepended to every Supervisor
turn, so nothing the Supervisor writes here can remove its guardrails.

Orchestra reads none of this (its rule 2: the Machines page reads what is observed). The files
are read through ``spec_git.read_files``: the working tree, or the side branch while a change
committed there is not merged yet."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field

from .config import Project
from .spec_git import ROOT, read_files

NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
ENGAGEMENTS = ("continuous", "scheduled", "ad_hoc")
DEFAULT_REVIEW_EVERY = "30m"


class SpecError(ValueError):
    pass


@dataclass
class Duty:
    name: str
    prompt: str | None = None           # duties/<name>.md, relative to the machine's directory
    builtin: bool = False               # its text is the plugin's (never copied, edited or removed)
    about: str = ""                     # one line: what it keeps true
    listens: list[str] = field(default_factory=list)   # wake-up entries (``type`` or ``type:filter``)
    tools: list[str] = field(default_factory=list)     # tool groups it needs
    added_by: str = ""
    added_at: str = ""
    reason: str = ""                    # the owner's words, or the review round's finding

    def to_dict(self) -> dict:
        d = {"name": self.name}
        for k in ("prompt", "about", "added_by", "added_at", "reason"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        if self.builtin:
            d["builtin"] = True
        for k in ("listens", "tools"):
            if getattr(self, k):
                d[k] = list(getattr(self, k))
        return d


@dataclass
class MachineSpec:
    name: str
    description: str = ""
    engagement: str = "continuous"
    model: str | None = None
    created_by: str = ""
    review_every: str | None = None
    duties: list[Duty] = field(default_factory=list)
    publishes: list[dict] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)       # this machine's files, relative to its directory

    @property
    def dir(self) -> str:
        return f"{ROOT}/{self.name}"

    def duty(self, name: str) -> Duty | None:
        return next((d for d in self.duties if d.name == name), None)

    def duty_text(self, d: Duty) -> str:
        return self.files.get(d.prompt or f"duties/{d.name}.md", "") if not d.builtin else ""

    @property
    def role_text(self) -> str:
        return self.files.get("role.md", "")

    def listens(self) -> list[str]:
        out: list[str] = []
        for d in self.duties:
            for w in d.listens:
                if w not in out:
                    out.append(w)
        return out


def parse(name: str, files: dict[str, str]) -> MachineSpec:
    """A spec from its directory's files (``machine.toml`` and the rest, relative to it)."""
    try:
        raw = tomllib.loads(files.get("machine.toml", ""))
    except tomllib.TOMLDecodeError as e:
        raise SpecError(f"{ROOT}/{name}/machine.toml does not parse: {e}") from None
    review = raw.get("review") if isinstance(raw.get("review"), dict) else {}
    duties = []
    for d in raw.get("duties") or []:
        if isinstance(d, dict) and isinstance(d.get("name"), str):
            duties.append(Duty(name=d["name"], prompt=d.get("prompt"), builtin=bool(d.get("builtin")), about=str(d.get("about") or ""),
                               listens=[str(x) for x in d.get("listens") or []], tools=[str(x) for x in d.get("tools") or []],
                               added_by=str(d.get("added_by") or ""), added_at=str(d.get("added_at") or ""),
                               reason=str(d.get("reason") or "")))
    return MachineSpec(name=raw.get("name") or name, description=str(raw.get("description") or ""),
                       engagement=str(raw.get("engagement") or "continuous"), model=raw.get("model") or None,
                       created_by=str(raw.get("created_by") or ""), review_every=review.get("every") or None, duties=duties,
                       publishes=[p for p in raw.get("publishes") or [] if isinstance(p, dict)], files=dict(files))


def load_all(project: Project) -> dict[str, MachineSpec]:
    """Every spec in the project, by machine name. A spec that does not parse is left out (the
    agent runs as its record says; ``lint`` says why)."""
    by: dict[str, dict[str, str]] = {}
    for path, text in read_files(project).items():
        rest = path[len(ROOT) + 1:]
        name, _, inner = rest.partition("/")
        if NAME.match(name) and inner:
            by.setdefault(name, {})[inner] = text
    out = {}
    for name, files in by.items():
        if "machine.toml" not in files:
            continue
        try:
            out[name] = parse(name, files)
        except SpecError:
            continue
    return out


def load(project: Project, name: str) -> MachineSpec | None:
    return load_all(project).get(name)


def apply(agent, spec: MachineSpec) -> None:
    """The spec's contract on the agent record it belongs to: its duties' wake-ups are the agent's
    too (the core's own are kept: a spec only adds)."""
    for w in spec.listens():
        if w not in agent.wake_on:
            agent.wake_on.append(w)


def _s(v: str) -> str:
    return json.dumps(v, ensure_ascii=False)


def render(spec: MachineSpec) -> str:
    """``machine.toml`` for a spec (the duty tools rewrite it whole)."""
    lines = [f"# The machine `{spec.name}`: its contract. Edited by its own duty tools (each change a commit with its",
             "# reason) or by hand; read by orchestra-agents, never by Orchestra.",
             f"name = {_s(spec.name)}"]
    for k in ("description", "engagement", "model", "created_by"):
        v = getattr(spec, k)
        if v:
            lines.append(f"{k} = {_s(v)}")
    if spec.review_every:
        lines += ["", "[review]", f"every = {_s(spec.review_every)}"]
    for d in spec.duties:
        lines += ["", "[[duties]]"]
        for k, v in d.to_dict().items():
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, list):
                lines.append(f"{k} = [{', '.join(_s(x) for x in v)}]")
            else:
                lines.append(f"{k} = {_s(v)}")
    for p in spec.publishes:
        lines += ["", "[[publishes]]"] + [f"{k} = {_s(str(v))}" for k, v in p.items() if isinstance(v, str)]
    return "\n".join(lines) + "\n"


def prompt_part(spec: MachineSpec) -> str:
    """What the spec adds to the agent's system prompt: its loop and its duties, in full."""
    parts = [(f"## Your machine spec (`{spec.dir}/`)\n\n"
              "This part of your prompt is the project's: your own loop and standing duties, which you change with "
              "`add_duty`, `edit_duty` and `remove_duty` (each change a commit with its reason) and undo with "
              "`revert_change`; `list_duties` shows them with their history.")]
    if spec.role_text.strip():
        parts.append(f"### How you work a wake-up\n\n{spec.role_text.strip()}")
    builtin = [d for d in spec.duties if d.builtin]
    if builtin:
        parts.append("### Built-in duties (their text is your built-in prompt above; fixed)\n\n"
                     + "\n".join(f"- `{d.name}`: {d.about}" for d in builtin))
    for d in spec.duties:
        if d.builtin:
            continue
        wakes = ", ".join(f"`{w}`" for w in d.listens) or "the review round and your other wake-ups"
        tools = ", ".join(f"`{t}`" for t in d.tools) or "any of yours"
        body = spec.duty_text(d).strip() or "(no text written yet)"
        parts.append(f"### Duty `{d.name}`\n\nWakes you on: {wakes}. Tool groups: {tools}.\n\n{body}")
    return "\n\n".join(parts) + "\n"


def lint(spec: MachineSpec, groups: tuple[str, ...] | list[str], known_types: set[str] | frozenset[str]) -> list[str]:
    """What is wrong with a spec, as notes: a duty without its text, a tool group the agent does not
    hold, a wake-up type that is neither known nor declared."""
    notes = []
    if spec.engagement not in ENGAGEMENTS:
        notes.append(f"engagement {spec.engagement!r} is not one of {', '.join(ENGAGEMENTS)}")
    for d in spec.duties:
        if not d.builtin and not spec.duty_text(d).strip():
            notes.append(f"duty {d.name}: no text at {d.prompt or f'duties/{d.name}.md'}")
        missing = [t for t in d.tools if t not in groups]
        if missing:
            notes.append(f"duty {d.name}: needs tool groups it does not hold: {', '.join(missing)}")
        for w in d.listens:
            if w.split(":", 1)[0] not in known_types:
                notes.append(f"duty {d.name}: wakes on {w!r}, a type the agents do not know (fine if a plugin publishes it)")
    return notes
