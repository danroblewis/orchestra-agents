"""The Dynamics side of the design tools (machine-design.md §2.2): declared stocks and effects,
targets, expected marks and ignores on discovered parts, the manual link of a declared stock to
the discovered one it turned out to be, and the ways to measure a declared element. Everything is
an op on the declared layer (``design.apply``); nothing here binds a series or makes discovery
find anything (Orchestra's rules 6 and 7).

**Clearance is decided from facts, never from the model's words.** An option needs the owner's
approval (``needs approval``: nothing reads it, nothing previews it, ``add_instrument`` refuses it)
when it reads a company system: a ``source`` option, one whose label, how, system, url or command
names one (``COMPANY``), or one whose url or command reaches any host but this machine and the
project's own configured stores and scrape targets (``reaches``). Until data classes exist (the
work charter §8) that is every company system. An option's url, command and series are kept with
the Supervisor (its agent state, ``measure_targets``: Orchestra's option keeps only its own
fields), and ``add_instrument`` runs only what was proposed."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from orchestra.config import ConfigError, parse_duration

from . import intended_api
from .config import Project
from .design import MACHINE, answer, apply, page_line
from .secret_shapes import secret_in
from .store import AgentStore, StoreError

OPTION_KINDS = ("existing_series", "script", "scrape", "source", "counters", "simulated")
# a company system: an option naming one (in its label, how, system, url or command) needs the owner's approval
COMPANY = ("jira", "github", "gitlab", "bitbucket", "prometheus", "jenkins", "confluence", "slack", "pagerduty", "datadog",
           "grafana", "splunk", "sentry", "opsgenie", "servicenow", "circleci", "buildkite")
NEEDS_APPROVAL = "needs approval"
CLEARED = "cleared: reads only this machine and the project's own stores and repository"
LOCAL = ("localhost", "127.0.0.1", "::1", "0.0.0.0", "")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{1,63}$")
URL = re.compile(r"\b[a-z][a-z0-9+.-]*://(?:[^/\s@'\"]*@)?(\[[^\]]+\]|[^/\s:'\"?#]+)", re.IGNORECASE)
HOST_PORT = re.compile(r"(?<![\w/.@-])((?:[a-z0-9-]+\.)+[a-z][a-z0-9-]*):\d{2,5}\b", re.IGNORECASE)


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").casefold()) if len(w) >= 3}


def _declare(project: Project, store: AgentStore, a: dict, prov: dict, part: str) -> dict:
    name = (a.get("name") or "").strip()
    if not name:
        raise StoreError(f"give the {part}'s name, in the owner's words (Features)")
    near = (a.get("near") or "").strip() or None
    owner_placed = bool(near and prov.get("quote") and _words(near) & _words(prov["quote"]))
    guessed = ["near"] if near and not owner_placed else []
    op = {"op": f"declare_{part}", "name": name, "unit": a.get("unit") or None, "near": near, "note": prov.get("quote"),
          "guessed": guessed}
    done = apply(project, store, [{k: v for k, v in op.items() if v not in (None, [])}], prov)
    eid = done["changes"][0]["id"]
    origin = "added by you (from your message)" if prov["author"] == "owner" else "proposed by the Supervisor"
    placed = ("where you said it belongs" if owner_placed else "placed by the Supervisor") + f" beside {near}" if near else "not placed"
    return answer(project, done, [f"{part} {eid} ({name}) declared: {origin}; {placed}",
                                  page_line(project, "the Dynamics page draws it in the Expected group as a ghost, \"no series "
                                                     "yet\", until a series is found for it"),
                                  "next: find how to measure it (propose_measurements)"],
                  id=eid, part=part, placed_by_owner=owner_placed)


def declare_stock(project, store, a, prov):
    return _declare(project, store, a, prov, "stock")


def declare_effect(project, store, a, prov):
    return _declare(project, store, a, prov, "effect")


def resolve(project: Project, element: str) -> tuple[str, str, bool]:
    """``element`` as (part, id or key, declared?): ``stock:features``, or a declared id, or a
    discovered stock's key."""
    element = (element or "").strip()
    if not element:
        raise StoreError("name the element: a declared stock or effect's id, or part:id")
    part, _, rest = element.partition(":")
    layer = intended_api.current(project)
    if rest and part in ("stock", "effect", "flow", "machine", "attachment"):
        return part, rest, rest in (layer.get(f"{part}s") or {})
    for p in ("stock", "effect", "machine", "flow", "attachment"):
        if element in (layer.get(f"{p}s") or {}):
            return p, element, True
    return "stock", element, False


