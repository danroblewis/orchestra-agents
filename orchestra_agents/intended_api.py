"""The declared layer, as the design tools reach it: Orchestra's own (``docs/proposals/intended-model.md``
§6.3, built in Orchestra's task C1) when the installed Orchestra has it, else the agents' stand-in
(``intended_standin.py``); and every other name of Orchestra's the design tools read that Orchestra
does not document yet, each with what to do when it is absent.

**The integration seam.** This module is the one place that chooses, and the one place that imports
an undocumented Orchestra name (the coordinator's list asks for public ones). The design tools call
only ``apply(ops, provenance)``, ``read(version)``, ``design()`` and ``open_requests()`` on what
``layer`` returns, in the proposal's shapes, so when Orchestra's store is installed it is used with
no other change, and the stand-in (with this fallback) is deleted.

- **Orchestra's** is written only by its host and its web server (Orchestra's rule 5 as amended:
  nothing else opens anything under ``<state>/model/intended/``), so ``Core`` calls the host's
  events endpoint over HTTP with the project's token: ``POST /intended`` (``{source, author, turn,
  message, quote, reason, request, ops}``, 422 with the refused op's ``index``), ``GET /intended``
  and ``GET /intended/design``. With it installed and its host not running, a call is refused,
  never written anywhere else.
- **Whether Orchestra has it** is asked of the endpoint itself (``GET /intended?version=0``), and,
  when no endpoint answers, of the documented ``project.intended`` (§6.3).
- **The stand-in** is used only when the installed Orchestra has no declared layer. What it holds
  is kept in the agents' state directory, and no page draws it.
- **Drawn** where the installed Orchestra ships the page that draws it (``pages``: its Machines
  page's ``intended-machines.js``, its Dynamics page's ``intended-dynamics.js``), or where its
  summary says so (``draws``, if it ever has one); otherwise the tools say "kept" and read it back.
- **Keys and names** (``Keys``, ``resolve``): Orchestra's own (``orchestra.sd.intended.keys`` and
  ``names``) when they import, so a draft edit is resolved exactly as Orchestra resolves it; the
  local copy is the stand-in's fallback only.
- **One deadline per tool call** (``one_call``): after an endpoint timeout, the rest of the call
  fails at once, saying what it did; ``design()`` is read once per call (until a write); the host
  skips its design tick for a while after a timeout (``backing_off``).
- **Carried over once** (``intended_carry.py``): on the first use of Orchestra's layer, the
  stand-in's calls are replayed into it."""

from __future__ import annotations

import contextvars
import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Protocol

from .config import Project
from .store import StoreError

PROVENANCE = ("source", "author", "turn", "message", "quote", "reason", "request")
TIMEOUT_S = 30.0
PROBE_S = 3.0
CACHE_S = 30.0
BACKOFF_S = 300.0
try:
    from orchestra.sd.intended.names import ABSENT, WAITS, WAITS_LINK
except ImportError:
    WAITS, WAITS_LINK, ABSENT = "waits for a series", "waits for a link", "not in the model"
PAGES = {"Machines": "intended-machines.js", "Dynamics": "intended-dynamics.js"}
_cache: dict[tuple, tuple[float, object]] = {}
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


def _cached(key: tuple, fn, ttl: float = CACHE_S):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


def core_installed() -> bool:
    """Whether the installed Orchestra documents a declared layer: ``project.intended`` (§6.3)."""
    from orchestra.config import Project as OrchestraProject
    return hasattr(OrchestraProject, "intended")


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


def has_core(project: Project) -> bool:
    """Whether Orchestra keeps the declared layer: its endpoint answers ``GET /intended``; with no
    endpoint answering, whether the installed Orchestra documents ``project.intended``."""
    addr = _recorded(project)
    if addr is None:
        return core_installed()

    def probe() -> bool:
        req = urllib.request.Request(addr[0] + "/intended?version=0", headers={"Authorization": f"Bearer {addr[1]}"})
        try:
            with urllib.request.urlopen(req, timeout=PROBE_S) as r:
                return r.status == 200
        except urllib.error.HTTPError as e:
            return False if e.code in (404, 405) else core_installed()      # no such route; else ask the install
        except (urllib.error.URLError, OSError):
            return core_installed()
    return _cached(("core", addr[0]), probe)


def _address(project: Project) -> tuple[str, str]:
    addr = _recorded(project)
    if addr is None:
        raise StoreError("Orchestra's host is not running (orchestra run), and the declared layer is written only "
                         "through its events endpoint: nothing was written. Start it, or ask the owner to")
    return addr


