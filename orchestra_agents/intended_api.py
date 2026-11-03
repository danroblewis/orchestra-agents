"""The declared layer, as the design tools reach it: Orchestra's own (``docs/proposals/intended-model.md``
§6.3, ``orchestra.sd.intended``), over HTTP, and the other names of Orchestra's the design tools read.

**The integration seam.** This module is the design tools' only path to the declared layer. The
core names it imports are those Orchestra's ``PLUGINS.md`` §11 lists for a plugin (the intended
model's ``ops``, ``names``, ``keys`` and ``checks``; the model's ``store``, ``eras`` and
``discover``). The design tools call only ``apply(ops, provenance)``, ``read(version)``,
``design()`` and ``open_requests()`` on what ``layer`` returns, in the proposal's shapes.

- **Orchestra's layer** is written only by its host and its web server (Orchestra's rule 5 as
  amended: nothing else opens anything under ``<state>/model/intended/``), so ``Core`` calls the
  host's events endpoint over HTTP with the project's token: ``POST /intended`` (``{source, author,
  turn, message, quote, reason, request, ops}``, 422 with the refused op's ``index``), ``GET
  /intended`` and ``GET /intended/design``. With its host not running, a call is refused, never
  written anywhere else.
- **Drawn** on Orchestra's Machines page (a machine) and its Dynamics page (a stock, an effect, a
  flow).
- **Keys and names** are Orchestra's own (``orchestra.sd.intended.keys.Keys`` and
  ``names.resolve``), so a draft edit is resolved exactly as Orchestra resolves it; and the names
  declarations lend discovered parts (``display.declared_names``), as the Dynamics page shows them.
- **One deadline per tool call** (``one_call``): after an endpoint timeout, the rest of the call
  fails at once, saying what it did; ``design()`` is read once per call (until a write); the host
  skips its design tick for a while after a timeout (``backing_off``)."""

from __future__ import annotations

import contextvars
import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Protocol

from orchestra.sd.intended.checks import PROVENANCE
from orchestra.sd.intended.display import declared_names  # noqa: F401  (name_part reads the names lent as the pages do)
from orchestra.sd.intended.keys import Keys
from orchestra.sd.intended.names import ABSENT, WAITS, WAITS_LINK  # noqa: F401  (WAITS_LINK: the tools' wording keys on it)
from orchestra.sd.intended.names import resolve as _core_resolve
from orchestra.sd.intended.ops import OPTION

from .config import Project
from .store import StoreError

TIMEOUT_S = 30.0
BACKOFF_S = 300.0
_scope: contextvars.ContextVar[dict | None] = contextvars.ContextVar("intended_call", default=None)
_last_timeout = [0.0]                                  # when an endpoint call last timed out (the host's back-off)


@contextmanager
def one_call():
    """One tool call (or one host tick): one deadline, and ``design()`` read once until a write."""
    if _scope.get() is not None:
        yield
        return
    token = _scope.set({"timed_out": None, "design": None, "applied": []})
    try:
        yield
    finally:
        _scope.reset(token)


def backing_off(now: float | None = None) -> bool:
    """Whether an endpoint call timed out in the last ``BACKOFF_S``: the host skips its design tick."""
    return (now or time.time()) - _last_timeout[0] < BACKOFF_S


def _timed_out(e: BaseException) -> bool:
    return isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)


class Layer(Protocol):
    def apply(self, ops: list[dict], provenance: dict) -> dict: ...
    def read(self, version: int | None = None) -> dict: ...
    def design(self) -> dict: ...
    def open_requests(self) -> dict[str, dict]: ...


def _recorded(project: Project) -> tuple[str, str] | None:
    url, token = os.environ.get("ORCHESTRA_EVENTS_ENDPOINT"), os.environ.get("ORCHESTRA_EVENTS_TOKEN")
    if url and token:
        return url.rstrip("/"), token
    from orchestra import endpoint
    rec = endpoint.recorded(project.orchestra)
    return (rec["url"].rstrip("/"), endpoint.token(project.orchestra)) if rec else None


def endpoint_url(project: Project) -> str | None:
    """Orchestra's own events endpoint for the project, when its host recorded one."""
    addr = _recorded(project)
    return addr[0] if addr else None


def _address(project: Project) -> tuple[str, str]:
    addr = _recorded(project)
    if addr is None:
        raise StoreError("Orchestra's host is not running (orchestra run), and the declared layer is written only "
                         "through its events endpoint: nothing was written. Start it, or ask the owner to")
    return addr


def _applied(scope: dict | None, said: str = "applied before it in this call") -> str:
    """What this tool call had applied when a later request failed: never "nothing" when it did."""
    done = ", ".join((scope or {}).get("applied") or [])
    return f"{said}: {done}" if done else "nothing was applied"


