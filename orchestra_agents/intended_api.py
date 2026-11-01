"""The declared layer, as the design tools reach it: Orchestra's own (``docs/proposals/intended-model.md``
§6.3, built in Orchestra's task C1) when the installed Orchestra has it, else the agents' stand-in
(``intended_standin.py``).

**The integration seam.** This module is the one place that chooses. The design tools call only
``apply(ops, provenance)``, ``read(version)`` and ``design()`` on what ``layer`` returns, in the
proposal's shapes, so when Orchestra's store is installed it is used with no other change, and the
stand-in (with this fallback) is deleted.

- **Orchestra's** is written only by its host and its web server (Orchestra's rule 5 as amended:
  nothing else opens anything under ``<state>/model/intended/``), so ``Core`` calls the host's
  events endpoint over HTTP with the project's token: ``POST /intended`` (``{source, author, turn,
  message, quote, reason, request, ops}``, 422 with the refused op's ``index``), ``GET /intended``
  and ``GET /intended/design``. With it installed and its host not running, a call is refused,
  never written anywhere else.
- **The stand-in** is used only when the installed Orchestra's endpoint has no ``/intended`` route.
  What it holds is kept in the agents' state directory, and no page draws it (``drawn``).
- **Carried over once.** On the first use of Orchestra's layer, a stand-in journal left from
  before (``<state>/agents/intended/journal.jsonl``) is replayed into it, call by call, with each
  call's provenance (less ``via`` and ``at``, which the store sets), when Orchestra's layer is still
  empty; the stand-in's directory is then renamed ``intended.replayed``. When both hold
  declarations, every call is refused until the owner says which to keep."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from .config import Project
from .store import StoreError

PROVENANCE = ("source", "author", "turn", "message", "quote", "reason", "request")
TIMEOUT_S = 30.0


class Layer(Protocol):
    def apply(self, ops: list[dict], provenance: dict) -> dict: ...
    def read(self, version: int | None = None) -> dict: ...
    def design(self) -> dict: ...


def has_core() -> bool:
    """Whether the installed Orchestra keeps the declared layer: its endpoint has ``POST /intended``."""
    try:
        from orchestra.endpoint import api
    except ImportError:
        return False
    return any(m == "POST" and name == "declare" for m, _, name in getattr(api, "ROUTES", ()))


def _address(project: Project) -> tuple[str, str]:
    url, token = os.environ.get("ORCHESTRA_EVENTS_ENDPOINT"), os.environ.get("ORCHESTRA_EVENTS_TOKEN")
    if not (url and token):
        from orchestra import endpoint
        rec = endpoint.recorded(project.orchestra)
        if rec is None:
            raise StoreError("Orchestra's host is not running (orchestra run), and the declared layer is written only "
                             "through its events endpoint: nothing was written. Start it, or ask the owner to")
        url, token = rec["url"], endpoint.token(project.orchestra)
    return url.rstrip("/"), token


class Core:
    """Orchestra's declared layer through its host's endpoint; refusals said as the tools say theirs."""

    def __init__(self, project: Project):
        self.project = project

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        url, token = _address(self.project)
        req = urllib.request.Request(url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                out = json.loads(e.read() or b"{}")
            except ValueError:
                out = {}
            index = out.get("index")
            raise StoreError(f"Orchestra refused {'op ' + str(index) + ': ' if index is not None else ''}"
                             f"{out.get('error') or e.reason} (HTTP {e.code}); nothing was applied") from None
        except (urllib.error.URLError, OSError) as e:
            raise StoreError(f"Orchestra's endpoint at {url} did not answer ({e}); nothing was applied") from None

    def apply(self, ops, provenance):
        body = {k: provenance[k] for k in PROVENANCE if provenance.get(k) is not None}
        out = self._call("POST", "/intended", {**body, "ops": ops})
        return {"version": out.get("version"), "changes": out.get("changes") or []}

    def read(self, version=None):
        out = self._call("GET", "/intended" + (f"?version={int(version)}" if version is not None else ""))
        return {k: out.get(k) for k in ("version", "at", "layer")}

    def design(self):
        out = self._call("GET", "/intended/design")
        return {k: v for k, v in out.items() if k != "ok"}


def standin_dir(project: Project) -> Path:
    return project.state_dir / "intended"


def _carry_over(project: Project, core: Core) -> None:
    """Replay the stand-in's journal into Orchestra's empty layer, once (module docstring)."""
    journal = standin_dir(project) / "journal.jsonl"
    try:
        lines = [json.loads(x) for x in journal.read_text(encoding="utf-8").splitlines() if x.strip()]
    except FileNotFoundError:
        return
    if not lines:
        return
    if core.read().get("version"):
        raise StoreError(f"both Orchestra's declared layer and the agents' stand-in ({standin_dir(project)}) hold "
                         "declarations, so neither is written: ask the owner which to keep (the stand-in's are replayed "
                         "into Orchestra's only while Orchestra's is empty)")
    for line in lines:
        prov = {k: v for k, v in (line.get("provenance") or {}).items() if k not in ("via", "at")}
        ops = line.get("sent") or line.get("ops") or []
        try:
            core.apply(ops, prov)
        except StoreError as e:
            raise StoreError(f"carrying the stand-in's declarations into Orchestra's layer stopped at call {line.get('seq')}: "
                             f"{e}. Orchestra's layer now holds the calls before it; the stand-in is left in place") from None
    standin_dir(project).rename(standin_dir(project).with_name(f"intended.replayed.{int(time.time())}"))


def layer(project: Project) -> Layer:
    if has_core():
        core = Core(project)
        _carry_over(project, core)
        return core
    from .intended_standin import StandIn
    return StandIn(project)


def current(project: Project) -> dict:
    """The declared layer as it is now: ``{stocks, effects, flows, machines, events, targets, marks, ...}``."""
    return layer(project).read()["layer"]


def drawn(project: Project) -> bool:
    """Whether Orchestra's pages draw the declared layer (only Orchestra's own; never the stand-in)."""
    return has_core()


def where(project: Project) -> str:
    """Where a declaration is kept, for the tools' answers."""
    if drawn(project):
        return "Orchestra's declared layer: the Dynamics and Machines pages draw it"
    return ("the agents' stand-in for Orchestra's declared layer (this Orchestra has none yet): kept, and no page draws it; "
            "read_design reads it back")
