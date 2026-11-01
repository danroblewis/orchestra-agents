"""The instrument a measurement option becomes (machine-design.md §2.2, "add_instrument, step by
step"; §2.1, where writes go).

``add_instrument`` writes a script of at most 100 lines to the state directory
(``~/.orchestrator/<key>/agents/instruments/<name>.sh``) and its ``[[collect]]`` entry (or a
``[[scrape]]`` entry for a URL) to the owner's private overlay through ``config_edit``, as one act.
It runs only what the option proposed (its url or command, kept on the option), never
an option that needs the owner's approval, nothing that names or reaches a company system (the
script's own text included, ``design_clearance.far``), and nothing that looks like a secret; it
never takes over an instrument of the same name already configured; the script is tried first
exactly as Orchestra would run it (``instruments.try_collector``), and a failed trial or a script
over 100 lines writes nothing. A script the owner wants in the repository is tried the same way
and then handed to the Coordinator (a ticket, a review, the gate), never a commit here (SPEC §10
says how this differs from machine-design.md §2.1's pull request). Such a script is configuration
the Supervisor writes (this package's rule 10 and SPEC §10, amended 2026-11-01 by the owner's
proposal 4). Every write, removal and keep is announced as ``instruments.changed`` (``by`` the
Supervisor, target the overlay, with its reason).

**Drafts.** The instrument records at once as a draft: its entry carries Orchestra's ``draft =
true`` and ``expires`` (RFC 3339) when the installed Orchestra's collector spec has them
(``supports_draft``; Orchestra labels the points ``draft="true"`` and discovery leaves them out
until the flag is cleared). The Supervisor's own record (``drafts`` in its agent state, updated
under the runtime lock) expires after ``[agents] draft_expiry`` (24 h by default) unless the owner
keeps it: ``keep_instrument`` takes ``draft`` and ``expires`` off the entry, records it kept, then
marks the option chosen (retried on the host's tick if that write fails, ``retry_chosen``);
``expire`` (the host's tick) keeps one the owner's expect mark backs and removes an unkept one's
entry and script. The points it wrote stay in the store. An Orchestra without the flag records it
as an ordinary instrument, which discovery reads at once, so it is not removed on a timer (that
would take a stock out of the model): it is kept pending the owner's word. A simulated
instrument's entry carries ``simulated = true`` and never ``draft``, so discovery reads it like any
other; it is still removed at its expiry unless kept, and once a real option of the same element
records, the simulated one is removed. Discarding a declared element removes its unkept drafts and
simulated placeholders, never one the owner kept. Keeping one needs the owner's backing: their
quoted words, or their expect mark on the instrument's series or on the element's linked stock."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from orchestra import runtime
from orchestra.config import ConfigError, parse_duration
from orchestra.launcher import NAME
from orchestra.overlay import OVERLAY

from . import handoff, instruments, intended_api
from .config import Project
from .config_change import change
from .design import MACHINE, OWNER, answer, apply, page_line
from .design_clearance import NEEDS_APPROVAL, UNTIL, company_named, far
from .design_dynamics import no_secret, options_of, resolve
from .secret_shapes import secret_in
from .setup_tools import INSTRUMENTS_CHANGED
from .store import AgentStore, StoreError

MAX_LINES = 100
FIRST_ROUND_S = 120
NO_DRAFT = ("this Orchestra cannot mark it draft: it records as an ordinary instrument (discovery reads it now) and is not "
            "removed on a timer; keep or remove it on the owner's word")
SIMULATED = "simulated = true, not draft: discovery reads its series like any other, and the Supervisor removes it unless kept"


def supports_draft() -> bool:
    """Whether the installed Orchestra's ``[[collect]]``/``[[scrape]]`` entries take ``draft`` and ``expires``."""
    return intended_api.draft_flag_supported()


