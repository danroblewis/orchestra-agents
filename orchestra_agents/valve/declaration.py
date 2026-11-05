"""A valve's declaration (``valve.json``, beside its ``machine.json`` in the state directory's
``machines/<id>/``): what the Supervisor writes (``declare_valve``) and the program reads. ``parse``
says every problem, one sentence each; the same checks serve both, so a declaration the tool wrote
is one the program runs.

    {"name": "oncall_filter",
     "question": "Does this ticket need the on-call engineer now?",
     "labels": ["page", "queue", "ignore"],
     "inputs": {"workflow": true, "step": true, "item": false,
                "fields": ["summary", "priority"],
                "facts": {"types": ["jira.issue.commented"], "data": ["body"], "last": 5},
                "max_chars": 2000},
     "api": "anthropic", "base_url": "https://api.anthropic.com", "model": "<the model's id>",
     "token_file": "/absolute/path/the/owner/gave", "min_confidence": 0.7, "fallback": "ask",
     "max_calls_per_day": 200, "timeout_s": 20, "retries": 2, "backoff_s": 1.0}

``api`` is ``openai`` (chat completions), ``anthropic`` (messages) or ``classify`` (a generic
endpoint: ``classify: {"path", "request": {"inputs", "labels", "question"}, "response": {"label",
"score", "reason"}, "auth_header", "auth_prefix"}``, each a field name, all optional). The outcomes
are the labels then the fallback when it is not one of them; outcome ``i`` exits ``10 + i``
(``exit_map``), and the Python failures the program cannot help (1, 2) are ``error``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

APIS = ("openai", "anthropic", "classify")
NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
LABEL = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,80}$")
EVENT = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
BASE = 10                  # outcome i exits BASE + i
KEYS = {"name", "question", "labels", "inputs", "api", "base_url", "model", "token_file", "min_confidence", "fallback",
        "max_calls_per_day", "timeout_s", "retries", "backoff_s", "classify", "max_tokens"}
INPUT_KEYS = {"workflow", "step", "item", "fields", "facts", "max_chars"}
FACT_KEYS = {"types", "data", "last"}
CLASSIFY_KEYS = {"path", "request", "response", "auth_header", "auth_prefix"}
CLASSIFY_REQUEST = {"inputs": "inputs", "labels": "labels", "question": "question"}
CLASSIFY_RESPONSE = {"label": "label", "score": "score", "reason": "reason"}
MAX_FACTS = 50
MAX_CHARS = 20_000
MAX_LABELS = 12


@dataclass(frozen=True)
class Facts:
    types: tuple[str, ...] = ()
    data: tuple[str, ...] = ()
    last: int = 5


@dataclass(frozen=True)
class Inputs:
    workflow: bool = True
    step: bool = True
    item: bool = False
    fields: tuple[str, ...] = ()
    facts: Facts = field(default_factory=Facts)
    max_chars: int = 2000


@dataclass(frozen=True)
class Valve:
    name: str
    question: str
    labels: tuple[str, ...]
    api: str
    base_url: str
    model: str = ""
    token_file: str = ""
    inputs: Inputs = field(default_factory=Inputs)
    min_confidence: float = 0.0
    fallback: str = "ask"
    max_calls_per_day: int = 100
    timeout_s: float = 20.0
    retries: int = 2
    backoff_s: float = 1.0
    max_tokens: int = 300
    classify: dict = field(default_factory=dict)

    @property
    def outcomes(self) -> tuple[str, ...]:
        return self.labels + (() if self.fallback in self.labels else (self.fallback,))

    def code(self, outcome: str) -> int:
        return BASE + self.outcomes.index(outcome)


def exit_map(v: Valve) -> dict[str, str]:
    """The ``spec.exit`` of the valve's ``machine.json``: each outcome's code, and ``error`` for the
    codes a Python failure exits with (1, and 2 for a bad argument)."""
    return {"1": "error", "2": "error", **{str(v.code(o)): o for o in v.outcomes}}


def _names(value, rx: re.Pattern, what: str, out: list[str], limit: int = 50) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(x, str) and rx.match(x) for x in value) or len(value) > limit:
        out.append(f"{what} is a list of names (at most {limit})")
        return ()
    return tuple(dict.fromkeys(value))


def _number(raw: dict, key: str, default, out: list[str], lo: float, hi: float, whole: bool = False):
    v = raw.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int | float) or (whole and not isinstance(v, int)) or not lo <= v <= hi:
        out.append(f"{key} is a {'whole ' if whole else ''}number from {lo:g} to {hi:g}, not {v!r}")
        return default
    return v


def _inputs(raw, out: list[str]) -> Inputs:
    if raw is None:
        return Inputs()
    if not isinstance(raw, dict):
        out.append("inputs is an object: {fields, facts: {types, data, last}, workflow, step, item, max_chars}")
        return Inputs()
    out.extend(f"inputs has an unknown key {k!r}" for k in sorted(set(raw) - INPUT_KEYS))
    facts = raw.get("facts") or {}
    if not isinstance(facts, dict):
        out.append("inputs.facts is an object: {types: [event types], data: [their data fields], last: n}")
        facts = {}
    out.extend(f"inputs.facts has an unknown key {k!r}" for k in sorted(set(facts) - FACT_KEYS))
    types = _names(facts.get("types"), EVENT, "inputs.facts.types", out)
    data = _names(facts.get("data"), FIELD, "inputs.facts.data", out)
    if types and not data:
        out.append("inputs.facts.data names the data fields of those facts that go in (a summary, a comment's body): "
                   "a fact goes in only with its type and time and the fields named")
    last = _number(facts, "last", 5, out, 1, MAX_FACTS, whole=True)
    flags = {}
    for k, d in (("workflow", True), ("step", True), ("item", False)):
        flags[k] = raw.get(k, d)
        if not isinstance(flags[k], bool):
            out.append(f"inputs.{k} is true or false")
            flags[k] = d
    return Inputs(fields=_names(raw.get("fields"), FIELD, "inputs.fields", out), facts=Facts(types, data, last),
                  max_chars=_number(raw, "max_chars", 2000, out, 100, MAX_CHARS, whole=True), **flags)


def _classify(raw, out: list[str]) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        out.append("classify is an object: {path, request: {inputs, labels, question}, response: {label, score, reason}}")
        return {}
    out.extend(f"classify has an unknown key {k!r}" for k in sorted(set(raw) - CLASSIFY_KEYS))
    got = {"path": raw.get("path", "/classify"), "auth_header": raw.get("auth_header", "Authorization"),
           "auth_prefix": raw.get("auth_prefix", "Bearer ")}
    if not isinstance(got["path"], str) or not got["path"].startswith("/"):
        out.append("classify.path is the endpoint's path, beginning with /")
    for part, defaults in (("request", CLASSIFY_REQUEST), ("response", CLASSIFY_RESPONSE)):
        given = raw.get(part) or {}
        if not isinstance(given, dict) or set(given) - set(defaults) or not all(
                isinstance(v, str) and FIELD.match(v) for v in given.values()):
            out.append(f"classify.{part} maps {', '.join(defaults)} to the endpoint's field names")
            given = {}
        got[part] = {**defaults, **given}
    return got


def parse(raw) -> tuple[Valve | None, list[str]]:
    """The declaration, or None with every problem (one sentence each)."""
    if not isinstance(raw, dict):
        return None, ["a valve's declaration is a JSON object"]
    out = [f"the declaration has an unknown key {k!r}" for k in sorted(set(raw) - KEYS)]
    name, question = raw.get("name"), raw.get("question")
    if not isinstance(name, str) or not NAME.match(name):
        out.append("name is the valve's id (lowercase letters, digits and _), as the workflow's step names it")
    if not isinstance(question, str) or not 5 <= len(question.strip()) <= 2000:
        out.append("question is the decision to make, in a sentence (at most 2000 characters)")
    labels = raw.get("labels")
    if not isinstance(labels, list) or not 2 <= len(labels) <= MAX_LABELS or not all(
            isinstance(x, str) and LABEL.match(x) for x in labels) or len(set(labels)) != len(labels):
        out.append(f"labels are the allowed answers, 2 to {MAX_LABELS} distinct lowercase names (page, queue, ignore)")
        labels = []
    fallback = raw.get("fallback", "ask")
    if not isinstance(fallback, str) or not LABEL.match(fallback):
        out.append("fallback is the outcome when the model cannot decide (ask, or one of the labels)")
    api = raw.get("api")
    if api not in APIS:
        out.append(f"api is one of {', '.join(APIS)}, not {api!r}")
    base = raw.get("base_url")
    if not isinstance(base, str) or not re.match(r"^https?://[^\s/]+", base):
        out.append("base_url is the model's address, http(s)://host[:port][/prefix]")
    model = raw.get("model", "")
    if api in ("openai", "anthropic") and (not isinstance(model, str) or not model.strip()):
        out.append(f"model names the model the {api} API is asked for")
    token = raw.get("token_file", "")
    if not isinstance(token, str) or (token and not Path(token).expanduser().is_absolute()):
        out.append("token_file is the token file's full path, as the owner gave it (never the token)")
    elif not token and api in ("openai", "anthropic"):
        out.append(f"token_file is required for the {api} API: the token file's full path, as the owner gave it")
    if out and (not isinstance(name, str) or api not in APIS):
        return None, out
    v = Valve(
        name=name, question=str(question or "").strip(), labels=tuple(labels), api=api, base_url=str(base or "").rstrip("/"),
        model=str(model or ""), token_file=str(token or ""), inputs=_inputs(raw.get("inputs"), out),
        min_confidence=float(_number(raw, "min_confidence", 0.0, out, 0, 1)), fallback=str(fallback),
        max_calls_per_day=_number(raw, "max_calls_per_day", 100, out, 1, 1_000_000, whole=True),
        timeout_s=float(_number(raw, "timeout_s", 20.0, out, 0.05, 600)),
        retries=_number(raw, "retries", 2, out, 0, 10, whole=True),
        backoff_s=float(_number(raw, "backoff_s", 1.0, out, 0, 60)),
        max_tokens=_number(raw, "max_tokens", 300, out, 16, 4096, whole=True),
        classify=_classify(raw.get("classify"), out) if api == "classify" else {})
    return (None, out) if out else (v, [])


def as_json(v: Valve) -> dict:
    """The declaration as written: every value, defaults included, so the file says what runs."""
    out = {"name": v.name, "question": v.question, "labels": list(v.labels),
           "inputs": {"workflow": v.inputs.workflow, "step": v.inputs.step, "item": v.inputs.item,
                      "fields": list(v.inputs.fields),
                      "facts": {"types": list(v.inputs.facts.types), "data": list(v.inputs.facts.data),
                                "last": v.inputs.facts.last},
                      "max_chars": v.inputs.max_chars},
           "api": v.api, "base_url": v.base_url, "model": v.model, "token_file": v.token_file,
           "min_confidence": v.min_confidence, "fallback": v.fallback, "max_calls_per_day": v.max_calls_per_day,
           "timeout_s": v.timeout_s, "retries": v.retries, "backoff_s": v.backoff_s, "max_tokens": v.max_tokens}
    if v.api == "classify":
        out["classify"] = v.classify
    return out
