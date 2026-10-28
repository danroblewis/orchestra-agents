"""The Supervisor's duty tools: it grows its own spec (``machine_spec.py``), one reasoned commit
at a time (docs/proposals/agent-first-machines.md §4.2, §6; the owner, 2026-10-28).

``list_duties``, ``add_duty``, ``edit_duty``, ``remove_duty`` and ``revert_change``, in the
``roles`` group and the Supervisor's alone (checked here, whatever groups a custom role holds).
Every change:

- needs a reason in a sentence: the owner's words, or the review round's finding;
- is refused past ``[agents] spec_changes_per_day`` (the owner's cap, default 5) in the last 24
  hours, so eras stay long enough to judge anything; a revert is never refused for the cap;
- changes only ``.orchestra/machines/supervisor/``: never the fixed core, which is the plugin's,
  and never a built-in duty, whose text is the plugin's too;
- never gives the Supervisor a tool: a duty may name only tool groups it holds;
- is one commit (``spec_git``): on the base branch when the checkout is clean there and the
  gate is not landing, else on the side branch ``orchestra/supervisor``; with a line in
  ``ledger.md``; announced as ``role.changed`` (subject ``machine:supervisor``, the commit, the
  reason), which begins an era where ``[discovery] eras_from`` names it;
- restarts the Supervisor's resident session after its turn, so its next turn runs the new
  prompt (the conversation is kept)."""

from __future__ import annotations

import re
import time
from typing import Any

from . import spec_git
from .config import Project
from .gate_busy import landing
from .machine_spec import NAME, Duty, MachineSpec, SpecError, lint, load, parse, render
from .secret_shapes import secret_in
from .store import AgentStore, StoreError

COMMANDS = frozenset({"list_duties", "add_duty", "edit_duty", "remove_duty", "revert_change"})
CHANGES = COMMANDS - {"list_duties"}
MACHINE = "supervisor"
TEXT_MAX = 12_000
DAY = 86_400.0
LISTEN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+(:\S+)?$")


def changes_today(store: AgentStore, now: float | None = None) -> list[float]:
    now = now or time.time()
    return [t for t in store.agent_state(MACHINE).get("spec_changes") or [] if now - t < DAY]


def _groups(project: Project) -> tuple[str, ...]:
    from .access import granted_groups
    return granted_groups(project, MACHINE) or ()


def _known_types() -> set[str]:
    from .org import EVENT_TYPES
    return set(EVENT_TYPES)


def handle(project: Project, store: AgentStore, command: str, a: dict[str, Any], role: str) -> dict:
    if role != MACHINE:
        raise StoreError(f"refused {command}: a machine's spec is changed only by the Supervisor (the caller is {role or 'none'!r})")
    machine = (a.get("machine") or MACHINE).strip()
    if machine != MACHINE:
        raise StoreError("only the Supervisor's own spec is changed with these tools for now (splitting into new machines comes later)")
    spec = load(project, MACHINE)
    if spec is None:
        raise StoreError("the Supervisor has no spec yet (.orchestra/machines/supervisor/); the agents' host writes it when it starts")
    if command == "list_duties":
        return _list(project, store, spec)
    reason = (a.get("reason") or "").strip()
    if len(reason) < 10:
        raise StoreError("give the reason in a sentence: the owner's words, or what the review round found. It goes in the commit")
    if command == "revert_change":
        return _revert(project, store, a, reason)
    cap = project.config.spec_changes_per_day
    if len(changes_today(store)) >= cap:
        raise StoreError(f"refused: {cap} changes to your spec in the last 24 hours already ([agents] spec_changes_per_day, the "
                         "owner's). Tell the owner what you would change and why; a revert is still allowed")
    name = (a.get("name") or "").strip()
    if not NAME.match(name):
        raise StoreError("a duty's name is lowercase letters, digits and underscores, 2 to 41 characters (keep_gate_moving)")
    old = spec.duty(name)
    if command == "add_duty":
        if old is not None:
            raise StoreError(f"there is already a duty {name!r}; change it with edit_duty")
        duty = Duty(name=name, prompt=f"duties/{name}.md", added_by=MACHINE, added_at=time.strftime("%Y-%m-%d"), reason=reason)
        return _write(project, store, spec, duty, a, reason, "add", new=True)
    if old is None:
        raise StoreError(f"no duty {name!r}; list_duties shows them")
    if old.builtin:
        raise StoreError(f"{name!r} is a built-in duty: its text ships with orchestra-agents and is not yours to change. Add a "
                         "duty of your own beside it")
    if command == "edit_duty":
        return _write(project, store, spec, old, a, reason, "edit", new=False)
    spec.duties = [d for d in spec.duties if d.name != name]
    files = {f"{spec.dir}/machine.toml": render(spec), f"{spec.dir}/{old.prompt or f'duties/{name}.md'}": None}
    return _commit(project, store, spec, files, f"remove duty {name}", name, "remove", reason)


