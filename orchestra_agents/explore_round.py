"""The Supervisor's explore round, a standing duty on a schedule (Orchestra's
``docs/proposals/metric-explorer.md``, "The loop"; the owner, 2026-11-04: after a short discussion,
"whatever happens after that should be an automated exploration and set up by the supervisor").

Every ``[agents] explore_every`` (default 1 h; 0 turns it off, never under ``FLOOR_S``), once the
project declares at least one ``[[sources]]`` (it is then set up), the host checks without a model call:

1. the experiment log (``ModelStore.explorer()``) and the run record: when the last experiment is
   still to be judged and no discovery run has ended since it was recorded, there is nothing to
   judge and nothing new may be tried (one change at a time): no turn;
2. the day's cap for the Supervisor's initiative (``budget.over``): past it, no turn, said in the
   record;
3. otherwise a wake-up, ``[Explore round] ...``, a full turn: the sources with their ``match`` and
   ``explore``, the experiment to judge first, and the steps of one round (the prompt's
   "Exploring the stores"): read the state, look both ways, probe within ``explore``
   (``explore_source``), propose one experiment with its expected effect (``log_experiment``),
   apply it on the standing rule (narrowing ``match``, ``split``, ``kinds``, ``names``; code holds
   it, ``source_limits.check_scopes``) or ask the owner in one sentence for anything that widens
   ``match``, and judge the previous one (``judge_experiment``).

The record is the Supervisor's agent state ``explore``: when it ran, whether it woke the Supervisor
or why not, and the next round's time (``next_explore_at``). ``digest_line`` puts the rounds and the
experiments since a time in the "what changed since" digest."""

from __future__ import annotations

import time

from . import budget

MACHINE = "supervisor"
MARK = "[Explore round]"
FLOOR_S = 300.0


def _sources(project) -> list[dict]:
    return [e for e in project.orchestra.config.raw.get("sources") or [] if isinstance(e, dict) and e.get("name")]


def _log(project):
    from orchestra.sd.store import ModelStore
    return ModelStore.for_state(project.orchestra.state_dir)


def _discoveries(store) -> list[dict]:
    return [r for r in store.runs().recent(0) if r.get("kind") == "discovery"]


def _list(v) -> list[str]:
    return [v] if isinstance(v, str) else list(v or [])


def reason(sources: list[dict], pending: dict | None, since: int, last: dict | None) -> str:
    lines = [f"{MARK} One round of your explore duty (\"Exploring the stores\" in your prompt), within the caps."]
    for e in sources:
        lines.append(f"- source {e['name']}: match {', '.join(_list(e.get('match'))) or '(none)'}; explore "
                     f"{', '.join(_list(e.get('explore'))) or '(none: its match)'}")
    if pending:
        lines.append(f"First judge experiment {pending['id']} ({since} discovery run(s) since it was recorded): change "
                     f"{pending.get('change')}, expected: {pending.get('expect')}. judge_experiment kept, reverted (undo it "
                     "first) or inconclusive, by what the runs show.")
    elif last:
        lines.append(f"The last experiment, {last['id']}, was judged {last.get('verdict')}: {last.get('why') or ''}")
    lines.append("Then: read_experiments, read_discovery_runs and get_model's left_out; look both ways (too strict, too "
                 "loose); explore_source within explore, bounded; log_experiment one change with its expected effect; "
                 "apply it with set_setting key sources when it narrows match or changes split, kinds or names; ask the "
                 "owner in one sentence when it widens match (never write explore). Nothing worth trying: say so in a line.")
    return "\n".join(lines)


class ExploreRound:
    def __init__(self, host):
        self.host = host
        self.started = time.time()

    def every(self) -> float:
        v = float(getattr(self.host.cfg, "explore_every", 0.0) or 0.0)
        return 0.0 if v <= 0 else max(FLOOR_S, v)

    def tick(self, now: float | None = None) -> dict | None:
        """Run a round when one is due; what it did, or None."""
        h = self.host
        now = now or time.time()
        every = self.every()
        if not every or h.agents.get(MACHINE) is None:
            return None
        st = h.store.agent_state(MACHINE)
        if st.get("paused") or st.get("deleted"):
            return None
        sources = _sources(h.project)
        if not sources:
            return None
        last = (st.get("explore") or {}).get("at") or self.started
        if st.get("next_explore_at") != last + every:
            h.store.set_agent_state(MACHINE, next_explore_at=last + every)
        if now - last < every:
            return None
        return self.run(sources, now, every)

    def run(self, sources: list[dict], now: float, every: float) -> dict:
        h = self.host
        ms = _log(h.project)
        entries = ms.explorer().entries()
        pending = next((e for e in reversed(entries) if not e.get("verdict")), None)
        judged = next((e for e in reversed(entries) if e.get("verdict")), None)
        since = len([r for r in _discoveries(ms) if pending and (r.get("ended") or 0) > pending["t"]])
        record = {"at": now, "sources": [e["name"] for e in sources], "pending": pending["id"] if pending else None}
        if pending and not since:
            record.update(woke=False, skipped=f"experiment {pending['id']} waits for a discovery run to judge it")
        elif why := budget.over(h.store, h.cfg, now, priced=(h.harness, h.model_of(MACHINE))):
            record.update(woke=False, skipped=why)
        else:
            h.store.add_wakeup(MACHINE, reason(sources, pending, since, judged))
            record["woke"] = True
        rounds = [x for x in (h.store.agent_state(MACHINE).get("explore_rounds") or []) if now - x.get("at", 0) < 7 * 86_400][-50:]
        h.store.set_agent_state(MACHINE, explore=record, explore_rounds=[*rounds, record], next_explore_at=now + every)
        h.log(f"explore round: {'the Supervisor is woken' if record['woke'] else 'no turn: ' + record['skipped']}")
        return record


def digest_line(project, store, start: float) -> str | None:
    """The explore rounds and the experiments since ``start``, in a line; None when neither."""
    rounds = [r for r in store.agent_state(MACHINE).get("explore_rounds") or [] if r.get("at", 0) >= start]
    try:
        entries = _log(project).explorer().entries()
    except Exception:  # noqa: BLE001 - the log unreadable: the rounds alone
        entries = []
    new = [e for e in entries if (e.get("t") or 0) >= start]
    judged = [e for e in entries if e.get("verdict") and (e.get("judged") or 0) >= start]
    if not rounds and not new and not judged:
        return None
    woke = sum(1 for r in rounds if r.get("woke"))
    parts = [f"Exploring: {len(rounds)} round(s), {woke} with a turn"]
    if new:
        parts.append(f"{len(new)} experiment(s) recorded")
    if judged:
        verdicts = {}
        for e in judged:
            verdicts[e["verdict"]] = verdicts.get(e["verdict"], 0) + 1
        parts.append("judged " + ", ".join(f"{n} {v}" for v, n in sorted(verdicts.items())))
    latest = (new or judged)[-1] if (new or judged) else None
    line = "; ".join(parts) + "."
    if latest:
        eff = ", ".join(f"{k} {v:+g}" for k, v in (latest.get("effect") or {}).items())
        line += (f" Latest {latest['id']}: {latest.get('change')} expecting \"{latest.get('expect')}\""
                 + (f", {latest['verdict']}" + (f" ({eff})" if eff else "") if latest.get("verdict") else ", to be judged") + ".")
    skipped = next((r["skipped"] for r in reversed(rounds) if r.get("skipped")), None)
    if skipped and not (rounds[-1].get("woke")):
        line += f" Last round had no turn: {skipped}."
    return line
