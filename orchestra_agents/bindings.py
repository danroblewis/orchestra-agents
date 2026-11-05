"""An agent's own event bindings: what wakes it (the owner, 2026-11-04: "I think agents should be
able to adjust their own event bindings. ... I really do want agents to be able to remove their own
event bindings and add more event bindings").

``list_bindings``, ``bind_event`` and ``unbind_event``, in the ``bindings`` group, which every
agent the host wakes holds (``org.effective_tools``). An agent changes its own; the Supervisor may
also change another's (``agent``). What wakes an agent, by where it comes from:

- **built in**: its record's ``wake_on`` (a built-in role's, a custom agent's record, a designed
  machine's declared ``receives``) and what a scheduled one reads each run;
- **each duty**: the ``listens`` of its machine spec's duties (``duties.py``);
- **its own**: the spec's ``[[bindings]]``, each with its reason; and ``[[unbound]]``, the built-in
  or duty wake events it took out (``machine_spec.apply``).

Every change:

- needs a reason in a sentence, and is refused past ``[agents] binding_changes_per_day`` (default
  30) for that agent in 24 hours, a cap of its own: it never counts against
  ``spec_changes_per_day``;
- may unbind a built-in wake event (the Supervisor's ``attention.needed``,
  ``orchestra.process.gave_up``, ``work.planned``), never the owner's own channels (the chat, which
  is no event, and ``orchestra.intent.requested`` from ``owner``) nor the crash-loop guard
  (``machine_spec.PROTECTED``);
- may bind a busy event, answered with a warning naming its rate in the last hour and how it wakes
  the agent (the Supervisor's are gathered every ``[agents] attention_every`` and capped by the
  day's spend); only a heartbeat unfiltered is refused (``bus_rates.refusal``);
- for an agent whose spec is the repository's (``.orchestra/machines/<name>/``, the Supervisor's,
  a custom agent's: one is started for it when it has none), is one commit of its ``machine.toml``
  and a line in its ``ledger.md`` on the Supervisor's branch (``spec_git``, as a duty is), undone
  with ``revert_change``;
- for a designed machine (its spec in the state directory, ``state_machines``), is kept in
  ``<state>/agents/bindings/<name>.json`` with its history, never committed (AGENTS.md rule 2), and
  undone with ``revert_change`` and the change's ``bind-…`` id. Its declaration's ``receives`` in
  Orchestra's declared layer is not touched: a change to a confirmed machine's declaration is a
  pending change the owner confirms (``design_machines.refine_machine``), so this is the running
  agent's binding, which its announcement shows;
- is published as ``role.changed`` (subject ``machine:<name>``), and the host announces the agent
  again (``orchestra.machine.started`` with its new ``listens_for``, host.py) at its next tick, so
  the Event Map shows what wakes it now. It applies at that tick: no session is restarted."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

from . import spec_git, supervisor_pr
from .bus_rates import busy, rates, refusal
from .config import Project
from .events import INTENT_REQUESTED
from .gate_busy import landing
from .machine_spec import (
    PROTECTED,
    ROOT,
    Binding,
    MachineSpec,
    load,
    render,
    state_bindings_path,
    unbinds,
)
from .store import AgentStore, StoreError

COMMANDS = frozenset({"list_bindings", "bind_event", "unbind_event"})
CHANGES = COMMANDS - {"list_bindings"}
SUPERVISOR = "supervisor"
DAY = 86_400.0
OWNER_CHANNELS = ("chat", "message", "messages", "owner")
REVERT_PREFIX = "bind-"


def changes_today(store: AgentStore, name: str, now: float | None = None) -> list[float]:
    now = now or time.time()
    return [t for t in store.agent_state(name).get("binding_changes") or [] if now - t < DAY]


def _every(seconds: float) -> str:
    s = int(seconds or 0)
    return f"{s // 3600}h" if s and s % 3600 == 0 else f"{s // 60}m" if s and s % 60 == 0 else f"{s}s"


def how(project: Project, name: str, w: str) -> str:
    """How an event ``w`` wakes the agent ``name``, as the host does it (host._wake_on_events)."""
    from .notices import DIGEST_ONLY
    t = w.split(":", 1)[0]
    if name != SUPERVISOR:
        return "at once (wake-ups queued while a turn runs are taken together in the next)"
    if t == INTENT_REQUESTED:
        return "at once (the owner's page request)"
    if t in DIGEST_ONLY:
        return "said in the next chat turn's digest, never a turn of its own"
    return (f"gathered with your other notices into one turn at most every {_every(project.config.attention_every)} "
            "([agents] attention_every), capped by review_usd_per_day")


def warning(project: Project, name: str, w: str, rate: dict[str, int]) -> str | None:
    """What to say of a busy wake event that is allowed: its rate, and how it wakes the agent."""
    why = busy(w, rate)
    if why is None:
        return None
    t = w.split(":", 1)[0]
    return f"{why}. Allowed: {t} came {rate.get(t, 0)} times in the last hour, and it wakes {name} {how(project, name, w)}"


def _records(project: Project) -> dict:
    """Every agent's record as written, before any spec is applied (org_store.all_agents without it)."""
    from . import state_machines
    from .org import built_in_agents
    from .org_store import RepoOrgStore
    out = dict(built_in_agents())
    out.update({a.name: a for a in state_machines.agents(project) if a.name not in out})
    out.update({a.name: a for a in RepoOrgStore(project).list_agents()})
    return out