def set_target(project, store, a, prov):
    part, eid, declared = resolve(project, a.get("element"))
    cmp = a.get("compare")
    if cmp not in ("below", "above", "within"):
        raise StoreError("compare is below, above or within (within needs hi)")
    if not isinstance(a.get("value"), int | float) or (cmp == "within" and not isinstance(a.get("hi"), int | float)):
        raise StoreError("value is a number (and hi too, for within)")
    try:
        over_s = parse_duration(a.get("over"), 0.0)
    except ConfigError as e:
        raise StoreError(f"over: {e}") from None
    if over_s <= 0:
        raise StoreError("over is the window the target's mean is judged over: 1h, 1d, 7d")
    op = {"op": "set_target", "part": part, ("id" if declared else "key"): eid, "target": cmp, "value": a["value"],
          "over_s": over_s, **({"hi": a["hi"]} if cmp == "within" else {})}
    done = apply(project, store, [op], prov)
    hi = f"–{a['hi']}" if cmp == "within" else ""
    return answer(project, done, [f"target on {part} {eid}: {cmp} {a['value']}{hi} (the mean over {a['over']})",
                                  page_line(project, "the page marks it met, missed or unknown")])


def allowed_hosts(project: Project) -> set[str]:
    """This machine, and the hosts of the project's own configured stores and scrape targets."""
    raw = project.orchestra.config.raw
    urls = [e.get("url") for k in ("sources", "scrape") for e in raw.get(k) or [] if isinstance(e, dict)]
    urls.append((raw.get("metrics") or {}).get("url"))
    return set(LOCAL) | {(urlsplit(u).hostname or "").lower() for u in urls if isinstance(u, str) and "://" in u}


def reaches(text: str) -> set[str]:
    """The hosts a url, command or script names (a scheme's URL, or host:port)."""
    found = {m.group(1).strip("[]").lower() for m in URL.finditer(text or "")}
    return found | {m.group(1).lower() for m in HOST_PORT.finditer(text or "")}


def company_named(*texts: str) -> list[str]:
    words = set(re.findall(r"[a-z0-9]+", " ".join(t or "" for t in texts).casefold()))
    return [c for c in COMPANY if c in words]


def clearance(project: Project, kind: str, *texts: str) -> tuple[str, str | None]:
    """(clearance, why) from facts: the option's kind, the company systems it names, the hosts it reaches."""
    if kind == "source":
        return NEEDS_APPROVAL, "it reads another store"
    if named := company_named(*texts):
        return NEEDS_APPROVAL, f"it names {', '.join(named)}"
    if far := sorted(reaches(" ".join(texts)) - allowed_hosts(project)):
        return NEEDS_APPROVAL, f"it reaches {', '.join(far)}, neither this machine nor one of the project's own stores"
    return CLEARED, None


def no_secret(*texts: str) -> None:
    for t in texts:
        if t and (why := secret_in(t)):
            raise StoreError(f"it looks like it holds a secret ({why}); a credential is named only by its environment variable "
                             "(bearer_env), set in the overlay and supplied out of band. Nothing was recorded or run")