def _announce(store: AgentStore, section: str, what: str, reason: str) -> None:
    store.events.emit(INSTRUMENTS_CHANGED, section=section, change=what, reason=reason, commit=None, target=OVERLAY,
                      applies="orchestra run reads it within a second", by=MACHINE, src=MACHINE)   # the host's Events has no source


def scripts_dir(project: Project) -> Path:
    return project.state_dir / "instruments"


def drafts(store: AgentStore) -> dict:
    return dict(store.agent_state(MACHINE).get("drafts") or {})


def _update(store: AgentStore, fn) -> dict:
    """The drafts record, changed by ``fn`` under the runtime lock (the MCP server and the host both write it)."""
    def change_it(d):
        d = dict(d or {})
        fn(d)
        return d
    return store.update_agent_state(MACHINE, "drafts", change_it)


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


def _set_option(project: Project, store: AgentStore, part: str, eid: str, key: str, prov: dict, chosen: bool | None = None,
                **fields) -> dict:
    """Record ``fields`` on option ``key``; ``chosen`` True marks it chosen, False clears ``chosen``
    when it names this option, None leaves it."""
    m = options_of(project, part, eid)
    for o in m["options"]:
        if o["key"] == key:
            o.update(fields)
    was = m.get("chosen")
    now = key if chosen else (None if chosen is False and was == key else was)
    return apply(project, store, [{"op": "record_measurement", "part": part, "id": eid, "options": m["options"], "chosen": now}],
                 prov)


def _entry_change(project: Project, section: str, entry: dict | None, name: str, reason: str) -> dict:
    if entry is None:
        return change(project, lambda ed, kw: ed.remove_entry(section, name, **kw), lambda c: f"removed {name} from [[{section}]]",
                      reason, OVERLAY)
    return change(project, lambda ed, kw: ed.add_entry(section, entry, **kw),
                  lambda c: f"{'added' if c.what.startswith('added') else 'changed'} the instrument {name} ([[{section}]], overlay)",
                  reason, OVERLAY)


def _entry(project: Project, section: str, name: str) -> dict | None:
    project.reload_config()
    return next((dict(e) for e in project.orchestra.config.raw.get(section) or [] if isinstance(e, dict) and e.get("name") == name),
                None)