class Core:
    """Orchestra's declared layer through its host's endpoint; refusals said as the tools say theirs."""

    def __init__(self, project: Project):
        self.project = project

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        url, token = _address(self.project)
        scope = _scope.get()
        if scope is not None and scope["timed_out"]:
            raise StoreError(f"Orchestra's endpoint at {url} timed out earlier in this call ({scope['timed_out']}), so nothing more "
                             f"was tried: {_applied(scope, 'applied before it')}")
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
                             f"{out.get('error') or e.reason} (HTTP {e.code}); {_applied(scope)}") from None
        except (urllib.error.URLError, OSError) as e:
            if _timed_out(e):
                _last_timeout[0] = time.time()
                if scope is not None:
                    scope["timed_out"] = f"{method} {path} after {TIMEOUT_S:g} s"
            raise StoreError(f"Orchestra's endpoint at {url} did not answer ({e}); {_applied(scope)}") from None

    def apply(self, ops, provenance):
        body = {k: provenance[k] for k in PROVENANCE if provenance.get(k) is not None}
        scope = _scope.get()
        if scope is not None:
            scope["design"] = None                       # a write: the summary is read again
        out = self._call("POST", "/intended", {**body, "ops": ops})
        changes = out.get("changes") or []
        if scope is not None:
            scope["applied"] += [c.get("change") for c in changes if c.get("change")]
        return {"version": out.get("version"), "changes": changes}

    def read(self, version=None):
        out = self._call("GET", "/intended" + (f"?version={int(version)}" if version is not None else ""))
        return {k: out.get(k) for k in ("version", "at", "layer")}

    def design(self):
        scope = _scope.get()
        if scope is not None and scope["design"] is not None:
            return scope["design"]
        out = {k: v for k, v in self._call("GET", "/intended/design").items() if k != "ok"}
        if scope is not None:
            scope["design"] = out
        return out

    def open_requests(self) -> dict[str, dict]:
        """The owner's requests Orchestra noted, no op answered and that can still be acted on, by
        event id: each row's control, part, id and change."""
        return {i["event"]: i for i in self.design().get("open") or [] if i.get("what") == "unanswered request" and i.get("event")}

    def unpublishable(self) -> dict[tuple[str, str], str]:
        """Orchestra's own rule for a draft (its ``unpublishable draft`` open items): why each
        machine's edit waits, by (machine, the edit as stored)."""
        return {(i["id"], _edit_key(i.get("edit"))): i.get("why") or WAITS
                for i in self.design().get("open") or [] if i.get("what") == "unpublishable draft"}


def _edit_key(edit) -> str:
    return json.dumps(edit, sort_keys=True)


def layer(project: Project) -> Layer:
    return Core(project)


def current(project: Project) -> dict:
    """The declared layer as it is now: ``{stocks, effects, flows, machines, events, targets, marks, ...}``."""
    return layer(project).read()["layer"]


def unpublishable(project: Project, lay: Layer | None = None) -> dict[tuple[str, str], str]:
    """Orchestra's verdict on each draft edit (its ``unpublishable draft`` open items)."""
    return (lay or layer(project)).unpublishable()


def where(project: Project) -> str:
    """Where a declaration is kept, for the tools' answers (``kept_in``)."""
    return "Orchestra's declared layer: the Dynamics and Machines pages draw it"


OPTION_FIELDS = frozenset(OPTION)      # what the layer keeps on a measurement option (with C8: system, series, url, command, bearer_env)


def min_era_s(cfg) -> float:
    """What discovery needs of a new era: its minimum steps on its grid, or the share of its window
    a stock must cover, whichever is longer."""
    from orchestra.sd.discover import MIN_COVER, MIN_STEPS
    return max(MIN_STEPS * cfg.discovery_step, MIN_COVER * cfg.discovery_window)


def _models(project: Project):
    from orchestra.sd.store import ModelStore
    return ModelStore.for_state(project.orchestra.state_dir)


def era_cause(project: Project, era: str | None) -> str | None:
    """The id of the event that began era ``era`` (a prediction is about that change), or None."""
    if not era:
        return None
    from orchestra.sd.eras import recorded
    return next((e.cause for e in recorded(_models(project)) if e.id == era), None)


def current_model(project: Project):
    """The model the judge reads now: the newest saved version, else the latest discovery; None."""
    models = _models(project)
    m = models.load()
    if m is None and (found := models.latest_discovery()) is not None:
        m = found[1]
    return m


def partitions(model) -> dict:
    """The model's partitions by the labels its series carry, the default first, exactly as Orchestra
    serves them in ``/api/dynamics/model``'s ``model.subsystems`` before the declared ones are added
    (``orchestra.sd.subsystems.subsystems``; not yet in its PLUGINS.md §11 list): ``{"default",
    "partitions": [{"label", "groups": [{"value", "stocks", "flows", "machines"}], "crossing",
    "joined", "crossed", ...}]}``. Empty without a model."""
    if model is None:
        return {"default": None, "partitions": []}
    from orchestra.sd.subsystems import subsystems
    return subsystems(model)


def saved_models(project: Project, n: int) -> list:
    """The last ``n`` saved versions, oldest first."""
    models = _models(project)
    return [m for v in models.versions()[-n:] if (m := models.load(v.get("version"))) is not None]


def all_keys(keys) -> set[str]:
    """Every stock and flow key of a model's ``Keys``."""
    return set(keys.stocks) | set(keys.flows)


def resolve(text: str | None, lay: dict, keys: Keys) -> tuple[str | None, str | None, str | None]:
    """One name of a draft edit (a ``stock``, ``from`` or ``to``) as the judge reads it, by
    Orchestra's ``names.resolve``: (the model's id now, the key, why it cannot be published yet). A
    declared stock or effect is read through its ``found_as`` ("waits for a link" while it has none
    but its measure has a chosen option, else "waits for a series"); a discovered stock only by its
    key, never by its id (ids follow arrival order). ``None`` is outside."""
    if text is None:
        return None, None, None
    n = _core_resolve(text, {"stocks": {}, "effects": {}, **lay}, keys)
    if n.why is not None:
        return None, n.key, n.why
    return (n.id, n.key, None) if n.id is not None else (None, n.key, ABSENT)
