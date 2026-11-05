"""Each stock's level now, as Orchestra's Dynamics page has it, for ``read_model``.

A stock bound to the project's own store keeps the level Orchestra's model read gives
(``orchestra.sd.reads``: the own store's latest point), said to come from the ``own store``. A
stock fed by a source (``[[sources]]``) is never read from the own store, where it is not, nor from
its source here: its level is the one the web server read through that source in its last round
(``GET /api/dynamics/model``'s ``model.levels``, each with ``measured_by: "source <name>"``,
behind the project's web token, at the port the web server recorded in Orchestra's runtime
record). When it has none, the stock says why: the source's error as the page has it, the web
server not running or not answering, or no round read yet (a source's levels are read while a
page or a tool asks for them)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from orchestra import runtime
from orchestra.runtime import alive

from . import local_http
from .config import Project
from .store import StoreError

PATH = "/api/dynamics/model"
TIMEOUT_S = 10.0
OWN = "own store"
NOT_YET = ("the web server has not read it yet: it reads a source's levels while a page or a tool asks for them; "
           "read_model again in a few seconds")


def fetch(project: Project, version: int | None = None) -> dict:
    """The Dynamics page's model answer (tests replace this)."""
    from orchestra.web import auth
    web = runtime.read(project.orchestra).get("web") or {}
    if not web.get("port") or not web.get("pid") or not alive(web["pid"]):
        raise StoreError("Orchestra's web server is not running (orchestra web), and a source's levels are read "
                         "through it, as the Dynamics page shows them")
    query = f"?{urllib.parse.urlencode({'version': version, 'since': 'off'})}" if version is not None else "?since=off"
    req = urllib.request.Request(f"http://127.0.0.1:{int(web['port'])}{PATH}{query}",
                                 headers={"Authorization": f"Bearer {auth.token(project.orchestra)}"})
    try:
        with local_http.urlopen(req, TIMEOUT_S) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise StoreError(f"Orchestra's web server refused the Dynamics page's model (HTTP {e.code})") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise StoreError(f"Orchestra's web server did not answer ({e})") from None


def _sources(model) -> dict[str, str]:
    """The stocks fed by a source: id -> the source's name."""
    return {s.id: s.series.source for s in model.stocks
            if s.series and s.series.source not in (None, "own")}


def _why(error: str | None, source: str) -> str:
    """The page's reason a source's levels are missing: its own part of ``levels_error``."""
    if not error:
        return NOT_YET
    parts = [p.strip() for p in error.split("; ")]
    mine = [p.removeprefix(f"source {source}: ") for p in parts if p.startswith(f"source {source}: ")]
    return "; ".join(mine) if mine else error


def attach(project: Project, model, view: dict, version: int | None = None) -> None:
    """Say where each stock's level came from in ``view["stocks"]`` (``measured_by``), and give a
    source-fed stock the level the web server read through its source, or why it has none
    (``level_unread``)."""
    fed = _sources(model)
    for row in view.get("stocks") or []:
        if row["id"] not in fed and "level" in row:
            row["measured_by"] = OWN
    rows = [r for r in view.get("stocks") or [] if r["id"] in fed and not r.get("hidden")]
    if not rows:
        return
    try:
        page = fetch(project, version)
        m = page.get("model") if page.get("ok") else None
        if m is None:
            raise StoreError(f"the Dynamics page has no model to read levels for ({page.get('error') or 'no model yet'})")
    except StoreError as e:
        m, unreachable = None, str(e)
    for row in rows:
        src = fed[row["id"]]
        row.pop("level", None)
        row["measured_by"] = f"source {src}"
        lv = (m.get("levels") or {}).get(row["id"]) if m else None
        if lv is not None and lv.get("v") is not None:
            row["level"], row["level_at"] = lv["v"], lv.get("t")
            row["measured_by"] = lv.get("measured_by") or row["measured_by"]
        else:
            row["level_unread"] = _why(m.get("levels_error"), src) if m else unreachable
