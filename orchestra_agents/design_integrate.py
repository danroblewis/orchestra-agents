"""Connecting an outside system (Orchestra's ``docs/INTEGRATING.md``, task I3): the Supervisor's
``integrate_system`` and ``approve_integration``, in the ``design`` group (``design.handle``: the
Supervisor's alone, each with its reason and provenance).

``integrate_system`` takes the system's name and what the owner wants from it, and the strategy
(INTEGRATING.md §2; ``integrate_kits.choose`` when none is given):

- **poller** (events and series) or **collect** (series alone): the bridges plugin's kit is
  scaffolded into the state directory (``<state>/machines/<name>/``), its test is run against the
  kit's fake, and the owner's words and settings are filled into its ``machine.toml`` (after the
  test, which reads the fake's own world). A poller's system is declared on the Machines page as
  a machine managed elsewhere (``managed: external``), its actor source ``bot.<system>`` joined to
  it (``alias``), with its expected effect when the owner named the stock it fills: the poller is a
  relay and never a machine (Orchestra's rule 2). Without the kits nothing is written and the
  answer proposes INTEGRATING.md's no-kit route;
- **source**: a ``[[sources]]`` entry for a Prometheus-API store, planned (no code);
- **bridge**: the bridges' own reader's section, planned.

Nothing reads the real system yet: the program's ``machine.toml`` stays ``clearance = "needs
approval"`` and what runs it is only planned (``integrate_schedule``). The answer carries the
question the Supervisor puts to the owner in the chat (``ask_owner``): their approval, and the NAME
of the environment variable that holds the token, never the token.

``approve_integration`` is the owner's alone (a quote of their words this turn, ``author: owner``):
it records the clearance, the data class and the variable's name in ``machine.toml`` (or the store's
``auth.bearer_env``), then writes the plan to the overlay, so the program then reads the real
system. The record of each integration is the Supervisor's state (``integrations``)."""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from . import design_dynamics, design_machines
from . import integrate_kits as kits
from . import integrate_schedule as schedule
from .config import Project
from .design import MACHINE, OWNER, answer, apply, page_line
from .secret_shapes import redact
from .store import AgentStore, StoreError

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


def _taken(project: Project, store: AgentStore, name: str) -> str | None:
    if name in integrations(store):
        return f"the integration {name} (approve_integration, or another name)"
    raw = project.orchestra.config.raw
    for section in ("machines", "collect", "scrape", "sources"):
        if any(isinstance(e, dict) and e.get("name") == name for e in raw.get(section) or []):
            return f"a [[{section}]] entry"
    return None


def _no_kits(system: str, strategy: str) -> dict:
    return {"ok": True, "integrated": False, "said": [
        (f"the bridges plugin's kits (orchestra_bridges.kits) are not installed in Orchestra's Python, so nothing was "
        f"scaffolded, declared or written for {kits.title(system)}"),
        "the routes (" + kits.INTEGRATING + " §4): with the kits, the owner's coding tool installs the bridges plugin into "
        "Orchestra's environment and you call integrate_system again; without them, the program is the one-file poller of "
        "§5.2 beside minikit.py (§4, \"Without the kits\"), with its own fake and test: that is code, so hand it to the "
        "Coordinator (hand_to_coordinator) with the system, what the owner wants, and §3's rules (the token by its "
        "variable's name, clearance before any read, metadata only)",
        "a store with a Prometheus API needs no kit: strategy source"],
        "next": f"tell the owner the kits are missing and which route you propose for {kits.title(system)}"}


