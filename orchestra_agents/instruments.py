"""The Supervisor's view of an instrument, before and after it is declared: a trial round of a
collector (``try_collector``), run exactly as Orchestra's launcher would run it, and what a declared
instrument has done since (``read_instrument``): its last round, its error, its log and the points
it wrote. Both go through Orchestra as a library (``orchestra.collect``, the Instruments page's
``instruments_view``, the metrics store); neither writes to the metrics store or the configuration.

A trial round runs the command once in the repository root with Orchestra's environment (``python``
is Orchestra's Python, even inside ``sh -c``), parses what it printed with Orchestra's own parser,
and says which series it would store, which lines it did not understand and which names it would
refuse. Its log goes to a scratch file in the agents' state, never to the instrument's own log."""

from __future__ import annotations

import time
from pathlib import Path

from orchestra.collect.round import collect as collect_round
from orchestra.collect.spec import TIMEOUT_S, Collector, check
from orchestra.config import ConfigError, parse_duration
from orchestra.metrics import for_project
from orchestra.web.machine_logs import tail

from .config import Project
from .store import StoreError

SHOWN_SERIES = 40          # series listed from a trial round or a read
SHOWN_POINTS = 5           # latest points per series in a read
MAX_ONE_LINER = 300        # a shell command longer than this is a script: a worker's, through a ticket


def shell_command(command: str) -> list[str]:
    """A one-liner as the words Orchestra runs: ``sh -c '<command>'``. A command that spans lines, or
    is longer than MAX_ONE_LINER, is a script, and a script is code: refused, saying so."""
    command = (command or "").strip()
    if not command:
        raise StoreError("give the command")
    if "\n" in command or len(command) > MAX_ONE_LINER:
        raise StoreError(f"that is a script, not a one-liner (over {MAX_ONE_LINER} characters, or several lines): a script "
                         "is code, written by a worker under machines/ through a ticket (hand_to_coordinator); declare it "
                         "with set_collector once it has landed")
    return ["sh", "-c", command]


def _trial(a: dict) -> Collector:
    command, url = (a.get("command") or "").strip(), (a.get("url") or "").strip()
    if bool(command) == bool(url):
        raise StoreError("give a command (what [[collect]] would run) or a url (what [[scrape]] would read), not both")
    try:
        timeout = parse_duration(a.get("timeout"), TIMEOUT_S) if a.get("timeout") else TIMEOUT_S
    except ConfigError as e:
        raise StoreError(f"timeout: {e}") from e
    if url:
        c = Collector.scrape("trial", url, every_s=max(timeout, 1.0), timeout_s=timeout)
    else:
        c = Collector.collect("trial", shell_command(command) if a.get("shell") else command,
                              every_s=max(timeout, 1.0), timeout_s=timeout)
    if why := check(c, []):
        raise StoreError(why.replace("trial: ", ""))
    return c


def try_collector(project: Project, a: dict) -> dict:
    """One round of the collector ``a`` describes, as Orchestra would run it, stored nowhere."""
    c = _trial(a)
    log = project.state_dir / "trials" / "collector.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("", encoding="utf-8")
    began = time.time()
    r = collect_round(c, project.repo_root, project.key, log)
    took = round(time.time() - began, 2)
    stderr = tail(log, 20) if log.exists() else []
    if r.error:
        return {"ok": True, "works": False, "error": r.error, "took_s": took, "stderr": stderr,
                "next": "Fix the command (read the error and stderr) and try it again; do not declare it until it works."}
    series = [{"name": p.series, "labels": {k: v for k, v in p.labels.items() if k not in ("project", "instrument")},
               "value": p.value} for p in r.points]
    out = {"ok": True, "works": bool(series) and not r.bad and not r.refused, "took_s": took, "series": series[:SHOWN_SERIES],
           "series_count": len(series), "would_run": c.shown}
    if len(series) > SHOWN_SERIES:
        out["more"] = f"{len(series) - SHOWN_SERIES} more series not shown"
    if r.bad:
        out["bad_lines"] = [f"line {n}: {why}" for n, why in r.bad[:10]]
    if r.refused:
        out["refused"] = [f"{n}: names starting with orchestra_ are Orchestra's own; rename it" for n in r.refused]
    if r.left_out:
        out["left_out"] = r.left_out[:10]
    if not series:
        out["error"] = "it printed no measurement Orchestra understands: one `name value` (or `name{label=\"x\"} value`) per line"
    if stderr:
        out["stderr"] = stderr
    if took > c.timeout_s * 0.5:
        out["timing"] = (f"it took {took:g} s of its {c.timeout_s:g} s timeout: give the entry a timeout well above "
                         "what it takes, and an every at least as long")
    return out