def _write(project: Project, store: AgentStore, spec: MachineSpec, duty: Duty, a: dict, reason: str, change: str, new: bool) -> dict:
    text = a.get("text")
    if new and not (text or "").strip():
        raise StoreError("write the duty's text: what it keeps true, what wakes it, the fixes it may make, what it must never "
                         "do, and how success is measured")
    if text is not None:
        if len(text) > TEXT_MAX:
            raise StoreError(f"a duty's text is at most {TEXT_MAX} characters; split it into two duties")
        if why := secret_in(text):
            raise StoreError(f"refused: the text looks like it holds a secret ({why})")
    if "about" in a:
        duty.about = str(a.get("about") or "").strip()[:200]
    if "listens" in a:
        listens = [str(w).strip() for w in a.get("listens") or [] if str(w).strip()]
        bad = [w for w in listens if not LISTEN.match(w)]
        if bad:
            raise StoreError(f"not an event type (type or type:filter, e.g. orchestra.model.stalled:stock:queue): {bad}")
        duty.listens = listens
    if "tools" in a:
        tools = [str(t).strip() for t in a.get("tools") or [] if str(t).strip()]
        held = _groups(project)
        missing = [t for t in tools if t not in held]
        if missing:
            raise StoreError(f"refused: you do not hold the tool groups {missing} (you hold {', '.join(held)}); a duty never "
                             "grants a tool. Ask the owner")
        duty.tools = tools
    if new:
        spec.duties.append(duty)
    files = {f"{spec.dir}/machine.toml": render(spec)}
    if text is not None:
        files[f"{spec.dir}/{duty.prompt or f'duties/{duty.name}.md'}"] = text.rstrip() + "\n"
    return _commit(project, store, spec, files, f"{change} duty {duty.name}", duty.name, change, reason)


def _ledger(spec: MachineSpec, line: str) -> str:
    text = spec.files.get("ledger.md") or "# Why each duty exists\n\n"
    return text.rstrip("\n") + "\n" + line + "\n"


def _commit(project: Project, store: AgentStore, spec: MachineSpec, files: dict, what: str, duty: str, change: str,
            reason: str, count: bool = True) -> dict:
    files[f"{spec.dir}/ledger.md"] = _ledger(spec, f"- {time.strftime('%Y-%m-%d %H:%M')} {what} (supervisor): {reason}")
    message = f"Supervisor: {what} — {reason.splitlines()[0][:120]}\n\n{reason}\n\nMachine: {spec.name}\nChange: {change}\n"
    try:
        done = spec_git.commit(project, files, message, not_base=landing(store, MACHINE))
    except spec_git.SpecGitError as e:
        raise StoreError(str(e)) from None
    if not done.get("commit"):
        return {"ok": True, "changed": False, **done}
    if count:
        store.set_agent_state(MACHINE, spec_changes=[*changes_today(store), time.time()])
    store.events.publish("role.changed", {"machine": spec.name, "duty": duty, "change": change, "reason": reason,
                                          "commit": done["commit"], "where": done["where"], "by": MACHINE},
                         subject=f"machine:{spec.name}")
    store.set_agent_state(MACHINE, restart_requested=True)
    left = project.config.spec_changes_per_day - len(changes_today(store))
    return {"ok": True, "changed": True, "duty": duty, "change": change, **done,
            "applies": "your session starts again after this turn with the new prompt (the conversation is kept)",
            "changes_left_today": max(0, left),
            "next": "Tell the owner the duty and its commit. When the duty is meant to move something, predict it (predict_fix)."}