class Core:
    """Orchestra's declared layer through its host's endpoint; refusals said as the tools say theirs."""

    def __init__(self, project: Project):
        self.project = project

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        url, token = _address(self.project)
        scope = _scope.get()
        if scope is not None and scope["timed_out"]:
            done = ", ".join(scope["applied"])
            raise StoreError(f"Orchestra's endpoint at {url} timed out earlier in this call ({scope['timed_out']}), so nothing more "
                             "was tried: " + (f"applied before it: {done}" if done else "nothing was applied"))
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
            if _timed_out(e):
                _last_timeout[0] = time.time()
                if scope is not None:
                    scope["timed_out"] = f"{method} {path} after {TIMEOUT_S:g} s"
            raise StoreError(f"Orchestra's endpoint at {url} did not answer ({e}); nothing was applied") from None

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


def standin_dir(project: Project) -> Path:
    return project.state_dir / "intended"


def layer(project: Project) -> Layer:
    if has_core(project):
        from .intended_carry import carry_over
        core = Core(project)
        carry_over(project, core)
        return core
    from .intended_standin import StandIn
    return StandIn(project)


def current(project: Project) -> dict:
    """The declared layer as it is now: ``{stocks, effects, flows, machines, events, targets, marks, ...}``."""
    return layer(project).read()["layer"]


def unpublishable(project: Project, lay: Layer | None = None) -> dict[tuple[str, str], str]:
    """Orchestra's verdict on each draft edit when Orchestra keeps the layer; empty for the stand-in
    (whose rule is the plugin's, ``design_machines.publishable``)."""
    lay = lay or layer(project)
    return lay.unpublishable() if isinstance(lay, Core) else {}


def _shipped(asset: str) -> bool:
    try:
        return resources.files("orchestra.web").joinpath("static", asset).is_file()
    except (ModuleNotFoundError, OSError):
        return False


def pages(project: Project) -> set[str]:
    """The pages that draw the declared layer: none for the stand-in; with Orchestra's, what its
    summary says (``draws``) if it says anything, else the pages the installed Orchestra ships
    (``PAGES``)."""
    if not has_core(project):
        return set()

    def ask() -> set[str]:
        try:
            said = Core(project).design().get("draws")
        except StoreError:
            said = None
        if isinstance(said, list | tuple | set):
            return {p for p in PAGES if p.lower() in {str(x).lower() for x in said}}
        return {p for p, asset in PAGES.items() if _shipped(asset)}
    return _cached(("pages", str(project.state_dir)), ask)


def drawn(project: Project, page: str | None = None) -> bool:
    """Whether a page (``page``: that one) draws the declared layer."""
    got = pages(project)
    return page in got if page else bool(got)


def where(project: Project) -> str:
    """Where a declaration is kept, for the tools' answers (``kept_in``)."""
    if got := pages(project):
        return f"Orchestra's declared layer: the {' and '.join(sorted(got))} page{'s draw' if len(got) > 1 else ' draws'} it"
    if has_core(project):
        return "Orchestra's declared layer (no page draws it in this Orchestra): kept, and read_design reads it back"
    return ("the agents' stand-in for Orchestra's declared layer (this Orchestra has none yet): kept, and no page draws it; "
            "read_design reads it back")


BASE_OPTION = frozenset({"key", "title", "how", "kind", "instrument", "simulated", "cost", "caveat", "clearance",
                         "provenance", "commit", "first_points"})


def option_fields(project: Project) -> frozenset[str]:
    """The fields the declared layer keeps on a measurement option: Orchestra's ``ops.OPTION``
    with its layer (since its C8, also what an option names: system, series, url, command,
    bearer_env), the stand-in's own otherwise."""
    if has_core(project):
        try:
            from orchestra.sd.intended.ops import OPTION
        except ImportError:
            return BASE_OPTION
        return frozenset(OPTION)
    from .intended_standin_ops import OPTION
    return frozenset(OPTION)


def takes_description(project: Project) -> bool:
    """Whether ``declare_stock``/``declare_effect`` take a ``description``: Orchestra's layer since
    the same change that made its options keep what they name (C8), and the stand-in."""
    return "series" in option_fields(project)


def draft_flag_supported() -> bool:
    """Whether ``[[collect]]``/``[[scrape]]`` entries take ``draft`` and ``expires``."""
    try:
        from orchestra.collect.spec import Collector
    except ImportError:
        return False
    return {"draft", "expires"} <= set(getattr(Collector, "__dataclass_fields__", {}))


def min_era_s(cfg) -> float:
    """What discovery needs of a new era: its minimum steps on its grid, or the share of its window
    a stock must cover, whichever is longer."""
    try:
        from orchestra.sd.discover import MIN_COVER, MIN_STEPS
    except ImportError:
        MIN_COVER, MIN_STEPS = 0.5, 5
    return max(MIN_STEPS * cfg.discovery_step, MIN_COVER * cfg.discovery_window)


def _models(project: Project):
    from orchestra.sd.store import ModelStore
    return ModelStore.for_state(project.orchestra.state_dir)