def _target(project: Project, a: dict, role: str):
    from .org_store import all_agents
    name = (a.get("agent") or role or "").strip()
    if not role:
        raise StoreError("refused: no role. Bindings are changed from an agent's own session")
    if name != role and role != SUPERVISOR:
        raise StoreError(f"refused: you change your own bindings; {name}'s are its own, or the Supervisor's to change")
    agents = all_agents(project)
    agent = agents.get(name)
    if agent is None:
        raise StoreError(f"no agent {name!r} (list_agents names them)")
    if agent.builtin and not agent.resident:
        raise StoreError(f"{name} is woken by no event: its turns are the task system's assignments, answered by the host")
    return name, agent, _records(project).get(name)


def _spec(project: Project, name: str) -> tuple[MachineSpec, bool]:
    """The agent's spec and whether it is a designed machine's (kept in the state directory). An
    agent with none gets a new one in the repository's place, written by its first change."""
    spec = load(project, name)
    if spec is None:
        return MachineSpec(name=name), False
    return spec, spec.root != ROOT


def _list(project: Project, store: AgentStore, name: str, agent, record) -> dict:
    spec, designed = _spec(project, name)
    rate = rates(store)
    wakes = set(agent.wake_on)
    reads = set(agent.reads_events or [])

    def row(w: str, src: str) -> dict:
        on = w in wakes or w in reads
        r = {"event": w, "from": src, "per_hour": rate.get(w.split(":", 1)[0], 0),
             "wakes": (how(project, name, w) if w in wakes else "read at each scheduled run, not a wake-up") if on else "unbound"}
        if on and (why := busy(w, rate)):
            r["busy"] = why
        return r
    base_from = ("its declared receives (the declared layer; the owner confirms a change to it)" if designed else
                 "built in (orchestra-agents)" if record is not None and record.builtin else "its agent record")
    rows = [row(w, base_from) for w in [*(record.wake_on if record else []), *(record.reads_events if record else [])]]
    for d in spec.duties:
        rows += [row(w, f"duty {d.name}") for w in d.listens]
    rows += [{**row(b.event, "its own binding"), "reason": b.reason, "by": b.by, "at": b.at} for b in spec.bindings]
    always = [{"what": "the owner's chat", "why": "the owner's own channel: never unbound"}]
    if name == SUPERVISOR:
        always += [{"what": f"{t} from owner" if t == INTENT_REQUESTED else t, "why": f"{why}: never unbound"}
                   for t, why in PROTECTED.items()]
        always.append({"what": "escalate_to_supervisor", "why": "another agent's escalation"})
    cap = project.config.binding_changes_per_day
    return {"ok": True, "agent": name, "bindings": rows, "unbound": [b.to_dict() for b in spec.unbound], "always": always,
            "kept": ("the state directory, never committed (a designed machine's)" if designed else
                     f"{ROOT}/{name}/machine.toml, each change a commit on {spec_git.SIDE}"),
            "changes_today": len(changes_today(store, name)), "changes_per_day": cap,
            **({"history": _history(project, name)[-10:]} if designed else {})}


