"""Connecting an outside system (Orchestra's guide, ``orchestra/guide.md``, "Connecting another system: the strategies" on; task I3): the Supervisor's
``integrate_system`` and ``approve_integration``, in the ``design`` group (``design.handle``: the
Supervisor's alone, each with its reason and provenance).

``integrate_system`` takes the system's name and what the owner wants from it, and the strategy
(the guide's "Connecting another system: the strategies"; ``integrate_kits.choose`` when none is given):

- **poller** (events and series) or **collect** (series alone): the bridges plugin's kit (a
  library: the owner, 2026-11-01) is scaffolded into the owner's repository
  (``.orchestra/machines/<name>/``, live from there: a coding tool may edit it; its files one commit
  on ``orchestra/supervisor`` for its pull request, the live copy ignored in this checkout only,
  ``integrate_branch``), or into the state directory (``<state>/machines/<name>/``) only
  when the owner said the repository must stay clean (``root: state``); its test is run against the
  kit's fake, and the owner's settings are filled into its ``poller.toml`` (after the test, which
  reads the fake's own world; their words stay in the integration's record). When the test fails,
  or the system has no template of its own (the generic ``http`` kit), the script is handed to the
  Coordinator as work (``integrate_handoff``: moved out of the checkout first, so the worker
  commits it on its ticket and the ticket's review and gate land it) instead of stopping there; a
  failed one is called again by the same name once the worker's test passes (its test is run
  again where the script is now, nothing scaffolded again). A poller's system is declared on the
  Machines page as a machine managed elsewhere (``managed: external``), its actor source
  ``bot.<system>`` joined to it (``alias``), and each actor its poller relays once heard
  (``integrate_heard``), with its expected effect when the owner named the
  stock it fills: the poller is a relay and never a machine (Orchestra's rule 2). Without the kits
  nothing is written and the answer proposes the guide's no-kit route ("Without the kits");
- **source**: a ``[[sources]]`` entry for a Prometheus-API store, planned (no code).

Nothing reads the real system yet: the program's ``poller.toml`` stays ``clearance = "needs
approval"`` and what runs it is only planned (``integrate_schedule``). The answer carries the
question the Supervisor puts to the owner in the chat (``ask_owner``): their approval, and where the
token is (a variable's NAME or a file's path), never the token.

``approve_integration`` is the owner's alone (a quote of their words this turn, ``author: owner``):
it records the clearance, the data class and the credentials by reference in ``poller.toml``
(``token_env`` or ``token_file``, and ``user_env``/``user_file`` for an HTTP Basic account such as
Jira Cloud's email; ``integrate_kits.credentials``), or the store's ``auth.bearer_env`` or
``bearer_file``, then writes the plan to the overlay, so the program then reads the real system. The record of each integration is the Supervisor's state (``integrations``)."""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from . import design_dynamics, design_machines, installed, integrate_heard
from . import integrate_address as address
from . import integrate_branch as branch
from . import integrate_handoff as handoff
from . import integrate_kits as kits
from . import integrate_schedule as schedule
from .config import Project
from .design import MACHINE, OWNER, answer, apply, said_in_session, turn_of
from .secret_shapes import redact
from .store import AgentStore, StoreError
from .tasking_text import gate

KEY = "integrations"
NEEDS = "needs approval"
DATA_CLASS = "metadata only"


def integrations(store: AgentStore) -> dict:
    return dict(store.agent_state(MACHINE).get(KEY) or {})


def _save(store: AgentStore, name: str, rec: dict) -> None:
    store.update_agent_state(MACHINE, KEY, lambda d: {**(d or {}), name: rec})


def _system(a: dict) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (a.get("system") or "").lower()).strip("_")
    if not design_machines.ID.match(s):
        raise StoreError("name the system (jira, slack, pagerduty, chronosphere, jenkins): lower case letters, digits and _")
    return s


# what the owner may call a system in words, beside its name and title
SAID_AS = {"github": ("github", "git hub", "ghe", "gh enterprise"), "pagerduty": ("pagerduty", "pager duty"),
           "victoriametrics": ("victoriametrics", "victoria metrics")}
CREDENTIAL_ARGS = ("token_env", "token_file", "user_env", "user_file")