def era_cause(project: Project, era: str | None) -> str | None:
    """The id of the event that began era ``era`` (a prediction is about that change), or None."""
    if not era:
        return None
    try:
        from orchestra.sd.eras import recorded
    except ImportError:
        return None
    return next((e.cause for e in recorded(_models(project)) if e.id == era), None)


def current_model(project: Project):
    """The model the judge reads now: the newest saved version, else the latest discovery; None."""
    models = _models(project)
    m = models.load()
    if m is None and (found := models.latest_discovery()) is not None:
        m = found[1]
    return m


def saved_models(project: Project, n: int) -> list:
    """The last ``n`` saved versions, oldest first."""
    models = _models(project)
    return [m for v in models.versions()[-n:] if (m := models.load(v.get("version"))) is not None]


def _left_out() -> set[str]:
    try:
        from orchestra.sd.discover import NOT_IDENTITY
    except ImportError:
        NOT_IDENTITY = {"instrument", "machine", "source", "project", "instance"}
    return set(NOT_IDENTITY) | {"simulated", "draft"}


def _series_key(name: str, labels: dict) -> str:
    try:
        from orchestra.sd.series import series_key
    except ImportError:
        def series_key(n, lab):
            inner = ",".join(f'{k}="{v}"' for k, v in sorted(lab.items()) if k != "project")
            return f"{n}{{{inner}}}" if inner else n
    return series_key(name, labels)


def ref_key(ref) -> str | None:
    """A series' key as the declared layer matches parts by (intended-model.md §3.1): its name and
    the labels that say which thing it measures."""
    if ref is None:
        return None
    left = _left_out()
    return _series_key(ref.name, {k: v for k, v in (ref.labels or {}).items() if k not in left})


def _stock_key(stock) -> str:
    """Its series' key; else the key of the level its counters imply (``evidence["level"]``, a
    state between counters); ``stock:<id>`` only for a stock with neither."""
    level = (stock.evidence or {}).get("level") if isinstance(getattr(stock, "evidence", None), dict) else None
    implied = None
    if isinstance(level, dict) and level.get("name"):
        implied = _series_key(level["name"], {k: v for k, v in (level.get("labels") or {}).items() if k not in _left_out()})
    return ref_key(stock.series) or implied or f"stock:{stock.id}"


class _LocalKeys:
    """The stand-in's fallback for Orchestra's ``keys.Keys`` (``intended-model.md`` §3.1), as
    Orchestra's main keys a model: ``stocks`` and ``flows`` key -> id, ``stock_of_id`` id -> key."""

    def __init__(self, model):
        self.model = model
        self.stock_of_id = {s.id: _stock_key(s) for s in model.stocks} if model else {}
        self.stocks: dict[str, str] = {}
        for sid, k in self.stock_of_id.items():
            self.stocks.setdefault(k, sid)
        self.flows: dict[str, str] = {}
        for f in (model.flows if model else []):
            a = self.stock_of_id.get(f.src, f.src) if f.src else "outside"
            b = self.stock_of_id.get(f.dst, f.dst) if f.dst else "outside"
            counter = ref_key(f.counter) or "+".join(sorted(filter(None, (ref_key(r) for r in f.measured_by or []))))
            self.flows.setdefault(f"{a} -> {b}" + (f" by {counter}" if counter else ""), f.id)


try:
    from orchestra.sd.intended.keys import Keys
    from orchestra.sd.intended.names import resolve as _core_resolve
except ImportError:
    Keys, _core_resolve = _LocalKeys, None


def all_keys(keys) -> set[str]:
    """Every stock and flow key of a model's ``Keys``."""
    return set(keys.stocks) | set(keys.flows)


def resolve(text: str | None, lay: dict, keys) -> tuple[str | None, str | None, str | None]:
    """One name of a draft edit (a ``stock``, ``from`` or ``to``) as the judge reads it: (the model's
    id now, the key, why it cannot be published yet). A declared stock or effect is read through
    its ``found_as`` ("waits for a link" while it has none but its measure has a chosen option,
    else "waits for a series"); a discovered stock only by its key, never by its id (ids follow
    arrival order). ``None`` is outside. Orchestra's ``names.resolve`` when it imports."""
    if text is None:
        return None, None, None
    lay = {"stocks": {}, "effects": {}, **lay}
    if _core_resolve is not None and isinstance(keys, Keys):
        n = _core_resolve(text, lay, keys)
        if n.why is not None:
            return None, n.key, n.why
        return (n.id, n.key, None) if n.id is not None else (None, n.key, ABSENT)
    decl = lay["stocks"].get(text) or lay["effects"].get(text)
    if decl is not None:
        if not decl.get("found_as"):
            return None, None, WAITS_LINK if (decl.get("measure") or {}).get("chosen") else WAITS
        text = decl["found_as"]
    if text in keys.stocks:
        return keys.stocks[text], text, None
    return None, text, ABSENT
