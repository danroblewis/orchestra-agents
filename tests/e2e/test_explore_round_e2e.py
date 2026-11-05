"""The Supervisor's explore round, end to end on the dummy harness (a real host, a real turn process
per wake-up, the tools through the session's token, Orchestra's experiment log and run record): once
a store is declared, the round wakes it on schedule; it reads the experiment log, records one
experiment with its expected effect and applies it on the standing rule (a split), while a change
that widens match is refused on its own wake-up; the next round has nothing to judge until a
discovery run ends, then wakes it to judge the experiment first; the rounds count against the
day's cap for its initiative, and the digest says what was explored."""

import subprocess
import threading
import time

import pytest
from conftest import agents_project
from orchestra.sd.store import ModelStore

from orchestra_agents import explore_round
from orchestra_agents.events import Events
from orchestra_agents.harness import read_transcript
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore

ENTRY = ('{ name = "metrics", url = "http://127.0.0.1:9/", match = ["{namespace=\\"team\\"}"], '
         'explore = ["{namespace=\\"team\\"}", "{__name__=~\\"queue_.*\\", env=\\"prod\\"}"]')
SPLIT = ENTRY + ', split = ["queue"] }'
WIDE = ENTRY.replace('match = ["{namespace=\\"team\\"}"]',                          # within explore, outside match
                     'match = ["{namespace=\\"team\\"}", "{__name__=~\\"queue_.*\\", env=\\"prod\\"}"]') + " }"

SCRIPT = f'''
[[rule]]
role = "supervisor"
prompt = "\\\\[Explore round\\\\][\\\\s\\\\S]*First judge experiment (?P<id>x[\\\\w-]+)"
steps = [{{ tool = "judge_experiment", args = {{ id = "{{match.id}}", verdict = "kept", why = "the run after it kept every stock" }} }}]
reply = "Explore round: judged {{match.id}} kept."

[[rule]]
role = "supervisor"
prompt = "\\\\[Explore round\\\\]"
steps = [
  {{ tool = "read_experiments" }},
  {{ tool = "read_discovery_runs" }},
  {{ tool = "log_experiment", args = {{ change = {{ source = "metrics", split = ["queue"] }}, expect = "each queue's backlog its own stock" }}, keep = "e" }},
  {{ tool = "set_setting", args = {{ key = "sources", value = [{SPLIT}], target = "overlay", reason = "split by queue: experiment {{kept.e.experiment.id}}" }} }},
  {{ tool = "set_setting", args = {{ key = "sources", value = [{WIDE}], target = "overlay", reason = "a wider match" }} }},
]
reply = "Explore round: experiment {{kept.e.experiment.id}} recorded and applied."
'''


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def wait(cond, what, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if got := cond():
            return got
        time.sleep(0.2)
    raise AssertionError(f"{what}: not within {timeout} s")


def said(events, prefix):
    return [e for e in events.read(0, 5000)[0] if e["type"] == "agent.said" and e["source"] == "supervisor"
            and (e.get("summary") or "").startswith(prefix)]


@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo, harness="dummy")
    toml = repo / "orchestra.toml"
    # ask = "always": the round's wider match is refused as before (under "outside", the default since
    # 2026-11-04, a match widened within explore is applied; test_supervisor_ask holds that)
    toml.write_text(toml.read_text() + 'dummy_script = "dummy.toml"\nexplore_every = "5m"\nask = "always"\n\n[[sources]]\nname = "metrics"\n'
                    'url = "http://127.0.0.1:9/"\nmatch = [\'{namespace="team"}\']\n'
                    'explore = [\'{namespace="team"}\', \'{__name__=~"queue_.*", env="prod"}\']\n')
    (repo / "dummy.toml").write_text(SCRIPT)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    assert p.config.explore_every == 300.0 and p.config.ask == "always" and p.orchestra.config.raw["sources"][0]["name"] == "metrics"
    store, events = AgentStore(p), Events.for_project(p)
    host = AgentHost(p, store, events)
    th = threading.Thread(target=host.run, daemon=True)
    th.start()
    wait(lambda: host.residents.procs.get("supervisor") is not None, "the Supervisor's session")
    yield p, host, store, events
    host.stop.set()
    th.join(30)


def test_the_explore_round_records_applies_and_judges_one_experiment_at_a_time(running):
    project, host, store, events = running
    ms = ModelStore.for_state(project.orchestra.state_dir)
    host.explore.started = time.time() - 301                                   # the round's interval has passed
    wait(lambda: said(events, "Explore round: experiment"), "the explore round's turn")
    [e] = ms.explorer().entries()
    assert (e["by"], e["change"], e["verdict"]) == ("supervisor", {"source": "metrics", "split": ["queue"]}, None)
    project.reload_config()
    [src] = project.orchestra.config.raw["sources"]
    assert src["split"] == ["queue"] and src["match"] == ['{namespace="team"}']      # applied on the rule; not widened
    msgs = read_transcript(store.find_session("supervisor"))
    [refused] = [m for m in msgs if m["role"] == "toolResult" and m.get("is_error") and "match widens" in m["text"]]
    assert "ask the owner in one sentence" in refused["text"]
    assert "[Explore round]" in [m for m in msgs if m["role"] == "user"][-1]["text"]
    rec = store.agent_state("supervisor")["explore"]
    assert rec["woke"] is True and store.agent_state("supervisor")["spend"]                # counted as its initiative
    assert next(iter(store.agent_state("supervisor")["spend"].values())).get("initiative_turns", 0) >= 1

    # the next round: the experiment waits for a discovery run, so no turn
    host.explore.run(explore_round._sources(project), time.time(), 300)
    rec = store.agent_state("supervisor")["explore"]
    assert rec["woke"] is False and e["id"] in rec["skipped"]

    # a discovery run ends: the next round wakes it to judge the experiment first
    ms.runs().append({"id": "r1", "kind": "discovery", "trigger": "schedule", "started": time.time(), "ended": time.time() + 1,
                      "outcome": "saved v2", "result": {"stocks": 3, "flows": 4, "trust": 0.8}})
    time.sleep(1.1)
    host.explore.run(explore_round._sources(project), time.time(), 300)
    wait(lambda: said(events, "Explore round: judged"), "the judging turn")
    [e] = ms.explorer().entries()
    assert e["verdict"] == "kept" and e["rounds"] == ["r1"] and e["judged_by"] == "supervisor"
    line = explore_round.digest_line(project, store, 0)
    assert line.startswith("Exploring: 3 round(s), 2 with a turn; 1 experiment(s) recorded; judged 1 kept.") and e["id"] in line