def _declare(project: Project, store: AgentStore, system: str, name: str, every: str, emits: list[str], prov: dict) -> tuple[dict, list]:
    """The system as a machine managed elsewhere, its actor source joined to it; never the poller."""
    mid, actor = system, f"bot.{system}"
    m = {"id": mid, "title": kits.title(system), "kind": "programmatic", "managed": "external",
         "description": f"{kits.title(system)} itself: what its people and bots do there, relayed by the poller {name}",
         "tags": ["integration"], "engagement": {"mode": "continuous"}, "receives": [], "emits": emits, "reads": [],
         "spec": {"source": actor, "receive": f"polled every {every} by {name} (a relay in the state directory, not a machine)"}}
    design_machines.check_machine(m)
    known = mid in (design_machines.intended_api.current(project).get("machines") or {})
    ops = ([] if known else [{"op": "propose_machine", **m, "guessed": []}]) + [{"op": "alias", "machine": mid, "observed": actor}]
    done = apply(project, store, ops, prov)
    who = "added by you (from your message)" if prov["author"] == OWNER else "proposed by the Supervisor"
    lines = [(f"machine {mid} ({m['title']}) {'already declared; ' if known else 'declared, programmatic, managed elsewhere, ' + who + '; '}"
             f"heard as {actor} (and its people as person.{system}.<account>)"),
             page_line(project, "the Machines page draws it dashed, \"unconfirmed\", until its events are heard and the owner "
                                "confirms it")]
    return done, lines


def _stock(project: Project, store: AgentStore, a: dict, prov: dict) -> tuple[str | None, list]:
    name = (a.get("stock") or "").strip()
    if not name:
        return None, []
    stocks = design_machines.intended_api.current(project).get("stocks") or {}
    sid = next((k for k, s in stocks.items() if (s.get("name") or k).casefold() == name.casefold()), None)
    if sid:
        return sid, [f"stock {sid} ({name}) was declared already"]
    got = design_dynamics.declare_stock(project, store, {"name": name, "unit": a.get("unit"), "description": a.get("wants")}, prov)
    return got["id"], got["said"]


def _effect(project: Project, store: AgentStore, mid: str, sid: str, prov: dict) -> list:
    got = design_machines.declare_expected_effect(
        project, store, {"machine": mid, "expect": [{"kind": "appears", "to": sid}], "horizon": "1d"}, prov)
    return got["said"]


def _ask(system: str, kit_env: str | None, strategy: str, base_url_missing: bool, scope: str | None = None) -> str:
    t = kits.title(system)
    parts = [(f"May I read {t}? It reads metadata only (ids, keys, accounts, states, times; never titles, text or "
             "messages), at most the configured rate, and writes nothing to it. Your yes, in your words, naming the "
             "token's scope (read-only)"),
             (f"the NAME of the environment variable that will hold the token (for example {kit_env or system.upper() + '_TOKEN'}), "
             "set in the environment `orchestra run` starts with; never the token itself, and not in this chat")]
    if base_url_missing:
        parts.append(f"{t}'s address (its API's base URL)")
    if scope:
        parts.append(f"{kits.SCOPE[scope]} ({scope})")
    return "; ".join(parts) + "."


