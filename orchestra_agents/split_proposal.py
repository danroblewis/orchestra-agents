"""The self-decomposition hook: when the Supervisor's overload signals cross (``load.py``), its
review round writes a **proposal** to split it into its spec, and the Supervisor tells the owner
(the owner, 2026-10-28: machines should be capable of decomposing themselves; each review asks
"should I be two machines?"; this phase records the proposal only, the split comes later).

The proposal is judged by cohesion and coupling (the owner's rule): duties that use their own
tools and wake on their own events cluster, and a cluster is a candidate machine; duties sharing
the same wake-ups or tools stay together. A split is for **load**, **privilege** (the groups only
the Supervisor may hold, ``setup``, ``roles``, ``patch``, stay with it) or **engagement** (a duty
woken by events or a schedule, apart from the owner's chat). When only the busy signal crossed,
the proposal says scaling out may fit better than splitting.

Written as ``proposals/split-<date>.md`` in ``.orchestra/machines/supervisor/``, one commit (not
counted against the day's cap: it changes nothing the agent runs), at most once a week unless
other signals cross."""

from __future__ import annotations

import time

from . import spec_git
from .machine_spec import MachineSpec

PRIVILEGED = ("setup", "roles", "patch")
READING = ("observe", "model", "predict", "events", "guides")      # shared by every duty: no coupling
WEEK = 7 * 86_400.0


def clusters(spec: MachineSpec) -> list[list[str]]:
    """The Supervisor's own duties grouped by shared wake-up types and shared non-privileged tool
    groups (connected components); a built-in duty stays with the Supervisor."""
    own = [d for d in spec.duties if not d.builtin]
    parent = {d.name: d.name for d in own}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, a in enumerate(own):
        for b in own[i + 1:]:
            wakes = {w.split(":")[0] for w in a.listens} & {w.split(":")[0] for w in b.listens}
            tools = (set(a.tools) & set(b.tools)) - set(PRIVILEGED) - set(READING)
            if wakes or tools:
                parent[find(a.name)] = find(b.name)
    groups: dict[str, list[str]] = {}
    for d in own:
        groups.setdefault(find(d.name), []).append(d.name)
    return sorted(groups.values(), key=lambda g: (-len(g), g))


def text(spec: MachineSpec, values: dict, hit: list[dict], at: float) -> str:
    rows = "\n".join(f"| {h['signal']} | {h['value']} | {h['threshold']} | {h['why']} |" for h in hit)
    lines = [f"# Should the Supervisor be two machines? ({time.strftime('%Y-%m-%d %H:%M', time.localtime(at))})", "",
             ("Written by the Supervisor's review round because these overload signals crossed. A proposal only: nothing "
              "is split until the owner agrees and the split tool exists."), "",
             "| signal | value | threshold | what it means |", "|---|---|---|---|", rows, "",
             "## Its duties", ""]
    for d in spec.duties:
        if d.builtin:
            lines.append(f"- `{d.name}` (built in, stays): {d.about}")
        else:
            lines.append(f"- `{d.name}`: {d.about or '(no line)'}; wakes on {', '.join(d.listens) or 'the round only'}; "
                         f"tools {', '.join(d.tools) or 'any'}")
    lines += ["", "## Candidate machines (cohesion: shared wake-ups and tools)", ""]
    for g in clusters(spec):
        ds = [spec.duty(n) for n in g]
        tools = sorted({t for d in ds for t in d.tools} - set(PRIVILEGED))
        privileged = sorted({t for d in ds for t in d.tools} & set(PRIVILEGED))
        event_driven = any(d.listens for d in ds)
        lines.append(f"- **{' + '.join(g)}**: tools {', '.join(tools) or 'reading only'}"
                     + (f"; needs {', '.join(privileged)}, which only the Supervisor holds: the new machine would ask the "
                        "Supervisor for those acts (escalate), or this cluster stays" if privileged else "")
                     + ("; woken by events, apart from the owner's chat (engagement)" if event_driven else "")
                     + "; model: haiku by default, sonnet if it reads code or long logs")
    only_busy = {h["signal"] for h in hit} <= {"utilisation_day"}
    lines += ["", "## Recommendation", "",
              ("Only the busy signal crossed: the backlog may need another instance of the same duties (scaling out) rather "
               "than a split." if only_busy else
               "Hand the largest event-driven cluster without privileged tools to a new machine; the Supervisor keeps the "
               "owner's chat, setup and its privileged acts. Predict that its load falls and the duties' outcomes do not "
               "change (the null test); merge back if the new machine idles or only relays."),
              "", "## All signals measured", "", "```", *(f"{k}: {v}" for k, v in sorted(values.items())), "```", ""]
    return "\n".join(lines)


def maybe_write(project, store, spec: MachineSpec, values: dict, hit: list[dict], now: float | None = None) -> dict | None:
    """Write a proposal when signals crossed and none was written this week for the same signals
    (or fewer). Returns ``{path, commit, where, signals}``, or None."""
    now = now or time.time()
    if not hit:
        return None
    signals = sorted(h["signal"] for h in hit)
    last = store.agent_state(spec.name).get("split_proposal") or {}
    if last.get("at") and now - last["at"] < WEEK and set(signals) <= set(last.get("signals") or []):
        return None
    path = f"{spec.dir}/proposals/split-{time.strftime('%Y-%m-%d', time.localtime(now))}.md"
    try:
        done = spec_git.commit(project, {path: text(spec, values, hit, now)},
                               f"Supervisor: a split proposal — {', '.join(signals)} crossed\n\nWritten by the review round; nothing "
                               f"is split.\n\nMachine: {spec.name}\nChange: proposal\n", allow_plain=True)
    except spec_git.SpecGitError:
        return None
    rec = {"at": now, "signals": signals, "path": path, "commit": done.get("commit"), "where": done.get("where")}
    store.set_agent_state(spec.name, split_proposal=rec)
    return rec