def _option(project: Project, i: int, o: dict) -> tuple[dict, dict]:
    """The option as Orchestra keeps it, and what the Supervisor keeps of it (url, command, series)."""
    if not isinstance(o, dict):
        raise StoreError("each option is an object: {label, kind, how, cost, caveat?, series?, url?, command?, system?, bearer_env?}")
    kind = o.get("kind")
    if kind not in OPTION_KINDS:
        raise StoreError(f"option {i + 1}: kind is one of {', '.join(OPTION_KINDS)}")
    for k in ("label", "how", "cost"):
        if not (o.get(k) or "").strip():
            raise StoreError(f"option {i + 1}: give its {k} ({'how long a round takes, what it reads, a store’s request rate' if k == 'cost' else k})")
    env = (o.get("bearer_env") or "").strip()
    if env and not ENV_NAME.match(env):
        raise StoreError(f"option {i + 1}: bearer_env is the name of an environment variable (WORK_TOKEN), never a value")
    url, command, series = ((o.get(k) or "").strip() for k in ("url", "command", "series"))
    system = (o.get("system") or "").strip().lower()
    no_secret(url, command, o["how"], o["label"])
    level, why = clearance(project, kind, o["label"], o["how"], system, url, command)
    how = o["how"].strip()                     # Orchestra keeps an option's fields only (its OPTION): the rest goes in how
    how += f" (the series {series})" if series else ""
    how += f" (reads {system})" if system and system not in how.lower() else ""
    how += f"; its token is read from the environment variable {env}, supplied out of band" if env else ""
    key = "abcdefghij"[i]
    kept = {"key": key, "title": o["label"].strip(), "kind": kind, "how": how, "cost": o["cost"].strip(),
            "caveat": (o.get("caveat") or "").strip() or None, "clearance": level, "simulated": kind == "simulated",
            "instrument": None, "commit": None, "first_points": [], "provenance": None}
    mine = {"kind": kind, "url": url or None, "command": command or None, "series": series or None,
            "bearer_env": env or None, "why_approval": why}
    return kept, mine


def options_of(project: Project, part: str, eid: str) -> dict:
    el = (intended_api.current(project).get(f"{part}s") or {}).get(eid) or {}
    return el.get("measure") or {"options": [], "chosen": None}


def targets_of(store: AgentStore, part: str, eid: str) -> dict:
    """What the Supervisor kept of each option of ``part:eid`` (its url, command, series), by key."""
    return dict((store.agent_state(MACHINE).get("measure_targets") or {}).get(f"{part}:{eid}") or {})


def propose_measurements(project, store, a, prov):
    part, eid, declared = resolve(project, a.get("element"))
    if not declared or part not in ("stock", "effect"):
        raise StoreError(f"{eid} is not a declared stock or effect: declare it first (declare_stock)")
    given = a.get("options")
    if not isinstance(given, list) or not 1 <= len(given) <= 6:
        raise StoreError("options is a list of one to six ways to measure it, the best first, the simulated one last")
    pairs = [_option(project, i, o) for i, o in enumerate(given)]
    options = [k for k, _ in pairs]
    sims = [i for i, o in enumerate(options) if o["simulated"]]
    if sims and sims != [len(options) - 1]:
        raise StoreError("a simulated placeholder is always the last option")
    old = options_of(project, part, eid)
    kept = {o["key"]: o for o in old["options"]}
    for o in options:                                     # what an option already recorded stays with it
        if (was := kept.get(o["key"])) and was.get("title") == o["title"]:
            o.update({k: was[k] for k in ("instrument", "commit", "first_points", "provenance") if was.get(k)})
    chosen = old.get("chosen") if old.get("chosen") in {o["key"] for o in options} else None
    done = apply(project, store, [{"op": "record_measurement", "part": part, "id": eid, "options": options, "chosen": chosen}], prov)
    store.update_agent_state(MACHINE, "measure_targets",
                             lambda t: {**(t or {}), f"{part}:{eid}": {o["key"]: m for o, (_, m) in zip(options, pairs, strict=True)}})
    lines = [f"{len(options)} way(s) to measure {eid}, in order: " + "; ".join(f"{o['key']} {o['title']} ({o['kind']})" for o in options),
             page_line(project, "the Dynamics page shows them in the element's drawer, \"How to measure it\"")]
    if waiting := [f"{o['key']} ({m['why_approval']})" for o, (_, m) in zip(options, pairs, strict=True) if o["clearance"] == NEEDS_APPROVAL]:
        lines.append(f"{', '.join(waiting)}: recorded \"needs approval\"; nothing reads or previews it until the owner clears it")
    return answer(project, done, lines, options=[{k: o[k] for k in ("key", "title", "kind", "clearance")} for o in options])