def _named_by_owner(store: AgentStore, system: str) -> None:
    """The owner named the system in words, in a message of this session (SPEC §10, the bounds:
    bootstrap run 2 scaffolded a GitHub poller before the owner had said GitHub). Refused otherwise:
    the Supervisor proposes it in one line and waits for their word; nothing is scaffolded."""
    words = {system, system.replace("_", " "), kits.title(system).lower(), *SAID_AS.get(system, ())}
    msgs = [r.get("message") or "" for r in said_in_session(store)] + [turn_of(store).get("message") or ""]
    if any(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", m.lower()) for w in words for m in msgs):
        return
    raise StoreError(f"the owner has not named {kits.title(system)} in their words this session: never scaffold, declare or "
                     f"configure a system they have not named. Propose it in one line (\"{kits.title(system)} would show …; "
                     "shall I connect it?\") and wait for their word; nothing was scaffolded")


def _retry(store: AgentStore, name: str) -> None:
    """An integration whose test could not run (no pytest) is begun again under its name: its
    scaffold and record go first (nothing else was written for it)."""
    rec = integrations(store).get(name)
    if rec and rec.get("state") == "test not run":
        if rec.get("dir") and not rec.get("adopted"):      # a collector the project already had is never removed
            shutil.rmtree(rec["dir"], ignore_errors=True)
        store.update_agent_state(MACHINE, KEY, lambda d: {k: v for k, v in (d or {}).items() if k != name})


def _taken(project: Project, store: AgentStore, name: str) -> str | None:
    if name in integrations(store):
        return f"the integration {name} (approve_integration, or another name)"
    raw = project.orchestra.config.raw
    for section in ("machines", "collect", "scrape", "sources"):
        if any(isinstance(e, dict) and e.get("name") == name for e in raw.get(section) or []):
            return f"a [[{section}]] entry"
    return None


def _adoptable(project: Project, store: AgentStore, system: str, strategy: str, a: dict, given: str) -> dict | None:
    """A collector already in the project (shipped with it, or scaffolded by hand) from the kit this
    system takes, which is configured instead of scaffolding a second beside it: the one ``name``
    names, else the only one of that kit. Recognised from the files on disk as the bridges list them
    (``integrate_kits.shipped``: each one's kit from its own ``poller.toml``), never from what a
    machine says of itself. One an integration already has is not offered again. A store whose
    strategy was left out is measured by such a collector (``collect``) rather than planned as a
    ``[[sources]]`` entry."""
    if strategy == "source":
        if a.get("strategy"):
            return None
        strategy = "collect"
    kit = a.get("kit") or kits.kit_for(system, strategy)
    recs = integrations(store)
    known = {r.get(k) for r in recs.values() for k in ("dir", "lands")}
    found = [c for c in kits.shipped(project) if c["name"] not in recs and c["dir"] not in known]
    if given:
        c = next((c for c in found if c["name"] == given), None)
        if c and c["kit"] != kit:
            raise StoreError(f"{given} is already in the project at {c['dir']}, from the {c['kit']} kit, not {kit}: name "
                             f"another collector, or leave kit out to configure that one; nothing was written")
        return {**c, "strategy": strategy} if c else None
    mine = [c for c in found if c["kit"] == kit]
    if len(mine) > 1:
        raise StoreError(f"the project already has {len(mine)} collectors from the {kit} kit ({', '.join(c['name'] for c in mine)}"
                         f", in {Path(mine[0]['dir']).parent}): call integrate_system once for each the owner wants, naming "
                         "it (name) with its own settings, or name a new collector to scaffold another beside them; "
                         "nothing was written")
    return {**mine[0], "strategy": strategy} if mine else None


def _no_kits(system: str) -> dict:
    return {"ok": True, "integrated": False, "said": [
        (f"the bridges plugin's kits (orchestra_bridges.kits) are not installed in Orchestra's Python, so nothing was "
        f"scaffolded, declared or written for {kits.title(system)}"),
        "the routes (" + kits.INTEGRATING + ", \"Without the kits: `minikit.py` and a one-file poller\"): with the kits, the owner's coding tool installs the bridges plugin into "
        f"Orchestra's environment ({kits.REINSTALL}) and you call integrate_system again; without them, the program "
        "is the one-file poller of §5.2 beside minikit.py (§4, \"Without the kits\"), with its own fake and test: that is code, so " + gate(
            "[[tasking]]hand it to the Coordinator (hand_to_coordinator) with the system, what the owner wants, and §3's rules (the "
            "token by its variable's name, clearance before any read, metadata only)[[/tasking]][[workflows]]it is a worker's, as a "
            "Workflows item on a branch (skill code-work)[[/workflows]][[no-workflows]]it is not handed on here (no task system "
            "or Workflows plugin): say so, and that installing Workflows is the one step[[/no-workflows]]"),
        "a store with a Prometheus API needs no kit: strategy source"],
        "next": f"tell the owner the kits are missing and which route you propose for {kits.title(system)}"}


def _declare(project: Project, system: str, name: str, every: str, emits: list[str], prov: dict,
             store: AgentStore | None = None) -> tuple[dict, list]:
    """The system as a machine managed elsewhere, its actor source joined to it; never the poller."""
    mid, actor = system, f"bot.{system}"
    m = {"id": mid, "title": kits.title(system), "kind": "programmatic", "managed": "external",
         "description": f"{kits.title(system)} itself: what its people and bots do there, relayed by the poller {name}",
         "tags": ["integration"], "engagement": {"mode": "continuous"}, "receives": [], "emits": emits, "reads": [],
         "spec": {"source": actor, "receive": f"polled every {every} by {name} (a relay, not a machine)"}}
    design_machines.check_machine(m)
    known = mid in (design_machines.intended_api.current(project).get("machines") or {})
    ops = ([] if known else [{"op": "propose_machine", **m, "guessed": []}]) + [{"op": "alias", "machine": mid, "observed": actor}]
    done = apply(project, ops, prov)
    joined = integrate_heard.join(project, store, system) if store is not None else []
    who = "added by you (from your message)" if prov["author"] == OWNER else "proposed by the Supervisor"
    lines = [(f"machine {mid} ({m['title']}) {'already declared; ' if known else 'declared, programmatic, managed elsewhere, ' + who + '; '}"
             f"heard as the actors its poller relays: {actor}, person.{system}.<account> and bot.{system}.<account>, each "
             "joined to it as it is heard (the poller never publishes as itself)"
             + (f"; heard already as {', '.join(joined)}" if joined else "")),
             ("the Machines page draws it dashed, \"unconfirmed\", until the owner confirms it, and \"never heard\" only "
              "while none of those actors is on the bus")]
    return done, lines


def _stock(project: Project, store: AgentStore, a: dict, prov: dict) -> tuple[str | None, list, list[str]]:
    """The stock the owner named (its id, what was said, the change ids it took: none when it was declared already)."""
    name = (a.get("stock") or "").strip()
    if not name:
        return None, [], []
    stocks = design_machines.intended_api.current(project).get("stocks") or {}
    sid = next((k for k, s in stocks.items() if (s.get("name") or k).casefold() == name.casefold()), None)
    if sid:
        return sid, [f"stock {sid} ({name}) was declared already"], []
    got = design_dynamics.declare_stock(project, store, {"name": name, "unit": a.get("unit"), "description": a.get("wants")}, prov)
    return got["id"], got["said"], list(got.get("changes") or [])


def _effect(project: Project, store: AgentStore, mid: str, sid: str, prov: dict) -> tuple[list, list[str]]:
    got = design_machines.declare_expected_effect(
        project, store, {"machine": mid, "expect": [{"kind": "appears", "to": sid}], "horizon": "1d"}, prov)
    return got["said"], list(got.get("changes") or [])


def _ask(system: str, kit_env: str | None, scope: str | None = None) -> str:
    t = kits.title(system)
    parts = [(f"May I read {t}? It reads metadata only (ids, keys, accounts, states, times; never titles, text or "
             "messages), at most the configured rate, and writes nothing to it. Your yes, in your words, naming the "
             "token's scope (read-only)"),
             (f"where the token is: the NAME of the environment variable that will hold it (for example "
              f"{kit_env or system.upper() + '_TOKEN'}, set in the environment `orchestra run` starts with), or the path of a "
              "chmod 600 file outside the project's repository that no repository tracks; never the token itself, "
              "and not in this chat")]
    if system == "jira":
        parts.append("for Jira Cloud (an atlassian.net site), also where the account's email is (a variable's name or a "
                     "file's path): Cloud logs in with the email and the token together")
    if scope:
        parts.append(f"{kits.SCOPE[scope]} ({scope})")
    return "; ".join(parts) + "."


def integrate_system(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    system = _system(a)
    if not any(r.get("system") == system for r in integrations(store).values()):   # one already begun was named then
        _named_by_owner(store, system)
    named = kits.credentials(a, project.repo_root, required=False) if any(a.get(k) for k in CREDENTIAL_ARGS) else {}
    wants = redact((a.get("wants") or "").strip())
    if len(wants) < 5:
        raise StoreError("say what the owner wants from it, in a sentence (issue transitions and open counts)")
    strategy = a.get("strategy") or kits.choose(system)
    if strategy not in kits.STRATEGIES:
        raise StoreError(f"strategy is one of {', '.join(kits.STRATEGIES)} (Orchestra's guide, \"Connecting another system: the strategies\"; the bridges have no readers "
                         "of their own: a collector is a script built from a kit)")
    root = (a.get("root") or "repository").strip()
    if root not in kits.ROOTS:
        raise StoreError("root is repository (the default: .orchestra/machines/<name>/ in the owner's repository) or state "
                         "(only when the owner said the repository must stay clean)")
    settings = kits.check_settings(a.get("settings") or {})
    base_url = (a.get("base_url") or "").strip() or None
    if base_url and not re.match(r"^https?://[^\s/]+", base_url):
        raise StoreError("base_url is the API's root, an http(s) URL")
    if base_url and not address.given(store, base_url):          # never a guess (bootstrap run 3: slack.com)
        raise StoreError(address.refused(kits.title(system), base_url))
    given = (a.get("name") or "").strip()
    adopt = _adoptable(project, store, system, strategy, a, given) if kits.installed() else None
    if adopt:
        strategy = adopt["strategy"]
    name = given or (adopt or {}).get("name") or (system if strategy == "source" else f"{system}_poller")
    relays = not adopt or kits.has_poller(adopt["kit"])  # a measurement alone may share the store's name: no machine is declared
    if not design_machines.ID.match(name) or (strategy in ("poller", "collect") and name == system and relays):
        raise StoreError(f"{name!r}: the poller's name is lower case letters, digits and _ (2 to 41), and never the system's "
                         f"own name (the system is the machine; the poller only relays it): {system}_poller")
    _retry(store, name)
    if (old := integrations(store).get(name)) and old.get("state") == "test failed":
        return _retest(project, store, a, prov, old, settings)
    if not adopt and (taken := _taken(project, store, name)):
        raise StoreError(f"{taken} already has the name {name}: nothing was written")
    rec = {"system": system, "strategy": strategy, "wants": wants, "clearance": NEEDS, "token_env": None, "at": time.time(),
           "base_url": base_url, "base_url_from": address.given(store, base_url), "root": root}
    if named:                                    # where the owner said the token is, checked before anything is scaffolded
        rec["named"] = {k: v for k, v in named.items() if k != "cautions"}
        rec["cautions"] = named.get("cautions") or []
    if strategy == "source":
        return _plan_only(project, store, name, rec, settings, a, prov)
    if not kits.installed():
        return _no_kits(system)
    if adopt:
        return _adopt(project, store, a, prov, rec, adopt, settings)
    kit = a.get("kit") or kits.kit_for(system, strategy)
    if kit not in kits.kits():
        raise StoreError(f"no kit {kit!r}: the kits are {', '.join(kits.kits())}")
    design_machines.intended_api.current(project)        # the declared layer answers (its host runs) before anything is written
    handoff.leftover(project, name)                      # a killed call's scaffold, never recorded
    target = kits.scaffold(project, kit, name, root)
    rec.update(name=name, kit=kit, dir=str(target), settings=settings)
    try:
        return _tested(project, store, a, prov, rec, kits.run_test(target, name), settings, first=True)
    except BaseException:
        if name not in integrations(store):               # nothing recorded: nothing left behind
            shutil.rmtree(rec["dir"], ignore_errors=True)
        raise


def _adopt(project: Project, store: AgentStore, a: dict, prov: dict, rec: dict, found: dict, settings: dict) -> dict:
    """A collector the project already has, configured in place: its own test run against the kit's
    fake, the owner's settings written into its ``poller.toml``, the system declared and its run
    planned, exactly as for a new scaffold, but nothing scaffolded, and its directory never removed
    or moved (a committed one is adapted on a ticket where it is)."""
    design_machines.intended_api.current(project)        # the declared layer answers before anything is written
    target = Path(found["dir"])
    rec.update(name=found["name"], kit=found["kit"], dir=str(target), settings=settings, adopted=True, root=found["root"],
               tracked=found["root"] == "repository" and handoff.tracked(project, target))
    return _tested(project, store, a, prov, rec, kits.run_test(target, found["name"]), settings, first=True)


def _retest(project: Project, store: AgentStore, a: dict, prov: dict, rec: dict, settings: dict) -> dict:
    """An integration whose test failed, called again by its name: its test is run again where the
    script is now (the files the worker adapted, or the landed copy in the checkout), and on a pass
    it goes on as a first call would; nothing is scaffolded again."""
    name = rec["name"]
    handoff.landed(rec)
    target = Path(rec["dir"])
    if not target.is_dir():
        raise StoreError(f"{name}'s script is no longer at {target}: nothing was changed")
    test = kits.run_test(target, name)
    if test["passed"]:
        return _tested(project, store, a, prov, rec, test, settings or rec.get("settings") or {}, first=False)
    rec["test"] = test
    _save(store, name, rec)
    tail = "; ".join((test.get("tail") or [])[-3:])
    lines = [f"its test in {target} still does not pass ({test['summary']}" + (f": {tail}" if tail else "") + ")"]
    if not rec.get("request"):
        rec["request"], said = handoff.hand_on(store, rec, f"its test against the kit's fake fails ({test['summary']})", prov)
        _save(store, name, rec)
        lines += said
    lines.append(f"the Coordinator has it as {rec['request']}: call integrate_system again with the same name once the "
                 "worker's test passes")
    return {"ok": True, "integrated": False, "said": lines, "integration": name, "dir": rec["dir"], "test": test,
            "request": rec["request"]}


def _tested(project: Project, store: AgentStore, a: dict, prov: dict, rec: dict, test: dict, settings: dict, *,
            first: bool) -> dict:
    """A scaffold whose test ran: filled, then (once it passed) its system declared and its run planned."""
    system, strategy, kit, name = rec["system"], rec["strategy"], rec["kit"], rec["name"]
    target = Path(rec["dir"])
    kits.fill(target, {**({"system": system} if kit == "http" else {}), **settings}, rec["base_url"])
    mf = kits.read(target)
    every = str(mf.get("settings", {}).get("every") or "5m")
    events = strategy == "poller" and kit != "promql"
    lines: list = [(_adopted_line(rec, target) if rec.get("adopted") else
                    f"scaffolded the {kit} kit's poller {name} into {target} (poller.toml, {name}.py, test_{name}.py, "
                    "AGENTS.md); poller.toml: clearance \"needs approval\", so it reads only a fake on this machine")
                   if first else f"ran its test again in {target}",
                   f"its test against the kit's fake: {'passed' if test['passed'] else 'NOT passed'} ({test['summary']})"]
    in_file = mf.get("settings", {}).get("base_url") or None
    if not rec.get("base_url") and (came := address.given(store, in_file)):   # the file's, only when the owner said it or it is here
        rec.update(base_url=in_file, base_url_from=came)
    rec.update(test=test, settings=settings, every=every, events=events, template_url=None if rec.get("base_url") else in_file,
               scope=next((k for k in kits.SCOPE_OF.get(kit, ()) if k not in settings), None))
    if not test["ran"]:
        rec["state"] = "test not run"
        _save(store, name, rec)
        lines += [handoff.who_lands(rec),
                  ("nothing was declared or planned: the test is not failing, it could not run. pytest is not installed "
                   "in Orchestra's Python; the owner's coding tool installs it with Orchestra's dev extra, naming every "
                   f"plugin it has, as the tool is installed: {kits.REINSTALL}. Then call integrate_system again, the "
                   "same way, or tell the owner")]
        return {"ok": True, "integrated": False, "said": lines, "integration": name, "dir": rec["dir"], "test": test}
    if not test["passed"]:
        rec["state"] = "test failed"
        tail = "; ".join((test.get("tail") or [])[-3:])
        handoff.move_out(project, rec)
        _save(store, name, rec)
        rec["request"], said = handoff.hand_on(store, rec, f"its test against the kit's fake failed ({test['summary']}"
                                               + (f": {tail}" if tail else "") + ")", prov)
        _save(store, name, rec)
        lines += [handoff.who_lands(rec)] + said + [
            ("nothing was declared or planned yet: call integrate_system again with the same name once the worker's test "
             "passes, or tell the owner")]
        return {"ok": True, "integrated": False, "said": lines, "integration": name, "dir": rec["dir"], "test": test,
                "request": rec["request"], "next": "Tell the owner the script is scaffolded at the path above, its test "
                "failed, and the Coordinator has it as work; name no ticket yet."}
    changes, sid, mid = _declarations(project, store, a, prov, rec, target, every, events, lines, first)
    home = Path(rec.get("lands") or target)               # where it runs: the landed copy, once the Coordinator's ticket lands it
    rec["steps"] = schedule.poller(name, home, every, events=events)
    rec.update(state=NEEDS, machine=mid, stock=sid)
    hand = not kits.templated(kit) and not rec.get("request") and not rec.get("adopted")
    if hand:
        handoff.move_out(project, rec)
    _save(store, name, rec)
    if hand:                                              # only now: nothing after it can fail and remove the scaffold
        rec["request"], said = handoff.hand_on(store, rec, f"the kits have no template for {kits.title(system)}, so the "
                                               "script is the generic http kit's guess at a JSON API", prov)
        _save(store, name, rec)
        lines += said
    if carried := branch.carry(project, store, rec, f"scaffold the collector {name} ({kit} kit) for {kits.title(system)}",
                               prov["reason"], prov.get("quote")):
        _save(store, name, rec)
        lines += carried
    lines.insert(2, handoff.who_lands(rec))
    extra = {**({"request": rec["request"]} if rec.get("request") else {}), **({"adopted": True} if rec.get("adopted") else {})}
    return _report(project, name, rec, {"changes": [{"change": c} for c in changes]}, lines,
                   mf.get("settings", {}).get("token_env"), test=test, dir=rec["dir"], **extra)


def _adopted_line(rec: dict, target: Path) -> str:
    clearance = kits.read(target).get("clearance") or NEEDS
    return (f"configured {rec['name']}, the {rec['kit']} kit's collector already in the project at {target} (its "
            f"{kits.settings_file(target).name} says kit {rec['kit']}), in place: nothing was scaffolded beside it; its "
            f"{kits.settings_file(target).name} says clearance \"{clearance}\""
            + (", so it reads only a fake on this machine until approve_integration" if clearance != "approved" else
               ": approve_integration records the owner's own yes and where the token is"))


def _declarations(project: Project, store: AgentStore, a: dict, prov: dict, rec: dict, target: Path, every: str,
                  events: bool, lines: list, first: bool) -> tuple[list[str], str | None, str | None]:
    """The system as a machine (when the poller relays its events), the stock the owner named and the
    machine's expected effect on it: the change ids, the stock and the machine. A refusal part way
    names what was applied before it, each with its change ids."""
    system, name = rec["system"], rec["name"]
    changes: list[str] = []
    applied: list[str] = []
    sid = mid = None
    try:
        if events:
            done, said = _declare(project, system, name, every, kits.publishes(target), prov, store)
            ids = [c["change"] for c in done.get("changes") or [] if c.get("change")]
            changes += ids
            lines += said
            mid = system
            if ids:
                applied.append(f"machine {mid} declared, heard as bot.{system} ({', '.join(ids)})")
        sid, said, ids = _stock(project, store, a, prov)
        lines += said
        changes += ids
        if ids:
            applied.append(f"stock {sid} ({(a.get('stock') or '').strip()}) declared ({', '.join(ids)})")
        if sid and mid:
            said, ids = _effect(project, store, mid, sid, prov)
            lines += said
            changes += ids
    except StoreError as e:
        if not applied:
            raise
        kept = f"The poller {name} was not kept" if first else f"The poller {name} stays as it was ({rec.get('state')})"
        raise StoreError(f"{e}. Applied before it in this call, and kept in the declared layer: {'; '.join(applied)}; "
                         f"integrate_system again finds them, discard takes them back. {kept} and nothing was planned") from None
    return changes, sid, mid


def _plan_only(project: Project, store: AgentStore, name: str, rec: dict, settings: dict, a: dict, prov: dict) -> dict:
    system = rec["system"]
    if not rec["base_url"]:
        raise StoreError("base_url: the store's address is needed to plan it")
    entry = {"name": name, "url": rec["base_url"], "prefix": settings.pop("prefix", system), **settings}
    if not entry.get("match"):
        raise StoreError("settings.match: the PromQL selectors to read (Orchestra's guide, \"The systems\": never a whole tenant)")
    rec["steps"] = schedule.source(entry)
    lines: list = [f"planned the [[sources]] entry {name} for {rec['base_url']}: read in place, nothing copied, no code"]
    sid, said, ids = _stock(project, store, a, prov)
    rec.update(name=name, state="needs approval", stock=sid)
    _save(store, name, rec)
    return _report(project, name, rec, {"changes": [{"change": c} for c in ids]}, lines + said, None)


def _report(project: Project, name: str, rec: dict, done: dict, lines: list, kit_env: str | None, **extra) -> dict:
    planned = [schedule.describe(s) for s in rec["steps"]]
    missing_url = kits.placeholder(rec.get("base_url")) or not rec.get("base_url_from")
    lines.append(f"planned, written to the owner's overlay once approved: {'; '.join(planned)}")
    if rec.get("named"):
        lines.append("where the owner said the credentials are, checked (no commit can carry them), recorded for the approval: "
                     + ", ".join(f"{k} {v}" for k, v in rec["named"].items()))
        lines += rec.get("cautions") or []
    ask = _ask(rec["system"], kit_env, rec.get("scope"))
    if missing_url:
        ask += " " + address.ask(kits.title(rec["system"]), rec.get("template_url"))
    lines.append("waiting for the owner's approval and where the token is, by name or path (approve_integration)")
    return answer(project, done, lines, integration=name, clearance=NEEDS, planned=planned, ask_owner=ask,
                  owner_must_set=[f"the approval, in their words, for {kits.title(rec['system'])}",
                                  ("where the token is: an environment variable's name in orchestra run's environment, or "
                                  "a chmod 600 file outside the project's repository that no repository tracks")]
                  + ([f"{kits.title(rec['system'])}'s base URL"] if missing_url else [])
                  + ([kits.SCOPE[rec["scope"]]] if rec.get("scope") else []),
                  next="Put ask_owner to the owner in the chat, in your words. Never ask for the token itself, and never "
                       "ask them to edit a file: you write everything once they answer.", **extra)


def approve_integration(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    name = (a.get("name") or "").strip()
    rec = integrations(store).get(name)
    if rec is None:
        known = ", ".join(integrations(store)) or "none"
        raise StoreError(f"no integration {name!r} (integrate_system first); the integrations: {known}")
    if prov["author"] != OWNER:
        raise StoreError("approving a company system is the owner's alone: quote their yes from this turn's message (quote); "
                         "nothing was changed")
    if rec.get("state") not in ("needs approval", "approved"):
        raise StoreError(f"{name} is {rec.get('state')}: only an integration whose test passed is approved")
    was = rec.get("dir")
    if not handoff.landed(rec):
        raise StoreError(f"{name}'s script is with the Coordinator ({rec.get('request')}) and has not landed at {rec['lands']} "
                         "in the owner's checkout yet: what runs is the landed script, so approve it once its ticket lands; "
                         "nothing was changed")
    if rec.get("dir") != was:                       # the landed copy: the owner's settings, as they were given
        kits.fill(Path(rec["dir"]), rec.get("settings") or {}, None)
    given = {k: a[k] for k in CREDENTIAL_ARGS if a.get(k)}
    creds = kits.credentials({**(rec.get("named") or {}), **given}, project.repo_root)    # checked again, as it is now
    cautions = creds.pop("cautions", [])
    if rec["strategy"] == "source" and set(creds) - {"token_env", "token_file"}:
        raise StoreError("a store's [[sources]] entry takes a bearer token alone (token_env or token_file), no account")
    data_class = (a.get("data_class") or DATA_CLASS).strip()
    base_url = (a.get("base_url") or "").strip() or rec.get("base_url")
    if not base_url or kits.placeholder(base_url):
        raise StoreError(f"{kits.title(rec['system'])}'s address is still the template's ({base_url or rec.get('template_url')}): "
                         f"ask the owner in one sentence: \"{address.ask(kits.title(rec['system']), rec.get('template_url'))}\"; "
                         "nothing was changed")
    came = address.given(store, base_url) or (rec.get("base_url_from") if base_url == rec.get("base_url") else None)
    if not came:                                     # never a guess: the owner's words this session, or this machine
        raise StoreError(address.refused(kits.title(rec["system"]), base_url).replace("nothing was written", "nothing was changed"))
    settings = kits.check_settings(a.get("settings") or {})
    scope = rec.get("scope")
    if scope and scope not in settings:
        raise StoreError(f"{kits.title(rec['system'])}'s scope ({scope}: {kits.SCOPE[scope]}) is still the template's: ask the "
                         f"owner for it and pass it in settings.{scope}; nothing was changed")
    named = ", ".join(f"{k} {v}" for k, v in creds.items())
    lines = list(cautions)
    if rec.get("dir"):
        target = Path(rec["dir"])
        if settings:
            kits.fill(target, settings, None)
        kits.clear(target, creds, data_class, base_url)
        lines += branch.carry(project, store, rec, f"approve the collector {name} for {kits.title(rec['system'])}",
                              prov["reason"], prov.get("quote"))
        lines.append(f"{rec['dir']}/poller.toml: clearance \"approved\", data_class {data_class!r}, {named} (by reference "
                     f"only), base_url {base_url}" + (f", {', '.join(settings)}" if settings else ""))
        lines.append("its test ran against the kit's fake before your settings (it sets the template's variable and reads "
                     "the fake's own world); its first poll, in its log, says whether it reads the real system")
        if {"user_env", "user_file"} & set(creds) and "basic" not in (target / f"{name}.py").read_text(encoding="utf-8").casefold():
            lines.append(f"{name}.py does not log in with an account and a token yet (HTTP Basic, auth_format \"Basic "
                         "{basic}\"; Jira Cloud's search is /rest/api/3/search/jql): that is a change to the script, so "
                         + gate("[[tasking]]hand it to the Coordinator (hand_to_coordinator)[[/tasking]][[workflows]]it is a "
                                "worker's, as a Workflows item (skill code-work)[[/workflows]][[no-workflows]]say it is not "
                                "handed on here (code work) and that installing Workflows is the one step[[/no-workflows]] before saying it reads"))
    steps = schedule.with_token(rec["steps"], creds)
    if rec["strategy"] == "source":
        steps = [{**s, "entry": {**s["entry"], "url": base_url}} for s in steps]
    lines += schedule.write(project, store, steps, prov["reason"])
    rec.update(state="approved", clearance="approved", token_env=creds.get("token_env"), credentials=creds,
               data_class=data_class, base_url=base_url, base_url_from=came,
               approved={"at": time.time(), "quote": prov.get("quote"), "turn": prov.get("turn")}, steps=steps)
    _save(store, name, rec)
    missing = []
    for k, v in creds.items():
        if k.endswith("_env"):
            is_set = bool(os.environ.get(v))
            lines.append(f"{v} is {'set' if is_set else 'not set'} in this process's environment; the programs orchestra run "
                         "starts inherit its environment, so the owner sets it there and restarts orchestra run if it was "
                         "not set when it started")
            if not is_set and not (k == "token_env" and "token_file" in creds) and not (k == "user_env" and "user_file" in creds):
                missing.append(f"{v}, in the environment orchestra run starts with")
        else:
            state = kits.file_state(v)
            lines.append(f"{v}: {state} (its content is never read here)")
            if state != "there, chmod 600":
                missing.append(f"{v}: the file, chmod 600" if state == "not there yet" else f"chmod 600 {v}")
    said, more = _confirm_heard(store, rec, project)
    lines += said
    if installed.workflows():
        lines.append(items_follow_up(kits.title(rec["system"])))
    return answer(project, {"changes": []}, lines, integration=name, clearance="approved", credentials=creds, **more,
                  token_env=creds.get("token_env"), env_set=bool(creds.get("token_env") and os.environ.get(creds["token_env"])),
                  owner_must_set=missing)


def items_follow_up(title: str) -> str:
    """The line after a connection, with the Workflows plugin installed: its items as a workflow, observe only."""
    return (f"next: if {title} has items (tickets, incidents, threads), propose in one sentence a workflow that makes them "
            "items, naming the items and the stocks the owner gets (each step's count and the moves between steps, which "
            f"discovery finds), observe only, writing nothing to {title} (skill connect-system); declare it only on their yes")


def heard(store: AgentStore, system: str) -> list[str]:
    """The sources the bus has heard as the system's actors (``bot.<system>``, ``person.<system>.<account>``),
    as the Machines page has them: how a relayed system is confirmed heard."""
    seen = design_dynamics.last_seen(store)
    return sorted(s for s in seen if s in (system, f"bot.{system}") or s.startswith((f"bot.{system}.", f"person.{system}.")))


def _confirm_heard(store: AgentStore, rec: dict, project: Project | None = None) -> tuple[list[str], dict]:
    """What the answer says of whether the system is heard yet, and what to do next to confirm it."""
    system, every = rec["system"], rec.get("every") or "5m"
    if not rec.get("events"):
        return ([(f"it measures only: its series appear on the Instruments page after the first [[collect]] round "
                 f"(every {every}); nothing of it is heard on the bus")],
                {"next": f"After {every}, confirm its series with list_metrics (the queries' names) and tell the owner."})
    got = heard(store, system)
    if got:
        joined = integrate_heard.join(project, store, system) if project is not None else []
        return [f"{kits.title(system)} is heard on the bus already: {', '.join(got[:5])}"
                + (f"; joined to machine {system}" if joined else "")], {"heard": got}
    return ([(f"{kits.title(system)} is not heard on the bus yet: its first poll is within {every} of orchestra run reading "
             "the overlay")],
            {"heard": [], "next": (f"Confirm it is heard: after {every}, read_design shows machine {system} running, its "
                                   f"events heard from bot.{system}, bot.{system}.<account> or person.{system}.<account>. Tell the owner it is heard, "
                                   "or, if not, read the collector's log (its [[machines]] process) and say why.")})


COMMANDS = {"integrate_system": integrate_system, "approve_integration": approve_integration}