def integrate_system(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    system = _system(a)
    wants = redact((a.get("wants") or "").strip())
    if len(wants) < 5:
        raise StoreError("say what the owner wants from it, in a sentence (issue transitions and open counts)")
    strategy = a.get("strategy") or kits.choose(system)
    if strategy not in kits.STRATEGIES:
        raise StoreError(f"strategy is one of {', '.join(kits.STRATEGIES)} (INTEGRATING.md §2)")
    settings = kits.check_settings(a.get("settings") or {})
    base_url = (a.get("base_url") or "").strip() or None
    if base_url and not re.match(r"^https?://[^\s/]+", base_url):
        raise StoreError("base_url is the API's root, an http(s) URL")
    name = (a.get("name") or "").strip() or (system if strategy in ("source", "bridge") else f"{system}_poller")
    if not design_machines.ID.match(name) or (strategy in ("poller", "collect") and name in kits.BRIDGES):
        raise StoreError(f"{name!r}: the poller's name is lower case letters, digits and _ (2 to 41), and never a bridge's "
                         f"own name ({', '.join(kits.BRIDGES)}): {system}_poller")
    if taken := _taken(project, store, name):
        raise StoreError(f"{taken} already has the name {name}: nothing was written")
    rec = {"system": system, "strategy": strategy, "wants": wants, "clearance": NEEDS, "token_env": None, "at": time.time(),
           "base_url": base_url}
    if strategy in ("source", "bridge"):
        return _plan_only(project, store, name, rec, settings, a, prov)
    if not kits.installed():
        return _no_kits(system, strategy)
    kit = a.get("kit") or kits.kit_for(system, strategy)
    if kit not in kits.kits():
        raise StoreError(f"no kit {kit!r}: the kits are {', '.join(kits.kits())}")
    design_machines.intended_api.current(project)        # the declared layer answers (its host runs) before anything is written
    target = kits.scaffold(project, kit, name)
    try:
        return _scaffolded(project, store, a, prov, rec, kit, name, target, settings)
    except BaseException:
        if name not in integrations(store):               # nothing recorded: nothing left behind
            shutil.rmtree(target, ignore_errors=True)
        raise


def _scaffolded(project: Project, store: AgentStore, a: dict, prov: dict, rec: dict, kit: str, name: str, target: Path,
                settings: dict) -> dict:
    system, strategy, base_url = rec["system"], rec["strategy"], rec["base_url"]
    test = kits.run_test(target, name)
    kits.fill(target, {**({"system": system} if kit == "http" else {}), **settings}, base_url)
    mf = kits.read(target)
    every = str(mf.get("settings", {}).get("every") or "5m")
    events = strategy == "poller" and kit != "promql"
    lines: list = [(f"scaffolded the {kit} kit's poller {name} into {target} (machine.toml, {name}.py, test_{name}.py, AGENTS.md); "
                   "machine.toml: clearance \"needs approval\", so it reads only a fake on this machine"),
                   f"its test against the kit's fake: {'passed' if test['passed'] else 'NOT passed'} ({test['summary']})"]
    rec.update(name=name, kit=kit, dir=str(target), test=test, base_url=base_url or mf.get("settings", {}).get("base_url"),
               scope=next((k for k in kits.SCOPE_OF.get(kit, ()) if k not in settings), None))
    if not test["passed"]:
        rec["state"] = "test failed"
        _save(store, name, rec)
        lines.append("nothing was declared or planned: the program is code, so hand it to the Coordinator with the test's "
                     "failure (hand_to_coordinator) or tell the owner")
        return {"ok": True, "integrated": False, "said": lines, "integration": name, "dir": str(target), "test": test}
    changes, mid = [], None
    if events:
        done, said = _declare(project, store, system, name, every, kits.publishes(target), prov)
        changes += done.get("changes") or []
        lines += said
        mid = system
    sid, said = _stock(project, store, a, prov)
    lines += said
    if sid and mid:
        lines += _effect(project, store, mid, sid, prov)
    rec["steps"] = schedule.poller(name, target, every, kits.title(system), events=events, workflows=kits.workflows_installed())
    rec.update(state="needs approval", machine=mid, stock=sid)
    _save(store, name, rec)
    return _report(project, name, rec, {"changes": changes}, lines, mf.get("settings", {}).get("token_env"), test=test, dir=str(target))


def _plan_only(project: Project, store: AgentStore, name: str, rec: dict, settings: dict, a: dict, prov: dict) -> dict:
    system, strategy = rec["system"], rec["strategy"]
    if not rec["base_url"] and not (strategy == "bridge" and system in kits.BRIDGE_DEFAULT_API):
        raise StoreError(f"base_url: the {'store' if strategy == 'source' else 'site'}'s address is needed to plan it")
    lines: list = []
    if strategy == "source":
        entry = {"name": name, "url": rec["base_url"], "prefix": settings.pop("prefix", system), **settings}
        if not entry.get("match"):
            raise StoreError("settings.match: the PromQL selectors to read (INTEGRATING.md §8: never a whole tenant)")
        rec["steps"] = schedule.source(entry)
        lines.append(f"planned the [[sources]] entry {name} for {rec['base_url']}: read in place, nothing copied, no code")
    else:
        if system not in kits.BRIDGES:
            raise StoreError(f"the bridges have no reader for {system}: they read {', '.join(kits.BRIDGES)}; use strategy poller")
        scope = kits.BRIDGE_SCOPE.get(system, ())
        if scope and not any(settings.get(k) for k in scope):
            raise StoreError(f"settings.{' or settings.'.join(scope)}: what the {kits.title(system)} reader follows is the "
                             "owner's to name (ask them); nothing was planned")
        rec["steps"] = schedule.bridge(system, {**({"api": rec["base_url"]} if rec["base_url"] else {}), **settings})
        lines.append(f"planned the bridges' reader {kits.BRIDGES[system]}; never run a poller over the same items too")
    sid, said = _stock(project, store, a, prov)
    rec.update(name=name, state="needs approval", stock=sid)
    _save(store, name, rec)
    return _report(project, name, rec, {"changes": []}, lines + said, None)


def _report(project: Project, name: str, rec: dict, done: dict, lines: list, kit_env: str | None, **extra) -> dict:
    planned = [schedule.describe(s) for s in rec["steps"]]
    missing_url = kits.placeholder(rec.get("base_url"))
    lines.append(f"planned, written to the owner's overlay once approved: {'; '.join(planned)}")
    if kits.BRIDGES.get(rec["system"]) and rec["strategy"] != "bridge":
        lines.append(f"the bridges also read {rec['system']} ({kits.BRIDGES[rec['system']]}); never run both over the same items")
    ask = _ask(rec["system"], kit_env, rec["strategy"], missing_url, rec.get("scope"))
    lines.append("waiting for the owner's approval and the token's variable name (approve_integration)")
    return answer(project, done, lines, integration=name, clearance=NEEDS, planned=planned, ask_owner=ask,
                  owner_must_set=[f"the approval, in their words, for {kits.title(rec['system'])}",
                                  "an environment variable holding the token, by name, in orchestra run's environment"]
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
    token_env = (a.get("token_env") or "").strip()
    if not kits.env_name_ok(token_env):
        raise StoreError("token_env is the NAME of an environment variable (JIRA_TOKEN), never a token: ask the owner for it")
    data_class = (a.get("data_class") or DATA_CLASS).strip()
    base_url = (a.get("base_url") or "").strip() or rec.get("base_url")
    if kits.placeholder(base_url):
        raise StoreError(f"{kits.title(rec['system'])}'s address is still the template's ({base_url}): ask the owner for it "
                         "(base_url); nothing was changed")
    settings = kits.check_settings(a.get("settings") or {})
    scope = rec.get("scope")
    if scope and scope not in settings:
        raise StoreError(f"{kits.title(rec['system'])}'s scope ({scope}: {kits.SCOPE[scope]}) is still the template's: ask the "
                         f"owner for it and pass it in settings.{scope}; nothing was changed")
    lines = []
    if rec.get("dir"):
        target = Path(rec["dir"])
        if settings:
            kits.fill(target, settings, None)
        kits.clear(target, token_env, data_class, base_url)
        lines.append(f"{rec['dir']}/machine.toml: clearance \"approved\", data_class {data_class!r}, token_env {token_env} "
                     f"(the name only), base_url {base_url}" + (f", {', '.join(settings)}" if settings else ""))
        lines.append("its test ran against the kit's fake before your settings (it sets the template's variable and reads "
                     "the fake's own world); its first poll, in its log, says whether it reads the real system")
    steps = schedule.with_token(rec["steps"], token_env)
    if rec["strategy"] == "bridge" and rec["system"] in kits.BRIDGE_TOKEN:      # the reader reads this variable
        steps = [{**s, "value": {**s["value"], "token_env": token_env}} if s.get("key") == f"bridges.{rec['system']}" else s
                 for s in steps]
    if rec["strategy"] == "source":
        steps = [{**s, "entry": {**s["entry"], "url": base_url}} for s in steps]
    lines += schedule.write(project, store, steps, prov["reason"])
    rec.update(state="approved", clearance="approved", token_env=token_env, data_class=data_class, base_url=base_url,
               approved={"at": time.time(), "quote": prov.get("quote"), "turn": prov.get("turn")}, steps=steps)
    _save(store, name, rec)
    is_set = bool(os.environ.get(token_env))
    lines.append(f"{token_env} is {'set' if is_set else 'not set'} in this process's environment; the programs orchestra "
                 "run starts inherit its environment, so the owner sets it there and restarts orchestra run if it was "
                 "not set when it started")
    return answer(project, {"changes": []}, lines, integration=name, clearance="approved", token_env=token_env,
                  env_set=is_set, owner_must_set=[] if is_set else [f"{token_env}, in the environment orchestra run starts with"])


COMMANDS = {"integrate_system": integrate_system, "approve_integration": approve_integration}