def _mark(project, store, a, prov, op: str) -> dict:
    part = a.get("part") or "stock"
    key = (a.get("key") or "").strip()
    if part not in ("stock", "flow") or not key:
        raise StoreError("name the discovered part: part (stock or flow) and its key, as read_model or read_design gives it")
    done = apply(project, store, [{"op": op, "part": part, "key": key}], prov)
    said = ("marked expected" if op == "expect" else "ignored")
    drawn = ("always drawn, with the pin; a ghost when a version leaves it out" if op == "expect" else
             "it moves to the collapsed \"ignored\" group; nothing is hidden or removed")
    return answer(project, done, [f"{part} {key} {said}", page_line(project, f"the Dynamics page: {drawn}")])


def mark_expected(project, store, a, prov):
    return _mark(project, store, a, prov, "expect")


def ignore(project, store, a, prov):
    return _mark(project, store, a, prov, "ignore")


def link(project, store, a, prov):
    from .design_confirm import publish_waiting
    part, eid, declared = resolve(project, a.get("declared"))
    found = (a.get("discovered") or "").strip()
    if not declared or part not in ("stock", "effect") or not found:
        raise StoreError("declared is a declared stock or effect's id; discovered, the key of the discovered stock or series")
    done = apply(project, store, [{"op": "link", "part": part, "id": eid, "found_as": found}], prov)
    lines = [(f"{eid} linked to {found}: a manual binding, origin declared, drawn as an assumption until fitted; {found} "
              "keeps its name")]
    lines += publish_waiting(project, store)
    return answer(project, done, lines)


def _event_edges(project: Project, store: AgentStore, layer: dict) -> dict:
    """Each event type's publishers and listeners on both layers: the declared machines' emits and
    receives, what was heard on the bus (its sources) and what announced machines listen for."""
    out: dict[str, dict] = {}

    def row(t: str) -> dict:
        return out.setdefault(t, {"declared_emitters": [], "declared_receivers": [], "heard_from": [], "listened_by": []})
    for mid, m in (layer.get("machines") or {}).items():
        for t in m.get("emits") or []:
            row(t)["declared_emitters"].append(mid)
        for t in m.get("receives") or []:
            row(t)["declared_receivers"].append(mid)
    for t, d in (layer.get("events") or {}).items():
        row(t)["purpose"] = d.get("description")
    end = store.events.end()
    evs, _ = store.events.read(max(0, end - 5000), limit=5000)
    for e in evs:
        src = str(e.get("source") or "")
        if e.get("type") in out and src not in out[e["type"]]["heard_from"]:
            out[e["type"]]["heard_from"].append(src)
        if e.get("type") == "orchestra.machine.started":
            for t in e.get("listens_for") or []:
                if t in out and src not in out[t]["listened_by"]:
                    out[t]["listened_by"].append(src)
    return out


def read_design(project: Project, store: AgentStore, a: dict) -> dict:
    from .design_instruments import drafts
    from .design_passes import last_proposal
    lay = intended_api.layer(project)
    out = lay.design()
    layer = lay.read()["layer"]
    machines = {mid: {k: m.get(k) for k in ("state", "title", "description", "kind", "managed", "engagement", "receives",
                                            "emits", "reads", "spec", "guessed", "pending", "expected_effect", "latest")}
                for mid, m in (layer.get("machines") or {}).items()}
    return {"ok": True, **out, "machines": machines, "events": _event_edges(project, store, layer),
            "instruments": drafts(store), "refinement": last_proposal(store), "kept_in": intended_api.where(project)}


COMMANDS = {"declare_stock": declare_stock, "declare_effect": declare_effect, "set_target": set_target,
            "propose_measurements": propose_measurements, "mark_expected": mark_expected, "ignore": ignore, "link": link,
            "read_design": read_design}
READS = frozenset({"read_design"})