def handle(project: Project, store: AgentStore, command: str, a: dict[str, Any], role: str) -> dict:
    project.reload_config()
    name, agent, record = _target(project, a, role)
    if command == "list_bindings":
        return _list(project, store, name, agent, record)
    event = str(a.get("event") or a.get("type") or "").strip()
    reason = (a.get("reason") or "").strip()
    if len(reason) < 10:
        raise StoreError("give the reason in a sentence: it goes in the ledger and the commit")
    t = event.split(":", 1)[0]
    if command == "unbind_event" and (event.lower() in OWNER_CHANNELS or t in PROTECTED):
        what = PROTECTED.get(t) or "the owner's chat, their own channel"
        raise StoreError(f"refused: {event} is {what}. No agent unbinds the owner's own channels (the chat, "
                         f"{INTENT_REQUESTED} from owner) or the crash-loop guard (agent.crash_loop)")
    from .duties import LISTEN
    if not LISTEN.match(event):
        raise StoreError(f"not an event type: {event!r} (type or type:filter, e.g. orchestra.model.stalled:stock:queue)")
    cap = project.config.binding_changes_per_day
    if len(changes_today(store, name)) >= cap:
        raise StoreError(f"refused: {cap} changes to {name}'s bindings in the last 24 hours already ([agents] "
                         "binding_changes_per_day, the owner's). A revert is still allowed")
    spec, designed = _spec(project, name)
    before = snapshot(spec)
    by, at = role, time.strftime("%Y-%m-%d %H:%M")
    notes: list[str] = []
    if command == "bind_event":
        if why := refusal(event):
            raise StoreError(f"refused: {why}")
        if event in agent.wake_on:
            return {"ok": True, "changed": False, "agent": name, "event": event, "note": f"{event} wakes {name} already"}
        spec.unbound = [u for u in spec.unbound if u.event != event]
        if event not in _bound_elsewhere(spec, record) or unbinds(spec.unbound, event):
            spec.bindings.append(Binding(event=event, reason=reason, by=by, at=at))
        rate = rates(store)
        if w := warning(project, name, event, rate):
            notes.append(w)
        elif t not in _known_types() and not rate.get(t):
            notes.append(f"{t} is not a type the agents know and was not on the bus in the last hour: fine if a plugin "
                         "publishes it; nothing wakes you until it comes")
    else:
        hit = [w for w in [*agent.wake_on, *(agent.reads_events or [])] if w == event or (":" not in event and w.split(":", 1)[0] == event)]
        if not hit:
            raise StoreError(f"{event} does not wake {name}; list_bindings shows what does")
        spec.bindings = [b for b in spec.bindings if not (b.event == event or (":" not in event and b.event.split(":", 1)[0] == event))]
        if any(w in _bound_elsewhere(spec, record) for w in hit):
            spec.unbound = [u for u in spec.unbound if u.event != event] + [Binding(event=event, reason=reason, by=by, at=at)]
        if name == SUPERVISOR and t == "orchestra.process.gave_up":
            notes.append("a process Orchestra's launcher gave up on no longer wakes you: read_events and check_setup still show it")
    change = "bind" if command == "bind_event" else "unbind"
    what = f"{change} event {event}"
    done = (_keep_designed(project, spec, what, change, event, reason, by, before) if designed
            else _commit(project, store, spec, what, change, reason, by))
    if not done.get("changed", True):
        return {"ok": True, "changed": False, "agent": name, **done}
    store.set_agent_state(name, binding_changes=[*changes_today(store, name), time.time()], bindings_changed=True)
    store.events.publish("role.changed", {"machine": name, "change": change, "event": event, "reason": reason,
                                          "commit": done.get("commit") or done.get("revert"), "by": by},
                         source=role, subject=f"machine:{name}")
    return {"ok": True, "changed": True, "agent": name, "change": change, "event": event, **done,
            **({"warnings": notes} if notes else {}),
            "applies": ("at the host's next tick, a second or so; no session restarts. The host announces "
                        f"{name} again, so the Event Map shows what wakes it now"),
            "changes_left_today": max(0, cap - len(changes_today(store, name)))}


def _bound_elsewhere(spec: MachineSpec, record) -> list[str]:
    """The wake events that are not the spec's own bindings: the record's and the duties'."""
    return [*(record.wake_on if record else []), *(record.reads_events if record else []), *spec.listens()]


def _known_types() -> set[str]:
    from .org import EVENT_TYPES
    return set(EVENT_TYPES)


def _ledger(spec: MachineSpec, line: str) -> str:
    text = spec.files.get("ledger.md") or "# Why each duty and binding exists\n\n"
    return text.rstrip("\n") + "\n" + line + "\n"


