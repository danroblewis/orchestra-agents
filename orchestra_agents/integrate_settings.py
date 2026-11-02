"""A scaffolded collector's ``[settings]``, as the Supervisor writes them (``integrate_kits.fill``
and ``clear``): the owner's values checked (``check_settings``) and set line by line in its
``poller.toml`` (``set_keys``, ``drop_keys``), comments kept.

- **The shapes a kit takes**: a string, number, boolean, a list of them, a table of them (the Slack
  kit's per-channel ``cadence``, the promql kit's ``aliases``: written inline) or a list of such
  tables (the promql kit's ``queries``: written as ``[[settings.<key>]]`` blocks, replacing the old
  ones where they were).
- **The checks**, the same for every shape: a setting's name, never a credential's key at any
  depth (the token, its variable or its file are ``approve_integration``'s, by reference), nothing
  shaped like a secret; and what the kits' own settings mean (``_kit_rules``): each query a series'
  name and its PromQL, ``selectors`` and ``match`` bounded by the promql kit's own test
  (``integrate_kits.bounded``), ``cadence`` a channel's id to a duration."""

from __future__ import annotations

import json
import re

from .secret_shapes import secret_in
from .store import StoreError

KEY = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")
CREDENTIAL_KEYS = ("token", "token_env", "token_file", "user_env", "user_file")   # the kits' [settings] credentials

SUBKEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")       # a key inside a table setting (a channel's id, a label's name)
METRIC = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]{0,120}$")
CHANNEL = re.compile(r"^[CG][A-Z0-9]{2,30}$")
QUERY_KEYS = ("name", "query", "help")
SHAPES = ("a string, a number, true/false, a list of them, a table of them ({ C0123 = \"30m\" }) or a list of such "
          "tables ([{ name, query, help }])")


def _scalar(v) -> bool:
    return isinstance(v, str | int | float | bool)


def _table(v) -> bool:
    return isinstance(v, dict) and all(isinstance(k, str) and SUBKEY.match(k) and _scalar(x) for k, x in v.items())


def _shape_ok(v) -> bool:
    """The value shapes a kit's ``[settings]`` takes: a plain value, a list of plain values, a table of
    plain values (the slack kit's ``cadence``, the promql kit's ``aliases``) or a list of such tables
    (the promql kit's ``queries``, written as ``[[settings.queries]]``)."""
    if _scalar(v) or _table(v):
        return True
    if isinstance(v, list):
        return all(isinstance(x, str | int | float) for x in v) or (bool(v) and all(_table(x) for x in v))
    return False


def _bounded(selector: str) -> bool:
    from .integrate_kits import bounded
    return bounded(selector)


def _duration(k: str, v) -> None:
    from orchestra.config import ConfigError, parse_duration
    try:
        ok = isinstance(v, str) and parse_duration(v, 0.0) > 0
    except (ConfigError, TypeError, ValueError):
        ok = False
    if not ok:
        raise StoreError(f"settings: {k} is a duration (30m, 1h), not {v!r}")


def _kit_rules(k: str, v) -> None:
    """What the kits' own settings mean, checked before anything is written: the promql kit's
    ``queries`` (each a gauge's ``name`` and its PromQL ``query``, with an optional ``help``) and
    ``selectors`` (each bounded: never the whole store), and the slack kit's ``cadence`` (a channel's
    id to its own interval) and ``max_backoff``."""
    if k == "queries":
        if not isinstance(v, list) or not all(isinstance(q, dict) for q in v):
            raise StoreError("settings: queries is a list of tables, each { name, query, help }")
        names = []
        for q in v:
            if extra := set(q) - set(QUERY_KEYS):
                raise StoreError(f"settings: a query takes name, query and help, not {', '.join(sorted(extra))}")
            if not (isinstance(q.get("name"), str) and METRIC.match(q["name"])):
                raise StoreError(f"settings: each query's name is a series' name (payments_requests), not {q.get('name')!r}")
            if not (isinstance(q.get("query"), str) and q["query"].strip()):
                raise StoreError(f"settings: the query {q['name']} has no PromQL (query)")
            if "help" in q and not isinstance(q["help"], str):
                raise StoreError(f"settings: the query {q['name']}'s help is a sentence")
            names.append(q["name"])
        if len(set(names)) != len(names):
            raise StoreError("settings: two queries have the same name; each names its own series")
    elif k in ("selectors", "match"):
        for sel in v if isinstance(v, list) else [v]:
            if not (isinstance(sel, str) and _bounded(sel)):
                raise StoreError(f"settings: {k}: {sel!r} would match the whole store: name a metric or a label value "
                                 "(the team's job or namespace); nothing was written")
    elif k == "cadence":
        if not isinstance(v, dict):
            raise StoreError("settings: cadence is a table of a channel's id to its own interval ({ C0123 = \"30m\" })")
        for ch, d in v.items():
            if not CHANNEL.match(ch):
                raise StoreError(f"settings: cadence: {ch!r} is not a channel's id (C0123, G0123 for a private one)")
            _duration(f"cadence.{ch}", d)
    elif k == "max_backoff":
        _duration(k, v)