def _round_of(view: dict, name: str) -> tuple:
    e = next((i for i in view.get("instruments") or [] if i.get("name") == name), None) or {}
    if e.get("kind") == "source":                 # a source's round is a discovery
        return (view.get("discovered_at"),)
    return tuple(e.get(k) for k in ("last_write", "points", "last_error_at", "failures"))


def _wait_for_round(project: Project, name: str, view: dict, until: float) -> dict:
    """The view once the instrument's record changed (a round ended), or at ``until``."""
    before = _round_of(view, name)
    while time.time() < until:
        time.sleep(1.0)
        view = _view(project)
        if _round_of(view, name) != before:
            return view
    return view


def _view(project: Project) -> dict:
    """The Instruments page's read, with how the latest discovery found each source (as the page has it)."""
    from orchestra.sd.store import ModelStore
    from orchestra.web.instruments import instruments_view
    try:
        cached = ModelStore.for_state(project.orchestra.state_dir).latest_discovery()
    except Exception:  # noqa: BLE001 - the rest of the view stands without it
        cached = None
    view = instruments_view(project.orchestra, cached[1].discovery if cached else None)
    view["discovered_at"] = cached[0] if cached else None
    return view


MAX_WAIT_S = 120


def read_instrument(project: Project, a: dict) -> dict:
    """A declared instrument as Orchestra sees it (the Instruments page: when it last wrote, how many
    points, its failures and last error), the end of its log, and the latest points of each series
    it wrote in the last ``minutes`` (default 30). With ``wait_s``, it first waits up to that long
    (at most MAX_WAIT_S) for the instrument's next round to end, written or failed."""
    name = (a.get("name") or "").strip()
    if not name:
        raise StoreError("give the instrument's name, as in [[collect]] or [[scrape]]")
    wait = min(float(a.get("wait_s") or 0), MAX_WAIT_S)
    view = _view(project)
    if wait > 0:
        view = _wait_for_round(project, name, view, time.time() + wait)
    entry = next((i for i in view.get("instruments") or [] if i.get("name") == name), None)
    errors = [e for e in view.get("errors") or [] if name in e]
    if entry is None:
        return {"ok": True, "found": False, "errors": errors,
                "known": [i.get("name") for i in view.get("instruments") or []],
                "note": f"no instrument named {name} is declared (or it was left out: see errors)"}
    if entry.get("kind") == "source":
        st = entry.get("status")
        at = view.get("discovered_at")
        return {"ok": True, "found": True, "instrument": entry, "errors": errors,
                "latest_discovery_s_ago": round(time.time() - at) if at else None,
                "note": ("a source is read in place at each discovery and stores nothing here: `status` is how the latest "
                         "discovery read it (series, gaps, error)" if st else
                         "no discovery has read it yet: one runs every [discovery] every (5 min by default) while `orchestra "
                         "run` runs; infer_source shows now what it would read")}
    log = Path(entry["log"]) if entry.get("log") else project.orchestra.logs_dir / "instruments" / f"{name}.log"
    minutes = int(a.get("minutes") or 30)
    since, now = time.time() - minutes * 60, time.time()
    store = for_project(project.orchestra)
    try:
        dump = store.dump("", {"project": project.key, "instrument": name})
    finally:
        store.close()
    series = []
    for sname, labels, pts in dump:
        recent = [(t, v) for t, v in pts if t >= since]
        if not recent:
            continue
        series.append({"name": sname, "labels": {k: v for k, v in labels.items() if k not in ("project", "instrument")},
                       "points": len(recent), "last_s_ago": round(now - recent[-1][0]),
                       "latest": [v for _, v in recent[-SHOWN_POINTS:]]})
    out = {"ok": True, "found": True, "instrument": entry, "host_running": view.get("host_running"),
           "series": series[:SHOWN_SERIES], "series_count": len(series), "window_minutes": minutes,
           "log": tail(log, 15) if log.exists() else ["(no log yet: it has not run)"]}
    if errors:
        out["errors"] = errors
    if not view.get("host_running"):
        out["note"] = "no `orchestra run` is running for this project, so nothing collects now"
    elif not series:
        out["note"] = (f"it has stored no point in the last {minutes} min: it has not had a round yet (wait one `every`), "
                       "or its rounds fail (last_error and the log say why)")
    return out


__all__ = ["read_instrument", "shell_command", "try_collector"]
