"""The owner's scenario (machine-design.md §2.6), turns 1, 3, 4 and 5, with the fake Claude Code: a
real host, each turn a fake `claude -p` whose tool calls go through the real MCP server (or, with
`[agents] tools_via = "bash"`, the session's shell), so provenance is recorded where a real
session's calls are (the turn and the owner's message the host keeps). The assertions are on the
declared layer (the stand-in until Orchestra has project.intended: intended_api.py)."""

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from conftest import agents_project
from fake_tasks import FakeTasks

from orchestra_agents import intended_api
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore

FAKE = Path(__file__).parent / "fake_claude.py"
TURN1 = ("I want a feedback loop where once per day an agent runs and tries to improve how many features get delivered per "
         "day. I think we need to start tracking features … I'm going to create a Features stock.")
TURN3 = "Create a new Universal Machine called Feature Count Improver."
TURN4 = ("I think the Feature Count Improver would watch the ticket queue for DONE and REJECTED tickets, and the Merge Gate "
         "… then maybe communicate those ideas to the Coordinator.")
TURN5 = ("I see this has a lot of high-throughput event triggers, maybe we should make this a scheduled task for 12am every "
         "day.")
SCRIPT = "echo 'acceptance_tests_passing 41'\n"
STEPS = ["Read session.settled and task.reported since the last run", "Compare Features with acceptance tests passing",
         "Find what most slowed delivery", "Write one improvement with its evidence",
         "Publish work.improvement_proposed for the Coordinator"]