def _commit(project: Project, store: AgentStore, spec: MachineSpec, what: str, change: str, reason: str, by: str) -> dict:
    files = {f"{spec.dir}/machine.toml": render(spec),
             f"{spec.dir}/ledger.md": _ledger(spec, f"- {time.strftime('%Y-%m-%d %H:%M')} {what} ({by}): {reason}")}
    who = spec.name.replace("_", " ").title() if spec.name != SUPERVISOR else "Supervisor"
    message = f"{who}: {what} — {reason.splitlines()[0][:120]}\n\n{reason}\n\nMachine: {spec.name}\nChange: {change}\nBy: {by}\n"
    try:
        done = spec_git.commit(project, files, message, not_base=landing(store, SUPERVISOR))
    except spec_git.SpecGitError as e:
        raise StoreError(str(e)) from None
    if not done.get("commit"):
        return {"changed": False, **done}
    done.update(supervisor_pr.carried(project, store, done["commit"], title=f"{who}: {what}"[:120], reason=reason,
                                      verified=f"the binding tools' checks passed on {spec.name}'s spec, live from {spec_git.SIDE} at once",
                                      quote=supervisor_pr.owner_words(store)))
    return {**done, "revert": f"revert_change {done['commit']} (the Supervisor's), or the opposite tool"}


def _history(project: Project, name: str) -> list[dict]:
    try:
        return list(json.loads(state_bindings_path(project, name).read_text(encoding="utf-8")).get("history") or [])
    except (OSError, ValueError):
        return []


def _write_state(project: Project, spec: MachineSpec, history: list[dict]) -> None:
    p = state_bindings_path(project, spec.name)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"bindings": [b.to_dict() for b in spec.bindings], "unbound": [b.to_dict() for b in spec.unbound],
            "history": history[-200:]}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def _keep_designed(project: Project, spec: MachineSpec, what: str, change: str, event: str, reason: str, by: str,
                   before: dict | None = None) -> dict:
    """A designed machine's change, kept beside its spec in the state directory with its history;
    ``before`` is what to put back on a revert (its bindings and unbound, as dicts)."""
    old = before or {"bindings": [], "unbound": []}
    hist = _history(project, spec.name)
    rid = REVERT_PREFIX + secrets.token_hex(4)
    hist.append({"id": rid, "at": time.time(), "change": change, "event": event, "reason": reason, "by": by, "what": what,
                 "before": old})
    _write_state(project, spec, hist)
    return {"revert": rid, "where": "state", "commit": None,
            "note": (f"kept in {state_bindings_path(project, spec.name)}, never committed (a designed machine's). Its "
                     "declared receives are unchanged: a change to a confirmed machine's declaration is the owner's to "
                     "confirm (refine_machine records it as pending); the announcement shows what wakes it now")}


def snapshot(spec: MachineSpec) -> dict:
    return {"bindings": [b.to_dict() for b in spec.bindings], "unbound": [b.to_dict() for b in spec.unbound]}


def revert(project: Project, store: AgentStore, rid: str, reason: str) -> dict:
    """Undo a designed machine's binding change (``bind-…``) by putting back its bindings as they were
    before it, when nothing changed them since; never refused for the day's cap."""
    from .machine_spec import load_all
    for name, spec in load_all(project).items():
        if spec.root == ROOT:
            continue
        hist = _history(project, name)
        i = next((k for k, h in enumerate(hist) if h.get("id") == rid), None)
        if i is None:
            continue
        if any(h.get("change") != "revert" for h in hist[i + 1:]):
            raise StoreError(f"refused: {name}'s bindings changed again after {rid}; revert the later change first")
        was = snapshot(spec)
        before = hist[i].get("before") or {}
        spec.bindings = [Binding.from_dict(b) for b in before.get("bindings") or []]
        spec.unbound = [Binding.from_dict(b) for b in before.get("unbound") or []]
        done = _keep_designed(project, spec, f"revert {rid} ({hist[i].get('what')})", "revert", hist[i].get("event", ""),
                              reason, SUPERVISOR, before=was)
        store.set_agent_state(name, bindings_changed=True)
        store.events.publish("role.changed", {"machine": name, "change": "revert", "reverted": rid, "reason": reason,
                                              "by": SUPERVISOR}, source=SUPERVISOR, subject=f"machine:{name}")
        return {"ok": True, "changed": True, "agent": name, "change": "revert", **done}
    raise StoreError(f"no binding change {rid}; list_bindings with agent shows a designed machine's history")