def _when(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M:%S")


def _first_points(project: Project, name: str) -> list:
    from orchestra.metrics import for_project
    s = for_project(project.orchestra)
    try:
        dump = s.dump("", {"project": project.key, "instrument": name})
    finally:
        s.close()
    pts = sorted(p for _, _, ps in dump for p in ps)
    return [[round(t, 1), v] for t, v in pts[:5]]


def _what_runs(project: Project, store: AgentStore, part: str, eid: str, key: str, option: dict, a: dict) -> tuple[str, str, str, str]:
    """(kind, script, command, url) for the option: only what it proposed; refused otherwise."""
    if option.get("kind") == "existing_series":
        raise StoreError(f"option {key} is a series already recorded: nothing is added for it (discovery relates it on its own)")
    if option.get("clearance") == NEEDS_APPROVAL:
        raise StoreError(f"option {key} reads a company system not cleared for the model (needs approval); nothing reads or "
                         f"previews it {UNTIL}")
    mine = {k: option[k] for k in ("url", "command") if option.get(k)}      # what the layer's option names
    script, command, url = a.get("script") or "", (a.get("command") or "").strip(), (a.get("url") or "").strip()
    for given, what in ((url, "url"), (command, "command")):
        if given and given != mine.get(what):
            raise StoreError(f"option {key} proposed {what} {mine.get(what)!r}: add_instrument runs only what the option "
                             f"proposed, not {given!r} (propose_measurements again to change it)")
    url, command = mine.get("url") or "", mine.get("command") or ""
    kind = "scrape" if url else "collect"
    if kind == "collect" and bool(script.strip()) == bool(command):
        raise StoreError("a [[collect]] instrument takes a script (written to the state directory) or the command its option "
                         "proposed, not both")
    no_secret(command, url)
    if script:
        check_script(script)
    if named := company_named(script, command):
        raise StoreError(f"it names {', '.join(named)}, a company system: that needs the owner's approval, {UNTIL}; nothing "
                         "was run")
    if why := far(project, f"{script}\n{command}\n{url}"):
        raise StoreError(f"{'; '.join(why)}: that needs the owner's approval, {UNTIL}; nothing was run")
    return kind, script, command, url


def _taken(project: Project, store: AgentStore, name: str) -> str | None:
    """What already has the instrument's name: an entry in the repository or the overlay, or a draft."""
    for section in ("collect", "scrape", "sources"):
        if _entry(project, section, name) is not None:          # its values unshown: an owner's command may hold anything
            return f"the [[{section}]] entry {name!r} (in orchestra.toml or the owner's overlay)"
    return f"the draft instrument {name!r}" if name in drafts(store) else None


def add_instrument(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    part, eid, declared = resolve(project, a.get("element"))
    if not declared or part not in ("stock", "effect"):
        raise StoreError(f"{eid} is not a declared stock or effect")
    key = (a.get("option") or "").strip()
    option = next((o for o in options_of(project, part, eid)["options"] if o["key"] == key), None)
    if option is None:
        raise StoreError(f"{eid} has no option {key!r}: propose_measurements first, then add the one you pick")
    name = (a.get("name") or "").strip()
    if not NAME.match(name):
        raise StoreError("the instrument's name is letters, digits, '.', '_' and '-' (features.acceptance)")
    try:
        every = a.get("every") or "60s"
        parse_duration(every, 60.0)
    except ConfigError as e:
        raise StoreError(f"every: {e}") from None
    if taken := _taken(project, store, name):
        raise StoreError(f"{taken} already has the name {name}: choose another name; nothing was run or written")
    kind, script, command, url = _what_runs(project, store, part, eid, key, option, a)
    path = None
    if script:
        trial = project.state_dir / "trials" / f"{name}.sh"
        trial.parent.mkdir(parents=True, exist_ok=True)
        trial.write_text(script if script.startswith("#!") else "#!/bin/sh\n" + script, encoding="utf-8")
        trial.chmod(0o755)
        command = str(trial)
    tried = instruments.try_collector(project, {"command": command} if kind == "collect" else {"url": url})
    if script and (not tried.get("works") or a.get("where") == "repo"):
        Path(command).unlink(missing_ok=True)
    if not tried.get("works"):
        raise StoreError("the trial round failed, so nothing was written: " + "; ".join(
            str(tried.get(k)) for k in ("error", "bad_lines", "refused", "stderr") if tried.get(k)))
    if a.get("where") == "repo":
        return _to_repo(store, eid, key, name, every, script or command or url, tried, prov)
    if script:
        path = scripts_dir(project) / f"{name}.sh"
        path.parent.mkdir(parents=True, exist_ok=True)
        Path(command).replace(path)
        command = str(path)
    section = kind
    now = time.time()
    entry = {"name": name, **({"command": command} if kind == "collect" else {"url": url}), "every": every}
    simulated = bool(option.get("simulated") or a.get("simulated"))
    if simulated:
        entry["simulated"] = True
    flagged = supports_draft() and not simulated            # a simulated series is read like any other (§2.2)
    until = now + project.config.draft_expiry_s if flagged or simulated else None
    if flagged:
        entry.update(draft=True, expires=datetime.fromtimestamp(until, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    try:
        done = _entry_change(project, section, entry, name, prov["reason"])
    except StoreError:
        if path:
            path.unlink(missing_ok=True)
        raise
    _announce(store, section, done["announce"], prov["reason"])
    rec = {"element": f"{part}:{eid}", "option": key, "section": section, "script": str(path) if path else None,
           "at": now, "until": until, "kept": False, "simulated": bool(entry.get("simulated")), "flagged": flagged,
           "series": sorted({s_["name"] for s_ in tried.get("series") or []})}
    _update(store, lambda d: d.__setitem__(name, rec))
    replaced = []
    if not entry.get("simulated"):                  # a real option records: the placeholder goes
        for other, r in drafts(store).items():
            if other != name and r["element"] == f"{part}:{eid}" and r.get("simulated"):
                remove(project, store, other, f"a real option ({key}) records {eid} now; the simulated placeholder goes", prov)
                replaced.append(other)
    first, waited = [], False
    if runtime.launcher(runtime.read(project.orchestra)) is not None:
        waited = True
        instruments.read_instrument(project, {"name": name, "wait_s": min(int(a.get("wait_s") or FIRST_ROUND_S), FIRST_ROUND_S)})
        first = _first_points(project, name)
    where = f"written by the Supervisor · {path}" if path else f"the command {command or url}"
    hours = round(project.config.draft_expiry_s / 3600)
    lines: list[str | None] = [
        (f"instrument {name} records {eid} (option {key}) every {every}; " + (
            f"the Supervisor removes it in {hours} h unless the owner keeps it (keep_instrument)" if until else
            "it stays until the owner keeps it (keep_instrument) or says to remove it"))]
    try:
        result = _set_option(project, store, part, eid, key, prov, instrument=name, first_points=first,
                             provenance=f"{where} · entry in the owner's overlay · recording · "
                                        + ("draft until kept" if flagged else "simulated" if simulated else "pending the owner's word"))
    except StoreError as e:                          # the instrument records: say so, and what is missing
        result = {"changes": []}
        lines.append(f"its option {key} was not updated in the declared layer ({unapplied(e)}): read_design shows the "
                     "instrument; the option's record follows at its keep")
    lines += [(f"marked draft (draft = true, expires {entry['expires']}): discovery leaves its series out until it is kept"
               if flagged else SIMULATED if simulated else NO_DRAFT),
              f"script: {path}" if path else f"{'url' if url else 'command'}: {url or command}",
              ("first points: " + ", ".join(f"{v:g} at {_when(t)}" for t, v in first[-3:]) if first else "no point yet: "
               + ("its first round has not ended" if waited else "no `orchestra run` runs this project now")),
              page_line(project, "the Dynamics page shows it in the element's drawer: recording · draft, the sparkline, the "
                                 "script and its entry")]
    if replaced:
        lines.append(f"removed the simulated placeholder{'' if len(replaced) == 1 else 's'} {', '.join(replaced)}")
    return answer(project, result, lines, instrument=name, script=str(path) if path else None, first_points=first,
                  trial=tried.get("series"), draft=flagged)


def remove(project: Project, store: AgentStore, name: str, reason: str, prov: dict | None = None) -> None:
    """Take a draft instrument out: its entry and its script, as one act, then its option, then the record."""
    rec = drafts(store).get(name)
    if rec is None:
        return
    if _entry(project, rec["section"], name) is not None:
        done = _entry_change(project, rec["section"], None, name, reason)
        _announce(store, rec["section"], done.get("announce") or f"removed {name} from [[{rec['section']}]]", reason)
    if rec.get("script"):
        Path(rec["script"]).unlink(missing_ok=True)
    part, _, eid = rec["element"].partition(":")
    if prov is not None and any(o["key"] == rec["option"] for o in options_of(project, part, eid)["options"]):
        _set_option(project, store, part, eid, rec["option"], prov, chosen=False, instrument=None,
                    provenance=f"removed: {reason}")
    _update(store, lambda d: d.pop(name, None) and None)


def remove_for(project: Project, store: AgentStore, element: str, reason: str, prov: dict | None = None) -> tuple[list[str], list[str]]:
    """A discarded element's unkept drafts and simulated placeholders, each entry and script
    removed; (the names removed, the names the owner kept, left in place)."""
    mine = {n: r for n, r in drafts(store).items() if r.get("element") == element}
    gone = [n for n, r in mine.items() if not r.get("kept") or r.get("simulated")]
    for n in gone:
        remove(project, store, n, reason, prov)
    return gone, [n for n in mine if n not in gone]


def unapplied(e: BaseException) -> str:
    """A layer's refusal without its "nothing was applied": said where something else was written."""
    return str(e).replace("; nothing was applied", "").replace(": nothing was applied", "")


def _backed(project: Project, rec: dict, prov: dict) -> str | None:
    """The owner's backing of a keep, in words: their quoted words, or their expect mark on one of
    the instrument's series or on the stock its element is linked to; None without one."""
    if prov.get("author") == OWNER:
        return "the owner's words"
    lay = intended_api.current(project)
    part, _, eid = rec["element"].partition(":")
    found_as = ((lay.get(f"{part}s") or {}).get(eid) or {}).get("found_as")
    for key, mk in ((lay.get("marks") or {}).get("expected") or {}).items():
        if (mk.get("provenance") or {}).get("author") == OWNER and (key.split("{", 1)[0] in (rec.get("series") or [])
                                                                   or key == found_as):
            return f"the owner's expect mark on {key}"
    return None


def _open_prediction(store: AgentStore, now: float | None = None) -> bool:
    """Whether any agent here has a prediction not judged yet and still within its horizon."""
    now = now or time.time()
    return any(not rec.get("judged") and (rec.get("at") or 0) + float(rec.get("horizon_s") or 0) > now
               for st in store.runtime()["agent_state"].values() for rec in st.get("predictions") or [])


def _keep(project: Project, store: AgentStore, name: str, rec: dict, prov: dict, backing: str) -> tuple[dict, list[str]]:
    """Keep a draft: the flag off its entry (config writes), the record kept, then the option
    chosen; what was done and what to say. Raises only when the configuration could not be written."""
    entry = _entry(project, rec["section"], name)
    if entry is None:
        raise StoreError(f"the instrument {name} has no [[{rec['section']}]] entry any more; add it again (add_instrument)")
    said = []
    if "draft" in entry or "expires" in entry:
        # Orchestra's editor sets an entry's fields and never drops one, except that it puts an overlay entry back whole when
        # the overlay disables it: so the entry is disabled, then given again without the flag (two writes back to back, the
        # entry restored as it was if the second is refused)
        kept = {k: v for k, v in entry.items() if k not in ("draft", "expires", "enabled")}
        _entry_change(project, rec["section"], {"name": name, "enabled": False}, name, prov["reason"])
        try:
            _entry_change(project, rec["section"], kept, name, prov["reason"])
        except StoreError:
            _entry_change(project, rec["section"], entry, name, f"put back as it was: {prov['reason']}")
            raise
        _announce(store, rec["section"], f"kept {name}: draft and expires taken off its entry", prov["reason"])
        said.append("draft = true and expires taken off its entry: discovery reads its series from now on")
        if _open_prediction(store):
            said.append("two configuration changes were recorded (two writes), so a prediction open now is judged interrupted "
                        "and renewed")
    _update(store, lambda d: d[name].update(kept=True, until=None, kept_at=time.time(), kept_by=backing, chosen_pending=True))
    part, _, eid = rec["element"].partition(":")
    try:
        done = _set_option(project, store, part, eid, rec["option"], prov, chosen=True, provenance=(
            f"written by the Supervisor · {rec['script']} · kept" if rec.get("script") else "kept"))
    except StoreError as e:
        return {"changes": []}, [(f"instrument {name} kept ({backing}): it no longer expires; option {rec['option']} is not "
                                  f"marked chosen yet: {unapplied(e)} (the host tries again within minutes)"), *said]
    _update(store, lambda d: d[name].pop("chosen_pending", None) and None)
    return done, [f"instrument {name} kept ({backing}): it no longer expires, and option {rec['option']} is chosen", *said]


def keep_instrument(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    name = (a.get("name") or "").strip()
    rec = drafts(store).get(name)
    if rec is None:
        raise StoreError(f"no draft instrument {name!r}; read_design lists them")
    if (backing := _backed(project, rec, prov)) is None:
        raise StoreError(f"keeping {name} is the owner's to say: quote their words, or wait for their expect mark on its series "
                         f"({', '.join(rec.get('series') or []) or 'none read yet'}) on the Dynamics page")
    from .design_machines import rewrite_readers
    done, said = _keep(project, store, name, rec, prov, backing)
    eid = rec["element"].partition(":")[2]
    if again := rewrite_readers(project, store, eid):
        said.append(f"the records of {', '.join(again)}, which read {eid}, are written again with its series")
    return answer(project, done, [*said, page_line(project, "the Dynamics page shows the option \"chosen\"")], instrument=name)


def retry_chosen(project: Project, store: AgentStore) -> list[str]:
    """Kept instruments whose option could not be marked chosen at their keep, marked now (the
    host's tick); what was said. Nothing is read while none waits."""
    said = []
    for name, rec in drafts(store).items():
        if not (rec.get("kept") and rec.get("chosen_pending")):
            continue
        part, _, eid = rec["element"].partition(":")
        prov = {"author": MACHINE, "source": MACHINE, "reason": f"the owner kept {name} ({rec.get('kept_by') or 'kept'})"}
        _set_option(project, store, part, eid, rec["option"], prov, chosen=True, provenance=(
            f"written by the Supervisor · {rec['script']} · kept" if rec.get("script") else "kept"))
        _update(store, lambda d, n=name: d[n].pop("chosen_pending", None) and None)
        said.append(f"instrument {name}: option {rec['option']} marked chosen now (its keep could not mark it)")
    return said


def _to_repo(store: AgentStore, eid: str, key: str, name: str, every: str, text: str, tried: dict, prov: dict) -> dict:
    """A script that worked, handed to the Coordinator for the repository (where = repo)."""
    first = ", ".join(f"{x['name']} {x.get('value')}" for x in tried.get("series") or [])
    req = handoff.hand_to_coordinator(store, {"requirements": (
        f"Add the measurement script for {eid} (option {key}) to the repository at .orchestra/instruments/{name}.sh, with its "
        f"[[collect]] entry `name = \"{name}\"`, `command` that script, every {every}, in orchestra.toml, through a ticket, a "
        f"review and the gate.\n\nThe script, as the Supervisor tried it (its trial round read {first or 'nothing'}):\n\n{text}\n"),
        "context": prov["reason"]}, MACHINE)
    return {"ok": True, "request": req["request"], "written": False, "trial": tried.get("series"),
            "said": [(f"tried it (its round read {first or 'nothing'}), then handed it to the Coordinator as {req['request']}: "
                      f"it lands at .orchestra/instruments/{name}.sh through a ticket, a review and the gate; nothing was "
                      "written here")]}


def expire(project: Project, store: AgentStore, now: float | None = None, log=lambda _: None) -> list[str]:
    """At its expiry, keep each unkept draft the owner's expect mark backs and remove every other
    (the host's tick), each on its own, saying in ``log`` what was kept and why one could not be
    removed; the names removed."""
    now = now or time.time()
    gone = []
    for name, rec in drafts(store).items():
        if rec.get("kept") or not rec.get("until") or now < rec["until"]:
            continue
        try:
            if backing := _backed(project, rec, {"author": MACHINE}):      # the owner's expect click keeps it (§2.2)
                prov = {"author": MACHINE, "source": MACHINE, "reason": f"kept at its expiry: {backing} backs it"}
                _, said = _keep(project, store, name, rec, prov, backing)
                log(f"draft instrument {name}: " + "; ".join(said))
                continue
            prov = {"author": MACHINE, "source": MACHINE, "reason": "a draft instrument the owner did not keep expired"}
            remove(project, store, name, prov["reason"], prov)
            gone.append(name)
        except Exception as e:  # noqa: BLE001 - one draft that cannot be removed never keeps the rest
            log(f"draft instrument {name}: not removed at its expiry: {e}")
    return gone


COMMANDS = {"add_instrument": add_instrument, "keep_instrument": keep_instrument}