NEW_STEPS = ["Read changes.requested, gate.rejected and gate.landed since the last run"] + STEPS[1:]
DAILY_STEPS = ["At midnight, read the day's changes.requested, gate.rejected and gate.landed"] + STEPS[1:]
FORBIDDEN = ("edit orchestra.toml", "add this to", "paste", "put this in", "run this command", "```toml")


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture(params=["mcp", "bash"])
def chat(request, tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    fake = tmp_path / "claude"
    shutil.copy(FAKE, fake)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "supervisor.json"
    monkeypatch.setenv("CLAUDE_BINARY", str(fake))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("FAKE_CLAUDE_SUPERVISOR", str(script))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    project = agents_project(repo, harness="claude_code")
    toml = repo / "orchestra.toml"
    toml.write_text(toml.read_text() + f'tools_via = "{request.param}"\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    project.reload_config()
    monkeypatch.setenv("ORCHESTRA_REPO", str(repo))
    events = Events.for_project(project)
    host = AgentHost(project, AgentStore(project, events), events)
    host._refresh_org(host.store.runtime()["agent_state"])
    with FakeTasks(project):
        def say(message: str, steps: list[dict]) -> str:
            script.write_text(json.dumps(steps))
            out = host.prompt_supervisor(message, lambda e: None)
            assert not out.get("error"), out
            return out["text"]
        try:
            yield say, project, repo
        finally:
            host.residents.close_all()


def test_the_feature_count_improver_is_declared_measured_drafted_and_refined_by_chat(chat):
    say, project, repo = chat
    head = git(repo, "rev-parse", "HEAD")
    why = "the owner's words this turn"

    replies = [say(TURN1, [
        {"tool": "declare_stock", "args": {"name": "Features", "unit": "features", "near": "DONE",
                                           "quote": "I'm going to create a Features stock.", "reason": why}},
        {"tool": "list_metrics", "args": {}},
        {"tool": "propose_measurements", "args": {"element": "features", "reason": why, "options": [
            {"label": "acceptance tests passing on main", "kind": "script", "how": "CI's JUnit report for main",
             "cost": "under a second a round; one file read"},
            {"label": "e2e tests passing", "kind": "script", "how": "the same, for e2e", "cost": "the same"},
            {"label": "tickets labelled feature reaching DONE", "kind": "counters", "how": "the task system's counters",
             "cost": "nothing new", "caveat": "counts what was planned as a feature, not what works"},
            {"label": "a simulated ramp", "kind": "simulated", "how": "a ramp", "cost": "nothing"}]}},
        {"tool": "add_instrument", "args": {"element": "features", "option": "a", "name": "features.acceptance",
                                            "kind": "collect", "script": SCRIPT, "every": "15m", "reason": why}},
        {"reply": "Features is declared and recording from the acceptance tests (features.acceptance, a draft): see its drawer "
                  "on the Dynamics page."}])]
    L = intended_api.current(project)
    assert L["stocks"]["features"]["state"] == "declared" and L["stocks"]["features"]["provenance"]["author"] == "owner"
    assert L["stocks"]["features"]["provenance"]["message"] == TURN1
    assert L["stocks"]["features"]["measure"]["chosen"] == "a"
    assert (project.state_dir / "instruments" / "features.acceptance.sh").exists()
    project.reload_config()
    assert [e["name"] for e in project.orchestra.config.raw["collect"]] == ["features.acceptance"]

    replies.append(say(TURN3, [
        {"tool": "read_design", "args": {}},
        {"tool": "read_events", "args": {"minutes": 60}},
        {"tool": "declare_machine", "args": {
            "name": "Feature Count Improver", "kind": "universal", "description": "Improves how many features get delivered a day",
            "engagement": {"mode": "scheduled", "at": "09:00", "tz": "America/Los_Angeles", "missed": "skip"},
            "receives": ["session.settled", "task.reported"], "emits": ["work.improvement_proposed"],
            "reads": ["features", "acceptance_tests_passing"],
            "spec": {"invariants": ["never changes code itself; it proposes work", "at most one proposal a run",
                                    "reads only what it is given"], "steps": STEPS, "uses": ["the coordinator's queue"]},
            "guesses": ["engagement", "receives[0]", "receives[1]"],
            "quote": "Create a new Universal Machine called Feature Count Improver.", "reason": why}},
        {"tool": "declare_expected_effect", "args": {"machine": "feature_count_improver", "horizon": "3d", "reason": why,
                                                     "expect": [{"flow": "tasks_done_rate", "factor": 1.2},
                                                                {"stock": "features", "rises": True}]}},
        {"reply": "Drafted feature_count_improver, unconfirmed, on the Machines page; about a turn a day on haiku."}]))
    m = intended_api.current(project)["machines"]["feature_count_improver"]
    assert m["state"] == "proposed" and m["provenance"]["author"] == "owner" and m["engagement"]["at"] == "09:00"
    assert m["expected_effect"]["edits"][1] == {"stock": "features", "rises": True}      # it waits for a series

    replies.append(say(TURN4, [{"tool": "refine_machine", "args": {
        "name": "feature_count_improver", "quote": "watch the ticket queue for DONE and REJECTED tickets, and the Merge Gate",
        "reason": "REJECTED tickets are changes.requested and DONE is gate.landed: what the reviewer and the gate publish",
        "edits": [{"op": "add_receive", "type": "changes.requested"}, {"op": "add_receive", "type": "gate.rejected"},
                  {"op": "add_receive", "type": "gate.landed"}, {"op": "remove_receive", "type": "session.settled"},
                  {"op": "remove_receive", "type": "task.reported"}, {"op": "rewrite_steps", "steps": NEW_STEPS}]}},
        {"reply": "It now watches changes.requested, gate.rejected and gate.landed: see its receives on the Machines page."}]))
    m = intended_api.current(project)["machines"]["feature_count_improver"]
    assert m["receives"] == ["changes.requested", "gate.rejected", "gate.landed"]

    replies.append(say(TURN5, [{"tool": "refine_machine", "args": {
        "name": "feature_count_improver", "quote": "make this a scheduled task for 12am every day", "reason": why,
        "edits": [{"op": "set_engagement", "mode": "scheduled", "at": "00:00", "tz": "America/Los_Angeles", "missed": "skip"},
                  {"op": "rewrite_steps", "steps": DAILY_STEPS}]}}]))
    m = intended_api.current(project)["machines"]["feature_count_improver"]
    assert m["engagement"] == {"mode": "scheduled", "at": "00:00", "tz": "America/Los_Angeles", "missed": "skip"}
    revs = [op for e in intended_api.layer(project).journal() for op in e["ops"] if op["op"] == "revise_machine"]
    assert len(revs) == 8 and m["expected_effect"]["edits"]
    assert m["guessed"] == []                                         # every guess was replaced by the owner's words
    assert (project.state_dir / "drafts" / "feature_count_improver" / "machine.toml").read_text().count('at = "00:00"') == 1
    assert git(repo, "rev-parse", "HEAD") == head                      # nothing committed on the base branch
    for text in replies:
        assert "Error" not in text and not any(p in text.lower() for p in FORBIDDEN), text