def check_settings(settings: dict) -> dict:
    """The owner's settings for the program, checked: plain keys, the shapes a kit takes
    (``_shape_ok``), what a kit's own settings mean (``_kit_rules``), no credential anywhere in them."""
    if not isinstance(settings, dict):
        raise StoreError("settings is an object of the program's own settings (jql, channels, every, per_minute, ttl, "
                         "queries, cadence)")
    out = {}
    for k, v in settings.items():
        if not KEY.match(str(k)):
            raise StoreError(f"settings: {k!r} is not a setting's name (lower case, digits, _)")
        if k in CREDENTIAL_KEYS or k in ("clearance", "data_class") or "token" in k or "secret" in k:
            raise StoreError(f"settings: {k} is not set here: the token's variable and the clearance are the owner's, at "
                             "approve_integration; a token is never written anywhere")
        if not _shape_ok(v):
            raise StoreError(f"settings: {k} is {SHAPES}")
        inner = [x for t in (v if isinstance(v, list) else [v]) if isinstance(t, dict) for x in t]
        if bad := next((x for x in inner if x in CREDENTIAL_KEYS or "token" in x.lower() or "secret" in x.lower()), None):
            raise StoreError(f"settings: {k}.{bad}: a credential is never a setting here (approve_integration names it by "
                             "reference)")
        if why := secret_in(f"{k} = {v!r}"):
            raise StoreError(f"settings: {k} looks like it holds a secret ({why}); nothing was written")
        _kit_rules(k, v)
        out[k] = v
    return out


def _key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k)


def _value(v) -> str:
    """A TOML value from a string, number, boolean, a list of them or a table of them, inline (JSON's
    string escapes are TOML's)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + " }" if v else "{}"
    return json.dumps(str(v))


def _tables(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def _drop_subtables(lines: list[str], table: str, keys) -> tuple[list[str], dict[str, int]]:
    """``lines`` without the ``[table.key]`` and ``[[table.key]]`` blocks of ``keys`` (each from its
    header to the next header), and where each key's first block was in what is left."""
    own = re.compile(rf"^\s*\[\[?\s*{re.escape(table)}\.([A-Za-z0-9_-]+)\s*\]\]?\s*(#.*)?$")
    head = re.compile(r"^\s*\[")
    out: list[str] = []
    first: dict[str, int] = {}
    skipping = False
    for ln in lines:
        if head.match(ln):
            m = own.match(ln)
            skipping = bool(m and m.group(1) in keys)
            if skipping:
                first.setdefault(m.group(1), len(out))
        if not skipping:
            out.append(ln)
    return out, first


def set_keys(text: str, table: str | None, values: dict) -> str:
    """``text`` with each key of ``values`` set in ``table`` (None: the top level), line by line:
    a key's line replaced (its end-of-line comment, which described the old value, goes), a new key
    added at the end of the table; every other line, comments included, kept as it was. A list of
    tables (the promql kit's ``queries``) is written as ``[[table.key]]`` blocks, replacing the old
    ones where they were (else at the end of the file), and a table as an inline one on the key's
    line. The program's own ``poller.toml`` is edited this way; Orchestra's configuration never is
    (rule 12)."""
    lines = text.splitlines()
    blocks = {k: v for k, v in values.items() if table is not None and _tables(v)}
    values = {k: v for k, v in values.items() if k not in blocks}
    at: dict[str, int] = {}
    if table is not None:
        lines, at = _drop_subtables(lines, table, {*blocks, *values})
    head = re.compile(r"^\s*\[")
    start = 0 if table is None else next(
        (i + 1 for i, ln in enumerate(lines) if re.match(rf"^\s*\[{re.escape(table)}\]\s*(#.*)?$", ln)), None)
    if start is None:
        lines += ["", f"[{table}]"]
        start = len(lines)
    end = next((i for i in range(start, len(lines)) if head.match(lines[i])), len(lines))
    for k in blocks:                                # a key line of the same name would clash with the blocks
        gone = [i for i in range(start, end) if re.match(rf"^\s*{re.escape(k)}\s*=", lines[i])]
        for i in reversed(gone):
            del lines[i]
            end -= 1
            at = {x: p - 1 if p > i else p for x, p in at.items()}
    for k, v in values.items():
        line = f"{k} = {_value(v)}"
        i = next((i for i in range(start, end) if re.match(rf"^\s*{re.escape(k)}\s*=", lines[i])), None)
        if i is not None:
            lines[i] = line
            continue
        last = max((i for i in range(start, end) if lines[i].strip()), default=start - 1)
        lines.insert(last + 1, line)
        at = {x: p + 1 if p > last else p for x, p in at.items()}
        end += 1
    for k, rows in blocks.items():
        text_rows = []
        for row in rows:
            text_rows += ["", f"[[{table}.{k}]]", *(f"{_key(f)} = {_value(x)}" for f, x in row.items())]
        where = at.get(k)
        if where is None:
            lines += text_rows
        else:
            while where > 0 and not lines[where - 1].strip():
                where -= 1
            lines[where:where] = text_rows
            at = {x: p + len(text_rows) if p >= where and x != k else p for x, p in at.items()}
    return "\n".join(lines) + "\n"


def drop_keys(text: str, table: str, keys: list[str]) -> str:
    """``text`` without the lines that set ``keys`` in ``table`` (a commented-out line is kept)."""
    lines = text.splitlines()
    start = next((i + 1 for i, ln in enumerate(lines) if re.match(rf"^\s*\[{re.escape(table)}\]\s*(#.*)?$", ln)), None)
    if start is None:
        return text
    end = next((i for i in range(start, len(lines)) if re.match(r"^\s*\[", lines[i])), len(lines))
    gone = {i for i in range(start, end) if any(re.match(rf"^\s*{re.escape(k)}\s*=", lines[i]) for k in keys)}
    return "\n".join(ln for i, ln in enumerate(lines) if i not in gone) + "\n"