def _revert(project: Project, store: AgentStore, a: dict, reason: str) -> dict:
    """Undo one spec commit as a new one, by what it did to each duty (added, edited, removed),
    applied to the spec as it is now, so later changes to other duties stay. Another file it
    touched (``role.md``, a proposal) is put back only when nothing changed it since."""
    sha = (a.get("commit") or "").strip()
    if not sha:
        raise StoreError("name the commit to revert (list_duties' history)")
    try:
        before, after, touched, subject = spec_git.commit_of(project, sha)
    except spec_git.SpecGitError as e:
        raise StoreError(str(e)) from None
    spec = load(project, MACHINE)
    if any(not p.startswith(spec.dir + "/") for p in touched):
        raise StoreError(f"refused: {sha} changed another machine's spec")
    rel = lambda files: {p[len(spec.dir) + 1:]: t for p, t in files.items() if p.startswith(spec.dir + "/")}
    b_files, a_files = rel(before), rel(after)
    b_spec = parse(MACHINE, b_files) if "machine.toml" in b_files else MachineSpec(name=MACHINE)
    a_spec = parse(MACHINE, a_files) if "machine.toml" in a_files else MachineSpec(name=MACHINE)
    files: dict[str, str | None] = {}
    for name in dict.fromkeys([d.name for d in a_spec.duties] + [d.name for d in b_spec.duties]):
        was, now_ = b_spec.duty(name), a_spec.duty(name)
        if was is not None and now_ is not None and was.to_dict() == now_.to_dict() and b_spec.duty_text(was) == a_spec.duty_text(now_):
            continue
        cur = spec.duty(name)
        if was is None:                                           # it added the duty: remove it
            spec.duties = [d for d in spec.duties if d.name != name]
            files[f"{spec.dir}/{(cur or now_).prompt or f'duties/{name}.md'}"] = None
        else:                                                     # it changed or removed it: as it was
            spec.duties = [was if d.name == name else d for d in spec.duties] if cur else [*spec.duties, was]
            if not was.builtin:
                files[f"{spec.dir}/{was.prompt or f'duties/{name}.md'}"] = b_spec.duty_text(was)
    for k in ("description", "engagement", "model", "review_every"):
        if getattr(b_spec, k) != getattr(a_spec, k):
            setattr(spec, k, getattr(b_spec, k))
    for path in touched:
        inner = path[len(spec.dir) + 1:]
        if inner in ("machine.toml", "ledger.md") or path in files or inner.startswith("duties/"):
            continue
        if spec.files.get(inner) != a_files.get(inner):
            raise StoreError(f"refused: {inner} changed again after {sha[:10]}; revert the later change first")
        files[path] = b_files.get(inner)
    files[f"{spec.dir}/machine.toml"] = render(spec)
    return _commit(project, store, spec, files, f"revert {sha[:10]} ({subject[:60]})", "", "revert", reason, count=False)


def _list(project: Project, store: AgentStore, spec: MachineSpec) -> dict:
    duties = [{"name": d.name, "about": d.about, "builtin": d.builtin, "listens": d.listens, "tools": d.tools,
               "added_by": d.added_by, "reason": d.reason, **({"text": spec.duty_text(d)} if not d.builtin else {})}
              for d in spec.duties]
    try:
        notes = lint(spec, _groups(project), _known_types())
    except SpecError as e:
        notes = [str(e)]
    return {"ok": True, "machine": spec.name, "model": spec.model, "review_every": spec.review_every, "duties": duties,
            "role": spec.role_text, "history": spec_git.history(project, 15), "pending_on_side_branch": bool(spec_git.side_pending(project)),
            "changes_today": len(changes_today(store)), "changes_per_day": project.config.spec_changes_per_day, "lint": notes}
