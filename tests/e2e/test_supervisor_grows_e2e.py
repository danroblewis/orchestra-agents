"""The Supervisor grows, end to end on the dummy harness (a real host, a real turn process per
wake-up, the tools through the session's token, real git): it writes its starting spec the first
time it runs; a standing instruction in the chat ("from now on, ...") becomes a duty, one commit
with the owner's words; a stalled stock Orchestra announces wakes it through keep_flow, and its
fix carries a prediction; its review round runs on schedule, finds what is off and wakes it, and
the turn counts against its day's budget."""

import subprocess
import threading
import time

import pytest
from conftest import agents_project

from orchestra_agents import budget
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.machine_spec import load
from orchestra_agents.store import AgentStore

SCRIPT = '''
# keep_flow: a stalled stock is diagnosed, and the fix is predicted before it is made
[[rule]]
role = "supervisor"
prompt = "event orchestra\\\\.model\\\\.stalled[^\\\\n]*subject=stock:(?P<stock>\\\\w+)"
steps = [{ tool = "predict_fix", args = { expect = [{ stock = "{match.stock}", below = 5 }], horizon = "30m", note = "restart the reviewer that stopped draining {match.stock}" }, keep = "p" }]
reply = "{match.stock} stalled; I predicted the fix as change {kept.p.change}."

[[rule]]
role = "supervisor"
prompt = "\\\\[Review round\\\\]"
reply = "Review round: noted what the quick check found."
'''


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo, harness="dummy")
    (repo / "orchestra.toml").write_text((repo / "orchestra.toml").read_text() + 'dummy_script = "dummy.toml"\n')
    (repo / "dummy.toml").write_text(SCRIPT)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    store, events = AgentStore(p), Events.for_project(p)
    host = AgentHost(p, store, events)
    th = threading.Thread(target=host.run, daemon=True)
    th.start()
    wait(lambda: host.residents.procs.get("supervisor") is not None, "the Supervisor's session")
    yield p, host, store, events
    host.stop.set()
    th.join(30)


def wait(cond, what, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if got := cond():
            return got
        time.sleep(0.2)
    raise AssertionError(f"{what}: not within {timeout} s")


def said(events, n_before: int):
    return [e for e in events.read(0, 5000)[0] if e["type"] == "agent.said" and e["source"] == "supervisor"][n_before:]


def test_the_supervisor_grows_a_duty_by_chat_keeps_the_flow_and_reviews_on_schedule(running):
    project, host, store, events = running
    spec = load(project, "supervisor")                                         # written the first time the host ran
    assert spec is not None and spec.model == "sonnet"
    assert git(project.repo_root, "log", "-1", "--format=%s").startswith("orchestra-agents: the Supervisor's spec")

    reply = host.prompt_supervisor("From now on, tell me every morning what waits on me", emit=lambda e: None)
    assert "standing duty" in reply["text"], reply
    msg = git(project.repo_root, "log", "-1", "--format=%B")
    assert msg.startswith("Supervisor: add duty owner_duty_") and "every morning what waits on me" in msg
    duty = next(d for d in load(project, "supervisor").duties if d.name.startswith("owner_duty_"))
    assert "every morning" in load(project, "supervisor").duty_text(duty)
    assert [e for e in events.read(0, 5000)[0] if e["type"] == "role.changed" and e["source"] == "supervisor"]

    before = len(said(events, 0))
    events.publish("orchestra.model.stalled", {"kind": "stock", "id": "review_queue", "since": time.time() - 900, "level": 9},
                   source="orchestra-host", subject="stock:review_queue")
    woke = wait(lambda: said(events, before), "the Supervisor woken by the stall")
    assert "review_queue stalled; I predicted the fix as change" in woke[0]["summary"]
    made = [e for e in events.read(0, 5000)[0] if e["type"] == "orchestra.prediction.made" and e["source"] == "supervisor"]
    assert made and made[-1]["expect"] == [{"stock": "review_queue", "below": 5}]
    assert budget.spent(store)["initiative_turns"] == 1                        # a duty's wake-up: the day's cap counts it

    before = len(said(events, 0))
    host.store.set_agent_state("supervisor", split_proposal={"at": time.time(), "signals": ["tools", "prompt_tokens",
                                                                                              "subscriptions", "in_share"]})
    events.publish("orchestra.machine.quiet", {"machine": "baker", "instance": "baker/1", "last_seen": time.time() - 900,
                                              "usual_gap": 60}, source="orchestra-host", subject="baker")
    host.review.started = time.time() - 1801                                  # the round's interval has passed
    wait(lambda: [e for e in said(events, before) if e["summary"].startswith("Review round: noted")], "the review round's turn")
    rec = store.agent_state("supervisor")["review"]
    assert rec["woke"] and any("machine baker quiet" in f for f in rec["findings"])
    assert store.agent_state("supervisor")["load"]["duties"] == 6
