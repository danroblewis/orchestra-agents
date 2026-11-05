"""What a valve's model is shown: exactly what its declaration names (``Inputs``), nothing else.

From the program host's environment: ``ORCHESTRA_WORKFLOW``, ``ORCHESTRA_STEP``, ``ORCHESTRA_ITEM``
and the named fields of ``ORCHESTRA_FIELDS`` (the item's fields as dispatch offered them). From the
item's log in the Workflows record (its read API, ``orchestra_workflows.api.Record``, the surface
other repositories may import): the last ``facts.last`` facts of the named types, each as its type,
its time and only the named fields of its data (a ticket's summary, a comment's body, when the
reader relays them). Every string is cut to ``max_chars``. ``fingerprint`` is the inputs' digest
with the question, the labels, the API and the model: an unchanged item asked the same thing has
the same one, and is never asked twice (``ledger.py``)."""

from __future__ import annotations

import hashlib
import json
import os

from .declaration import Valve


def cut(value, n: int):
    """``value`` with every string in it cut to ``n`` characters (lists and objects kept, bounded)."""
    if isinstance(value, str):
        return value if len(value) <= n else value[:n] + "…"
    if isinstance(value, list):
        return [cut(x, n) for x in value[:50]]
    if isinstance(value, dict):
        return {str(k)[:80]: cut(x, n) for k, x in list(value.items())[:50]}
    return value


def _env_fields(environ) -> dict:
    try:
        got = json.loads(environ.get("ORCHESTRA_FIELDS") or "{}")
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def facts_of(v: Valve, log: list[dict]) -> list[dict]:
    """The item's last facts of the declared types, each its type, time and declared data fields."""
    f = v.inputs.facts
    if not f.types:
        return []
    chosen = [e for e in log if isinstance(e, dict) and e.get("type") in f.types][-f.last:]
    out = []
    for e in chosen:
        data = e.get("data") if isinstance(e.get("data"), dict) else {}
        out.append({"type": e.get("type"), "time": e.get("time"),
                    "data": {k: cut(data[k], v.inputs.max_chars) for k in f.data if k in data}})
    return out


def read_log(item: str, repo: str | None) -> list[dict]:
    """The item's facts from the Workflows record; empty when the plugin or the record is not there."""
    if not item:
        return []
    try:
        from orchestra.config import load_project
        from orchestra_workflows.api import Record
        return Record.for_project(load_project(repo or None)).log(item)
    except Exception:  # noqa: BLE001 - no record to read is no facts, said by the decision's inputs
        return []


def gather(v: Valve, environ=None, log: list[dict] | None = None) -> dict:
    """The inputs, as the model sees them and as the fingerprint digests them."""
    environ = os.environ if environ is None else environ
    i = v.inputs
    out: dict = {}
    if i.workflow:
        out["workflow"] = environ.get("ORCHESTRA_WORKFLOW", "")
    if i.step:
        out["step"] = environ.get("ORCHESTRA_STEP", "")
    if i.item:
        out["item"] = environ.get("ORCHESTRA_ITEM", "")
    have = _env_fields(environ)
    out["fields"] = {k: cut(have[k], i.max_chars) for k in i.fields if k in have}
    if i.facts.types:
        if log is None:
            log = read_log(environ.get("ORCHESTRA_ITEM", ""), environ.get("ORCHESTRA_REPO"))
        out["facts"] = facts_of(v, log)
    return out


def fingerprint(v: Valve, inputs: dict) -> str:
    asked = {"question": v.question, "labels": list(v.labels), "api": v.api, "model": v.model, "inputs": inputs}
    return hashlib.sha256(json.dumps(asked, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
