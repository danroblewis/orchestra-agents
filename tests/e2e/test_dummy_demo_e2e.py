"""Goal G1's acceptance: the agents' demo runs end to end on the dummy harness (`demo/run.py
--dummy`), for free. Everything runs as the demo runs it, through a real `orchestra run` and
`orchestra web`, with the real task system and its gate; only the agents are scripted
(demo/dummy.toml). With no API key in the environment, a `claude`, `pi` and `codex` on the PATH
that only note that they ran, and every Python process unable to reach beyond this machine
(no_network/sitecustomize.py), it checks that:

- a ticket is offered, accepted, worked (a real commit), reviewed, gated and landed;
- a second ticket is rejected once by the reviewer, reworked by the same worker instance and
  session, and lands, the work passing directly between them: the hand-offs Orchestra's event map
  computes are planner → coordinator → worker → reviewer → worker → reviewer → merge_gate, the
  task system only where it acted (the first offer, as the coordinator's dispatch);
- the Supervisor answers a chat turn through the web's /supervisor;
- no model was run and nothing tried the network.

And what the owner sees (``test_what_the_owner_sees``), after more tickets, five of them rejected
once: the task system's scheduler is nowhere (no event is its, it is no machine, no edge or
hand-off names it), every hand-off of a reworked ticket is one machine to the next with nothing in
between (planner → coordinator → worker → reviewer → worker → reviewer → merge_gate), one
coordinator machine is the agent and the task system's dispatch, discovery hypothesises no
machine on a ticket flow for acts it did not take, and the ticket measurements arrive through the
task system's instrument."""

import math
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest
from conftest import free_port
from orchestra.web.machines import MachineIndex
from test_demo_e2e import AGENTS, TASKS, all_events, chat, git, reported_by, settled, states

from demo.run import Web, make_project, plan, start, stop, wait_for, wait_until, web_token

# Extra tickets for the owner's view (rules added to the target's dummy.toml, matching only their
# titles): the reviewer rejects a "Rework" ticket's first round and approves the rest. Each turn
# takes a few seconds, more than a discovery step (5 s), as real work would: a counter whose
# moves all fall in one step cannot be told from another's, and attribution would be noise.
REWORK_RULE = """

[[rule]]
role = "reviewer"
prompt = "Rework item"
turn = 1
steps = [{ sleep = 6 }, { tool = "report_review", args = { verdict = "REJECTED", summary = "Not yet.", feedback = "1. Do it again." } }]

[[rule]]
role = "reviewer"
prompt = " item "
steps = [{ sleep = 6 }, { tool = "report_review", args = { verdict = "APPROVED", summary = "Good." } }]

[[rule]]
role = "worker"
prompt = " item "
checkout = true
steps = [
  { sleep = 6 },
  { write = "work/{ticket}.md", text = "Work on {ticket}, turn {turn}. ", append = true },
  { commit = "{ticket}: work" },
  { tool = "report_ticket", args = { state = "READY_FOR_REVIEW", summary = "Wrote work/{ticket}.md." } },
]
"""
EXTRA = [f"Rework item {i}" for i in range(5)] + [f"Extra item {i}" for i in range(3)]

# The Supervisor hands the human's request to the Coordinator (the built-in rules), which plans it
# into a milestone and a ticket; the Supervisor says so when it lands (the host wakes it:
# landings.py). Put before the demo's own Supervisor rule.
SUPERVISOR_RULES = """# (the tests' rules, matching only their own prompts)
[[rule]]
role = "supervisor"
prompt = "made for the human.s request"
reply = "Your note ticket landed."

"""

GUARD = Path(__file__).parent / "no_network"
KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
        "GEMINI_API_KEY", "OPENROUTER_API_KEY")


