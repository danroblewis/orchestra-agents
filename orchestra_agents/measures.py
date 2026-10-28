"""The agents' measurements, served in the Prometheus text format and read by an instrument
(Orchestra's rule 7: the metrics store is filled by instruments; the host writes none itself, as it
is no machine). Named for what they measure:

- ``agents_working{role, machine}``: agent sessions of a role in a turn now, every measured role
  (the resident agents and the task system's roles the agents serve) even at 0, so the level is
  live from the first read and not from the first turn;
- ``agent_turns_started_total{role, machine}`` and ``agent_turns_finished_total{role, machine}``;
- ``agent_load{machine, signal}``: an agent's overload signals as its last review round measured
  them (``load.py``: ``prompt_tokens``, ``tools``, ``duties``, ``duty_tokens``, ``compactions_day``,
  ``utilisation_day``, ``wakeups_queued``, ``in_degree``, ``in_share``, ``subscriptions``,
  ``wait_p90_s``, ``usd_today``, ``cost_per_turn``, ...), one gauge per signal measured; and
  ``agent_spend_dollars_total{machine}``, what its turns cost as its harness reported it.

``machine`` is the agent the turn ran as, without its instance (``worker`` for ``worker/1``): the
measured system's own dimension, not who wrote the point. It equals ``role`` for every agent.

Where they come from: the counters are cumulative across restarts, kept in the agents' runtime
record (``metric_counts``) under the store's lock and moved in the same write as the session's
status, so a level and the counters read from one record balance. The plugin declares the
instrument (``Plugin.collectors``, web.py): ``orchestra run`` runs ``orchestra agents metrics``
every discovery step, which prints them from the record, and labels every point
``instrument=agents.turns`` (and ``project``). The same text is served at the plugin's ``/metrics``
(``/api/plugins/agents/metrics``), behind Orchestra's web login like every route.

Until 2026-10-27 the host wrote these points itself, each at the moment of a turn's start or end.
The series' names and their ``role`` and ``machine`` labels are kept; ``instrument`` is new. A
level is now sampled every discovery step, so a turn shorter than a step may not show in it; the
counters still count every turn."""

from __future__ import annotations

from orchestra.events import machine_and_instance

WORKING, STARTED, FINISHED = "agents_working", "agent_turns_started_total", "agent_turns_finished_total"
LOAD, SPEND = "agent_load", "agent_spend_dollars_total"
HELP = {WORKING: ("gauge", "agent sessions of a role in a turn now"),
        STARTED: ("counter", "agent turns started"),
        FINISHED: ("counter", "agent turns finished"),
        LOAD: ("gauge", "an agent's overload signals, as its last review round measured them"),
        SPEND: ("counter", "what an agent's turns cost, in dollars, as its harness reported it")}


def count(counts: dict, name: str, role: str, source: str) -> None:
    """Add one to a cumulative counter kept in ``counts`` (a dict saved in the runtime record, under
    the store's lock). ``source``: the instance the turn ran as."""
    labels = {"role": role, "machine": machine_and_instance(source)[0] or "unknown"}
    key = name + "|" + "|".join(f"{k}={v}" for k, v in sorted(labels.items()))
    counts[key] = counts.get(key, 0) + 1


def counters(counts: dict) -> dict[tuple[str, tuple], int]:
    """The kept counters by (series name, labels)."""
    out: dict[tuple[str, tuple], int] = {}
    for key, n in counts.items():
        name, *parts = key.split("|")
        if name in (STARTED, FINISHED):
            labels = tuple(sorted((k, v) for k, _, v in (p.partition("=") for p in parts)))
            out[(name, labels)] = out.get((name, labels), 0) + int(n)
    return out


def working(sessions: list[dict], roles: list[str]) -> dict[str, int]:
    """Each measured role's sessions in a turn now (``kind`` is the role, ``status`` running)."""
    out = dict.fromkeys(roles, 0)
    for s in sessions:
        if s.get("status") == "running" and s.get("kind"):
            out[s["kind"]] = out.get(s["kind"], 0) + 1
    return out


def _line(name: str, labels: tuple | dict, value: float) -> str:
    items = labels.items() if isinstance(labels, dict) else labels
    inner = ",".join(f'{k}="{_escape(str(v))}"' for k, v in items)
    return f"{name}{{{inner}}} {value:g}" if inner else f"{name} {value:g}"


def _escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def exposition(runtime: dict, roles: list[str]) -> str:
    """The measurements as Prometheus text, from the agents' runtime record (``runtime``) and the
    measured roles (``roles``)."""
    series: dict[str, list[str]] = {WORKING: [_line(WORKING, {"role": r, "machine": r}, n)
                                              for r, n in sorted(working(list(runtime.get("sessions", {}).values()), roles).items())]}
    for (name, labels), n in sorted(counters(runtime.get("metric_counts") or {}).items()):
        series.setdefault(name, []).append(_line(name, labels, n))
    for agent, st in sorted((runtime.get("agent_state") or {}).items()):
        for k, v in sorted((st.get("load") or {}).items()):
            if k != "at" and isinstance(v, int | float) and not isinstance(v, bool):
                series.setdefault(LOAD, []).append(_line(LOAD, {"machine": agent, "signal": k}, v))
        if st.get("spend_total"):                                # cumulative, never trimmed (budget.py); none until a harness reports a cost
            series.setdefault(SPEND, []).append(_line(SPEND, {"machine": agent}, float(st["spend_total"])))
    lines: list[str] = []
    for name, rows in series.items():
        kind, text = HELP[name]
        lines += [f"# HELP {name} {text}", f"# TYPE {name} {kind}", *rows]
    return "\n".join(lines) + "\n"
