"""The instrument a measurement option becomes (machine-design.md §2.2, "add_instrument, step by
step"; §2.1, where writes go).

``add_instrument`` writes a script of at most 100 lines to the state directory
(``~/.orchestrator/<key>/agents/instruments/<name>.sh``) and its ``[[collect]]`` entry (or a
``[[scrape]]`` entry for a URL) to the owner's private overlay through ``config_edit``, as one act:
the script is tried first exactly as Orchestra would run it (``instruments.try_collector``), and a
failed trial, a script over 100 lines or a line that looks like a secret writes nothing. A script
the owner wants in the repository goes through the Coordinator (a ticket, a review, the gate),
never a commit here. Such a script is configuration the Supervisor writes (this package's rule
10 and SPEC §10, amended 2026-11-01 by the owner's proposal 4).

The instrument records at once as a **draft**: the Supervisor's record of it (``drafts`` in its
agent state) expires after ``[agents] draft_expiry`` (24 h by default) unless the owner keeps it
(``keep_instrument``); ``expire`` (the host's tick) removes an unkept one's entry and script as one
act. Orchestra has no draft namespace (intended-model.md §9): the points it wrote stay as
measurements. A simulated instrument's entry carries ``simulated = true``; once a real option of
the same element records, the simulated one is removed."""

from __future__ import annotations

import time
from pathlib import Path

from orchestra import runtime
from orchestra.config import ConfigError, parse_duration
from orchestra.launcher import NAME
from orchestra.overlay import OVERLAY

from . import handoff, instruments
from .config import Project
from .config_change import change
from .design import MACHINE, answer, apply
from .design_dynamics import NEEDS_APPROVAL, options_of, resolve
from .secret_shapes import secret_in
from .setup_tools import INSTRUMENTS_CHANGED
from .store import AgentStore, StoreError

MAX_LINES = 100
FIRST_ROUND_S = 120


def scripts_dir(project: Project) -> Path:
    return project.state_dir / "instruments"


def drafts(store: AgentStore) -> dict:
    return dict(store.agent_state(MACHINE).get("drafts") or {})


def _save(store: AgentStore, d: dict) -> None:
    store.set_agent_state(MACHINE, drafts=d)


def check_script(text: str) -> list[str]:
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        raise StoreError(f"the script is {len(lines)} lines; one the Supervisor writes is at most {MAX_LINES} (anything longer is "
                         "code: hand it to the Coordinator)")
    for n, line in enumerate(lines, 1):
        if why := secret_in(line):
            raise StoreError(f"line {n} looks like it holds a secret ({why}); a credential is named only by its environment "
                             "variable (bearer_env), set in the overlay and supplied out of band. Nothing was written")
    return lines


def _set_option(project: Project, store: AgentStore, part: str, eid: str, key: str, prov: dict, chosen: bool = True, **fields) -> dict:
    m = options_of(project, part, eid)
    for o in m["options"]:
        if o["key"] == key:
            o.update(fields)
    return apply(project, store, [{"op": "record_measurement", "part": part, "id": eid, "options": m["options"],
                                   "chosen": key if chosen else m.get("chosen")}], prov)


def _entry_change(project: Project, section: str, entry: dict | None, name: str, reason: str) -> dict:
    if entry is None:
        return change(project, lambda ed, kw: ed.remove_entry(section, name, **kw), lambda c: f"removed {name} from [[{section}]]",
                      reason, OVERLAY)
    return change(project, lambda ed, kw: ed.add_entry(section, entry, **kw),
                  lambda c: f"{'added' if c.what.startswith('added') else 'changed'} the instrument {name} ([[{section}]], overlay)",
                  reason, OVERLAY)


def _first_points(project: Project, name: str) -> list:
    from orchestra.metrics import for_project
    s = for_project(project.orchestra)
    try:
        dump = s.dump("", {"project": project.key, "instrument": name})
    finally:
        s.close()
    pts = sorted(p for _, _, ps in dump for p in ps)
    return [[round(t, 1), v] for t, v in pts[:5]]