def handoffs(events: list[dict], subject: str) -> list[tuple[str, str | None, str]]:
    """The hand-offs Orchestra's Machines page and event map compute from the bus (its
    ``MachineIndex``, behind ``/api/machines``), in the order they arose, for the events about
    ``subject``: (from machine, via event type, to machine). The index reads the bus as it grows."""
    seen: list[dict] = []

    class Grown:                                     # the bus as it stood after each event
        def end(self):
            return len(seen)

        def read(self, since=0, limit=500):
            return seen[since:][-limit:], len(seen)

    idx, out = MachineIndex(Grown()), []
    for ev in events:
        before = dict(idx.handoffs)
        seen.append(ev)
        idx.update()
        if ev.get("subject") == subject:
            out += [k for k, n in idx.handoffs.items() if n > before.get(k, 0)]
    return out


@pytest.fixture
def free_demo(tmp_path, monkeypatch):
    home, logs, bins = tmp_path / "home", tmp_path / "logs", tmp_path / "bin"
    logs.mkdir()
    bins.mkdir()
    for product in ("claude", "pi", "codex"):          # a model product that runs says so and fails
        b = bins / product
        b.write_text(f'#!/bin/sh\necho "{product} $*" >> {logs / "products.log"}\nexit 1\n')
        b.chmod(b.stat().st_mode | stat.S_IEXEC)
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    env = {"ORCHESTRATOR_HOME": str(home), "NO_NETWORK_LOG": str(logs / "network.log"),
           "PYTHONPATH": os.pathsep.join(filter(None, [str(GUARD), os.environ.get("PYTHONPATH")])),
           "PATH": os.pathsep.join([str(bins), os.environ.get("PATH", "")]),
           "CLAUDE_BINARY": str(bins / "claude"), "PI_BINARY": str(bins / "pi"), "CODEX_BINARY": str(bins / "codex")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    target = make_project(tmp_path / "project", dummy=True)
    script = target / "dummy.toml"
    script.write_text(SUPERVISOR_RULES + script.read_text(encoding="utf-8") + REWORK_RULE, encoding="utf-8")   # only "Rework" tickets match it
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-qam", "a rule for rework tickets"], cwd=target, check=True)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    procs = start(target, port, env=env)
    try:
        web = Web(url, web_token(target, env))
        wait_for(web, procs)
        yield web, target, logs
    finally:
        stop(procs)


def test_the_demo_runs_end_to_end_on_dummy_agents_with_no_model_and_no_network(free_demo):
    web, target, logs = free_demo
    assert 'harness = "dummy"' in (target / "orchestra.toml").read_text()
    ids = plan(target)
    assert ids == ["DEMO-1", "DEMO-2", "DEMO-3", "DEMO-4"]
    wait_until("the tickets", lambda: settled(web), timeout_s=300)
    assert states(web) == dict.fromkeys(ids, "DONE")

    events = all_events(web)
    by = reported_by(web)
    verdicts = {}
    for e in events:
        if e["type"] in ("review.approved", "changes.requested") and e["source"].startswith("reviewer/"):
            verdicts.setdefault(e["subject"], []).append("APPROVED" if e["type"] == "review.approved" else "REJECTED")

    # DEMO-4, one round: offered, accepted by a dummy worker, worked with a real commit, reviewed,
    # gated and landed on main, where the collector it wrote now runs
    assert len(by["DEMO-4"]) == 2 and by["DEMO-4"][0] in ("worker/1", "worker/2") and by["DEMO-4"][1].startswith("reviewer/"), by["DEMO-4"]
    assert verdicts["DEMO-4"] == ["APPROVED"]
    offered = [e for e in events if e["type"] == "task.assigned" and e["subject"] == "DEMO-4"]
    accepted = [e for e in events if e["type"] == "task.accepted" and e["subject"] == "DEMO-4"]
    asked = [e for e in events if e["type"] == "review.requested" and e["subject"] == "DEMO-4"]
    assert {e["data"]["role"] for e in offered} == {"worker"} and by["DEMO-4"][1] in {e["source"] for e in accepted}   # the queue offered the work
    assert {e["source"] for e in offered} == {"coordinator/dispatch"}                       # as the coordinator's dispatch
    assert [e["source"] for e in asked] == [by["DEMO-4"][0]]                                 # the worker itself asked for the review
    assert "DEMO-4: measure the repository" in git(target, "log", "--format=%s", "main")
    assert "machines/repo_stats.sh" in git(target, "ls-tree", "-r", "--name-only", "main")
    assert "[[collect]]" in git(target, "show", "main:orchestra.toml")
    assert web.get(f"{TASKS}/tickets/DEMO-4")["notes"][-1]["text"].startswith("landed on main as ")

    # DEMO-3, two rounds: rejected once, reworked in the same worker session, approved, landed
    assert verdicts["DEMO-3"] == ["REJECTED", "APPROVED"]
    assert len(by["DEMO-3"]) == 4 and all(r.startswith("reviewer/") for r in by["DEMO-3"][1::2])   # any reviewer may take a review
    assert by["DEMO-3"][0] == by["DEMO-3"][2] and by["DEMO-3"][0].startswith("worker/")     # reworked by the same worker instance
    back = [e for e in events if e["type"] == "changes.requested" and e["subject"] == "DEMO-3"]
    assert [e["data"]["to"] for e in back] == [by["DEMO-3"][0]]                             # the rejection went to it, not to a queue
    assert handoffs(events, "DEMO-3") == [("planner", "ticket.created", "coordinator"), ("coordinator", "task.assigned", "worker"),
                                          ("worker", "review.requested", "reviewer"),
                                          ("reviewer", "changes.requested", "worker"), ("worker", "review.requested", "reviewer"),
                                          ("reviewer", "review.approved", "merge_gate")]
    assert "def test_longest_first" in git(target, "show", "main:tests/test_line_counts.py")
    assert {"DEMO-3: count the lines of the Python files", "DEMO-3: test that the longest file comes first"} <= \
        set(git(target, "log", "--format=%s", "main").splitlines())
    worker = web.get(f"{AGENTS}/sessions/worker-DEMO-3/transcript")
    assert worker["session"]["harness"] == "dummy" and worker["turns"] == 2

    # the Supervisor answers through the web's /supervisor, with a tool call on the way
    lines = chat(web, "What is going on?")
    assert lines[-1]["type"] == "done" and not lines[-1].get("error")
    assert any(ln["type"] == "tool_start" and ln.get("name") == "project_status" for ln in lines)
    assert any(ln["type"] == "assistant_end" and ln["text"].startswith("I read the project's status") for ln in lines)

    # no model ran, and nothing tried the network; the guard was in the agents' own processes
    assert not (logs / "products.log").exists(), (logs / "products.log").read_text()
    network = (logs / "network.log").read_text().splitlines()
    assert not [ln for ln in network if " refused " in ln], [ln for ln in network if " refused " in ln]
    assert any("orchestra_agents.harness.dummy_agent" in ln for ln in network)
    assert any("-m orchestra tasks run" in ln for ln in network) and any("orchestra web" in ln or "orchestra run" in ln or "-m orchestra" in ln for ln in network)


def flow_hypotheses(target: Path) -> dict[tuple[str, str, str], list[tuple[str, float]]]:
    """Discovery's machine hypotheses (orchestra.sd.attribute, as discovery runs it) on each ticket
    transition counter in the project's metrics: {(from, to, by): [(machine, confidence)]}."""
    from orchestra.config import load_project
    from orchestra.metrics import for_project
    from orchestra.sd.attribute import associate, candidates
    from orchestra.sd.series import sample, sum_instances
    from orchestra.sources.catalogue import Catalogue

    project = load_project(target)
    step = project.config.discovery_step
    end = math.floor(time.time() / step) * step
    start = end - project.config.discovery_window
    reading = Catalogue(project, for_project(project)).read(start, end, step)
    series, _ = sum_instances(sample(reading.dump, start, step, int((end - start) // step), reading.kinds))   # as discovery does
    on_bus = candidates(series)
    return {(s.labels["from"], s.labels["to"], s.labels["by"]): [(h["machine"], h["confidence"]) for h in associate(s, on_bus)["hypotheses"]]
            for s in series if s.name == "tasks_ticket_transitions_total"}


def test_what_the_owner_sees(free_demo):
    web, target, _logs = free_demo
    ids = plan(target)
    from orchestra_agents.config import load_project
    from orchestra_agents.events import Events
    from orchestra_agents.protocol import TaskClient
    ask = TaskClient(Events.for_project(load_project(target), source="planner"), "planner")
    for title in EXTRA:
        made = ask("create_ticket", {"title": title, "milestone_id": "M1", "description": "Write a note.", "acceptance": "- a note"})
        assert made.get("ok"), made
        ids.append(made["ticket"]["id"])
    wait_until("the tickets", lambda: settled(web) and len(states(web)) == len(ids), timeout_s=900)
    assert states(web) == dict.fromkeys(ids, "DONE")
    events = all_events(web)

    # the scheduler is nowhere: no event is its, and it is no machine, edge or hand-off
    assert not [e for e in events if e["source"].partition("/")[0] == "task_scheduler"]
    assert not [e for e in events if e["type"] in ("ticket.transitioned", "ticket.note_added")]      # no echoes
    d = web.get("/api/machines")
    assert "task_scheduler" not in {m["id"] for m in d["machines"]}
    assert not [e for e in d["edges"] if "task_scheduler" in (e["from"], e["to"])]
    assert not [h for h in d["handoffs"] if "task_scheduler" in (h["from"], h["to"])]

    # a reworked ticket passes from machine to machine with nothing in between
    rework = ids[len(ids) - len(EXTRA)]
    assert handoffs(events, rework) == [("planner", "ticket.created", "coordinator"), ("coordinator", "task.assigned", "worker"),
                                        ("worker", "review.requested", "reviewer"),
                                        ("reviewer", "changes.requested", "worker"), ("worker", "review.requested", "reviewer"),
                                        ("reviewer", "review.approved", "merge_gate")]
    edges = {(h["from"], h["via"], h["to"]) for h in d["handoffs"]}
    assert {("planner", "ticket.created", "coordinator"), ("coordinator", "task.assigned", "worker"), ("worker", "review.requested", "reviewer"),
            ("reviewer", "changes.requested", "worker"), ("reviewer", "review.approved", "merge_gate")} <= edges, edges
    assert not [e for e in edges if e[2] == "worker" and e[0] in ("planner", "supervisor")], edges   # new work reaches a worker only by dispatch

    # one coordinator machine: the Coordinator agent and the task system's dispatch, an instance of it
    [coord] = [m for m in d["machines"] if m["id"] == "coordinator"]
    assert {"ticket.created", "task.released", "gate.rejected", "task.attention_needed", "request.done"} <= set(coord["subscribes"]), coord
    assert coord["kind"] == "agent ×1, program ×1" and coord["package"] == "agents" and coord["about"].get("harness") == "dummy", coord
    assert [i["id"] for i in coord["instances"]] == ["dispatch"]

    # what each machine published: dispatch (offers, as the coordinator) adds one offer per ticket from the queue
    published = {m["id"]: {p["type"]: p["count"] for p in m["publishes"]} for m in d["machines"]}
    print("published:", {m: sum(c.values()) for m, c in published.items()})
    print("degrees:", {m["id"]: {"publishes": len(m["publishes"]), "listens_for": len(m["subscribes"])} for m in d["machines"]})
    assert published["coordinator"].get("task.assigned") == len(ids)                       # one offer per ticket: rework went to the worker directly
    assert "task.assigned" not in published["worker"] and "task.assigned" not in published["reviewer"]    # no role offers work as its queue
    assert "ticket.created" in published["planner"] and "gate.landed" in published["merge_gate"]

    # discovery hypothesises no machine on a ticket flow for acts it did not take: the scheduler on
    # none; each counter's moves are by the machine named in it, and that machine is the only one
    # whose own events can explain the counter with no other machine's act between
    hyps = flow_hypotheses(target)
    print("hypotheses:", hyps)
    assert hyps, "no ticket transition counter was read"
    assert not [k for k, hs in hyps.items() for m, _ in hs if m == "task_scheduler"]
    assert not [k for k in hyps if k[2] == "task_scheduler"]                                 # no move is counted as the scheduler's

    # the ticket measurements arrive through the task system's instrument, with its provenance
    from orchestra.config import load_project as orchestra_project
    from orchestra.metrics import for_project
    dump = for_project(orchestra_project(target)).dump("tasks_tickets", {})
    assert dump and all(labels.get("instrument") == "tasks.tickets" and "machine" not in labels for _, labels, _ in dump)


def test_the_agents_host_is_no_machine(free_demo):
    """The owner, 2026-10-27: the agents' host is the shared runtime that runs the configured
    agents, no machine. After a run: no machine ``agents``, no edge to it and no event from it; the
    Supervisor announces what the host watches for it, so the map shows merge_gate's gate.landed
    reaching the Supervisor; it is still told when its ticket lands; and the agents' measurements
    arrive through the plugin's instrument."""
    web, target, _logs = free_demo
    ids = plan(target)
    lines = chat(web, "Please file the note ticket.")
    assert lines[-1]["type"] == "done" and not lines[-1].get("error"), lines[-1]
    assert any(ln["type"] == "tool_start" and ln.get("name") == "hand_to_coordinator" for ln in lines)
    wait_until("the tickets", lambda: settled(web) and len(states(web)) == len(ids) + 1, timeout_s=400)
    assert set(states(web).values()) == {"DONE"}
    # the Supervisor is told its ticket landed: the host heard the gate for it and woke it
    wait_until("the Supervisor told", lambda: ((web.get(f"{AGENTS}/attention").get("message") or {}).get("text") or "") == "Your note ticket landed.",
               timeout_s=60)

    events = all_events(web)
    assert not [e for e in events if e["source"].partition("/")[0] == "agents"]
    [asked] = [e for e in events if e["type"] == "work.requested"]
    [planned] = [e for e in events if e["type"] == "work.planned"]
    assert (asked["source"], planned["source"], planned["data"]["request"]) == ("supervisor", "coordinator", asked["data"]["request"])
    [made] = [e for e in events if e["type"] == "ticket.created" and e["data"].get("by") == "coordinator"]
    assert made["source"] == "coordinator" and planned["data"]["tickets"][0]["id"] == made["subject"]   # the Coordinator planned it
    d = web.get("/api/machines")
    assert "agents" not in {m["id"] for m in d["machines"]}
    assert not [e for e in d["edges"] if "agents" in (e["from"], e["to"])]
    assert not [h for h in d["handoffs"] if "agents" in (h["from"], h["to"])]
    sup = next(m for m in d["machines"] if m["id"] == "supervisor")
    assert {"gate.landed", "ticket.created", "request.done", "request.refused", "task.attention_needed"} <= set(sup["subscribes"]), sup["subscribes"]
    assert {"from": "gate.landed", "to": "supervisor", "kind": "subscribes"} in d["edges"]
    assert "gate.landed" in {p["type"] for p in next(m for m in d["machines"] if m["id"] == "merge_gate")["publishes"]}
    coord = next(m for m in d["machines"] if m["id"] == "coordinator")
    assert "request.done" in coord["subscribes"]
    # the human's request passes from the Supervisor to the Coordinator and back; its ticket goes to dispatch,
    # which offers it to a worker
    assert handoffs(events, asked["subject"])[0] == ("supervisor", "work.requested", "coordinator")
    assert {"from": "work.planned", "to": "supervisor", "kind": "subscribes"} in d["edges"]           # the plan comes back to it
    assert ("coordinator", "task.assigned", "worker") in handoffs(events, made["subject"])

    # the measurements: through the plugin's instrument, the host writing none
    from orchestra.config import load_project as orchestra_project
    from orchestra.metrics import for_project
    store = for_project(orchestra_project(target))
    wait_until("the agents' measurements", lambda: bool(store.dump("agent_turns_finished_total", {})), timeout_s=30)
    for name in ("agents_working", "agent_turns_started_total", "agent_turns_finished_total"):
        dump = store.dump(name, {})
        assert dump and all(labels.get("instrument") == "agents.turns" for _, labels, _ in dump), (name, [lab for _, lab, _ in dump])
    roles = {labels["role"] for _, labels, _ in store.dump("agents_working", {})}
    assert {"supervisor", "coordinator", "worker", "reviewer"} <= roles