def add_instrument(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    part, eid, declared = resolve(project, a.get("element"))
    if not declared:
        raise StoreError(f"{eid} is not a declared stock or effect")
    key = (a.get("option") or "").strip()
    option = next((o for o in options_of(project, part, eid)["options"] if o["key"] == key), None)
    if option is None:
        raise StoreError(f"{eid} has no option {key!r}: propose_measurements first, then add the one you pick")
    if option.get("clearance") == NEEDS_APPROVAL:
        raise StoreError(f"option {key} reads a company system not cleared for the model (needs approval); nothing reads it "
                         "until the owner clears it")
    name, kind = (a.get("name") or "").strip(), a.get("kind") or "collect"
    if not NAME.match(name):
        raise StoreError("the instrument's name is letters, digits, '.', '_' and '-' (features.acceptance)")
    if kind == "source":
        raise StoreError("a source is read in place at each discovery, not recorded: declare the store with set_setting "
                         "key sources (check_sources and infer_source first) and say which option it is")
    try:
        every = a.get("every") or "60s"
        parse_duration(every, 60.0)
    except ConfigError as e:
        raise StoreError(f"every: {e}") from None
    script, command, url = a.get("script") or "", (a.get("command") or "").strip(), (a.get("url") or "").strip()
    if kind == "collect" and bool(script.strip()) == bool(command):
        raise StoreError("a [[collect]] instrument takes a script (written to the state directory) or the command of a tool "
                         "already there (python -m orchestra.instruments.test_reports ...), not both")
    if kind == "scrape" and not url:
        raise StoreError("a [[scrape]] instrument takes the url that serves Prometheus text")
    if script:
        check_script(script)
    if a.get("where") == "repo":
        req = handoff.hand_to_coordinator(store, {"requirements": (
            f"Add the measurement script for {eid} (option {key}) to the repository, at machines/{name}.sh, with its [[collect]] "
            f"entry `name = \"{name}\"`, every {every}, in orchestra.toml, through a pull request the owner approves.\n\n"
            f"The script, as the Supervisor tried it:\n\n{script or command}\n"), "context": prov["reason"]}, MACHINE)
        return {"ok": True, "request": req["request"], "written": False,
                "said": [(f"handed to the Coordinator as {req['request']}: the script lands through a ticket, a review and the "
                          "gate; nothing was written here")]}
    path = None
    if script:
        trial = project.state_dir / "trials" / f"{name}.sh"
        trial.parent.mkdir(parents=True, exist_ok=True)
        trial.write_text(script if script.startswith("#!") else "#!/bin/sh\n" + script, encoding="utf-8")
        trial.chmod(0o755)
        command = str(trial)
    tried = instruments.try_collector(project, {"command": command} if kind == "collect" else {"url": url})
    if not tried.get("works"):
        if script:
            Path(command).unlink(missing_ok=True)
        raise StoreError("the trial round failed, so nothing was written: " + "; ".join(
            str(tried.get(k)) for k in ("error", "bad_lines", "refused", "stderr") if tried.get(k)))
    if script:
        path = scripts_dir(project) / f"{name}.sh"
        path.parent.mkdir(parents=True, exist_ok=True)
        Path(command).replace(path)
        command = str(path)
    section = "collect" if kind == "collect" else "scrape"
    entry = {"name": name, **({"command": command} if kind == "collect" else {"url": url}), "every": every}
    if option.get("simulated") or a.get("simulated"):
        entry["simulated"] = True
    try:
        done = _entry_change(project, section, entry, name, prov["reason"])
    except StoreError:
        if path:
            path.unlink(missing_ok=True)
        raise
    store.events.emit(INSTRUMENTS_CHANGED, section=section, change=done["announce"], reason=prov["reason"], commit=None,
                      target=OVERLAY, applies="orchestra run reads it within a second and records on its interval", by=MACHINE)
    now = time.time()
    expiry = project.config.draft_expiry_s
    d = drafts(store)
    d[name] = {"element": f"{part}:{eid}", "option": key, "section": section, "script": str(path) if path else None,
               "at": now, "until": now + expiry, "kept": False, "simulated": bool(entry.get("simulated"))}
    _save(store, d)
    replaced = []
    if not entry.get("simulated"):                  # a real option records: the placeholder goes
        for other, rec in list(d.items()):
            if other != name and rec["element"] == f"{part}:{eid}" and rec.get("simulated"):
                remove(project, store, other, f"a real option ({key}) records {eid} now; the simulated placeholder goes", prov)
                replaced.append(other)
    first, waited = [], False
    if runtime.launcher(runtime.read(project.orchestra)) is not None:
        waited = True
        instruments.read_instrument(project, {"name": name, "wait_s": min(int(a.get("wait_s") or FIRST_ROUND_S), FIRST_ROUND_S)})
        first = _first_points(project, name)
    where = f"written by the Supervisor · {path}" if path else f"the command {command}"
    rec = _set_option(project, store, part, eid, key, prov, instrument=name, first_points=first,
                      provenance=f"{where} · entry in the owner's overlay · recording · draft until kept")
    lines = [(f"instrument {name} records {eid} (option {key}) every {every}, as a draft that expires in "
              f"{round(expiry / 3600)} h unless kept (keep_instrument)"),
             f"script: {path}" if path else f"command: {command}",
             (f"first points: {first}" if first else "no point yet: " + ("its first round has not ended" if waited else
                                                                           "no `orchestra run` runs this project now")),
             "the Dynamics page shows it in the element's drawer: recording · draft, the sparkline, the script and its entry"]
    if replaced:
        lines.append(f"removed the simulated placeholder(s) {', '.join(replaced)}")
    return answer(project, rec, lines, instrument=name, script=str(path) if path else None, first_points=first,
                  trial=tried.get("series"))


def remove(project: Project, store: AgentStore, name: str, reason: str, prov: dict | None = None) -> None:
    """Take a draft instrument out: its entry and its script, as one act, and the record."""
    d = drafts(store)
    rec = d.pop(name, None)
    if rec is None:
        return
    try:
        _entry_change(project, rec["section"], None, name, reason)
    except StoreError as e:
        if "no [[" not in str(e):
            raise
    if rec.get("script"):
        Path(rec["script"]).unlink(missing_ok=True)
    _save(store, d)
    part, _, eid = rec["element"].partition(":")
    if prov is not None:
        _set_option(project, store, part, eid, rec["option"], prov, chosen=False, instrument=None,
                    provenance=f"removed: {reason}")


def keep_instrument(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    name = (a.get("name") or "").strip()
    d = drafts(store)
    if name not in d:
        raise StoreError(f"no draft instrument {name!r}; read_design lists them")
    d[name].update(kept=True, until=None, kept_at=time.time())
    _save(store, d)
    part, _, eid = d[name]["element"].partition(":")
    done = _set_option(project, store, part, eid, d[name]["option"], prov, provenance=(
        f"written by the Supervisor · {d[name]['script']} · kept" if d[name].get("script") else "kept"))
    return answer(project, done, [(f"instrument {name} kept: it no longer expires; the option shows \"chosen\", and discovery "
                                   "reads its series like any other")], instrument=name)


def expire(project: Project, store: AgentStore, now: float | None = None) -> list[str]:
    """Remove every unkept draft past its expiry (the host's tick); the names removed."""
    now = now or time.time()
    gone = []
    for name, rec in drafts(store).items():
        if not rec.get("kept") and rec.get("until") and now >= rec["until"]:
            prov = {"author": MACHINE, "via": "plugin", "source": MACHINE, "at": now, "turn": None, "message": None,
                    "quote": None, "reason": "a draft instrument the owner did not keep expired"}
            remove(project, store, name, prov["reason"], prov)
            gone.append(name)
    return gone


COMMANDS = {"add_instrument": add_instrument, "keep_instrument": keep_instrument}
