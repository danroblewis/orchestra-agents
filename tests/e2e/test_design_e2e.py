"""The owner's scenario (machine-design.md §2.6), turns 1, 3, 4 and 5, with the fake Claude Code: a
real host, each turn a fake `claude -p` whose tool calls go through the real MCP server (or, with
`[agents] tools_via = "bash"`, the session's shell), so provenance is recorded where a real
session's calls are (the turn and the owner's message the host keeps). Every tool's answer is read
back from the reply and must be `ok`. The assertions are on the declared layer: the stand-in, or
Orchestra's own through its host's endpoint when the installed Orchestra has it (served here)."""

import json
import re
import shutil
import stat
import subprocess
from contextlib import nullcontext
from pathlib import Path

import pytest
from conftest import agents_project
from design_fixtures import CORE, core_endpoint, journal, model_with
from fake_tasks import FakeTasks

from orchestra_agents import intended_api, prompting
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore

FAKE = Path(__file__).parent / "fake_claude.py"
PACKAGE = Path(prompting.__file__).parent
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


def answers(text: str) -> list[dict]:
    """Each tool's answer in the fake's reply ("- tool: <answer>"), parsed."""
    assert text.startswith("What I did with my tools:"), text
    out = []
    for chunk in re.split(r"\n- (?=[a-z_]+: )", text.split("\n", 1)[1].removeprefix("- ")):
        tool, _, body = chunk.partition(": ")
        try:
            out.append({"tool": tool, **json.loads(body)})
        except ValueError:
            pytest.fail(f"{tool} did not answer with its result: {body[:400]}")
    return out


@pytest.fixture(params=["mcp", "bash"])
def chat(request, tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ORCHESTRA_EVENTS_ENDPOINT", raising=False)
    monkeypatch.delenv("ORCHESTRA_EVENTS_TOKEN", raising=False)
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
    with FakeTasks(project), (core_endpoint(project) if CORE else nullcontext()):
        def say(message: str, steps: list[dict]) -> list[dict]:
            script.write_text(json.dumps(steps))
            out = host.prompt_supervisor(message, lambda e: None)
            assert not out.get("error"), out
            got = answers(out["text"])
            assert [a["tool"] for a in got] == [s["tool"] for s in steps]
            for a in got:
                assert a.get("ok") is True, a
            return got
        try:
            yield say, project, repo
        finally:
            host.residents.close_all()


def test_the_feature_count_improver_is_declared_measured_drafted_and_refined_by_chat(chat):
    say, project, repo = chat
    head = git(repo, "rev-parse", "HEAD")
    why = "the owner's words this turn"
    model_with(project, {"s4": ("coordinator_queue", {"instrument": "tasks"}), "s5": ("tasks_tickets", {"state": "DONE"})},
               flows=[("tasks_done_rate", "s4", "s5")])                       # seeded model history: keys and ids differ

    turn1 = say(TURN1, [
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
                                            "script": SCRIPT, "every": "15m", "reason": why}}])
    L = intended_api.current(project)
    assert L["stocks"]["features"]["state"] == "declared" and L["stocks"]["features"]["provenance"]["author"] == "owner"
    assert L["stocks"]["features"]["provenance"]["message"] == TURN1
    assert L["stocks"]["features"]["measure"]["options"][0]["instrument"] == "features.acceptance"
    assert (project.state_dir / "instruments" / "features.acceptance.sh").exists()
    project.reload_config()
    entry = next(e for e in project.orchestra.config.raw["collect"] if e["name"] == "features.acceptance")
    assert turn1[-1]["draft"] is CORE and (entry.get("draft") is True) is CORE          # the flag, where Orchestra takes it

    turn3 = say(TURN3, [
        {"tool": "read_design", "args": {}},
        {"tool": "read_events", "args": {"minutes": 60}},
        {"tool": "read_model", "args": {}},
        {"tool": "declare_machine", "args": {
            "name": "Feature Count Improver", "kind": "universal", "description": "Improves how many features get delivered a day",
            "mode": "continuous", "receives": ["session.settled", "task.reported"], "emits": ["work.improvement_proposed"],
            "reads": ["features", "acceptance_tests_passing"],
            "spec": {"invariants": ["never changes code itself; it proposes work", "at most one proposal a run",
                                    "reads only what it is given"], "steps": STEPS, "uses": ["the coordinator's queue"]},
            "guesses": ["mode", "receives[0]", "receives[1]"],
            "quote": "Create a new Universal Machine called Feature Count Improver.", "reason": why}},
        {"tool": "declare_expected_effect", "args": {"machine": "feature_count_improver", "horizon": "3d", "reason": why,
                                                     "expect": [{"kind": "factor", "flow": "tasks_done_rate", "factor": 1.2},
                                                                {"kind": "appears", "to": "coordinator_queue"},
                                                                {"kind": "rises", "stock": "features"}]}}])
    m = intended_api.current(project)["machines"]["feature_count_improver"]
    assert m["state"] == "proposed" and m["provenance"]["author"] == "owner" and m["engagement"] == {"mode": "continuous"}
    assert m["receives"] == ["session.settled", "task.reported"]
    declared = turn3[3]["said"]
    assert any("woken by session.settled, task.reported" in line and "a day on the claude_code harness, model haiku" in line
               for line in declared)                                                    # the cost as drafted
    assert any("no listener yet for work.improvement_proposed" in line for line in declared)
    assert m["expected_effect"]["edits"][1] == {"from": None, "to": "coordinator_queue", "appears": True}   # a key, kept as one
    assert m["expected_effect"]["edits"][2] == {"stock": "features", "rises": True}      # it waits for a series
    effect = turn3[4]["said"]
    assert any("Features is not linked to a discovered stock yet" in line for line in effect)
    assert not any("coordinator_queue" in line for line in effect)               # in the model: publishable once confirmed
    from orchestra_agents import design_machines
    lay = intended_api.current(project)
    assert design_machines.publishable(project, lay, "feature_count_improver", m["expected_effect"]["edits"][1])[0] == {
        "from": None, "to": "s4", "appears": True}                               # the era's id, when it is published

    turn4 = say(TURN4, [{"tool": "refine_machine", "args": {
        "name": "feature_count_improver", "quote": "watch the ticket queue for DONE and REJECTED tickets, and the Merge Gate",
        "reason": "REJECTED tickets are changes.requested and DONE is gate.landed: what the reviewer and the gate publish",
        "add_receives": ["changes.requested", "gate.rejected", "gate.landed"],
        "remove_receives": ["session.settled", "task.reported"], "steps": NEW_STEPS}}])
    m = intended_api.current(project)["machines"]["feature_count_improver"]
    assert m["receives"] == ["changes.requested", "gate.rejected", "gate.landed"] and len(turn4[0]["changes"]) == 6
    assert m["latest"]["before"] == {"steps": STEPS}
    revs = [op for e in journal(project) for op in (e.get("sent") or e["ops"]) if op["op"] == "revise_machine"]
    assert [r.get("add") or r.get("remove") for r in revs[:5]] == [
        {"receives": ["changes.requested"]}, {"receives": ["gate.rejected"]}, {"receives": ["gate.landed"]},
        {"receives": ["session.settled"]}, {"receives": ["task.reported"]}]

    turn5 = say(TURN5, [{"tool": "refine_machine", "args": {
        "name": "feature_count_improver", "quote": "make this a scheduled task for 12am every day", "reason": why,
        "mode": "scheduled", "at": "00:00", "tz": "America/Los_Angeles", "missed": "skip", "steps": DAILY_STEPS}}])
    m = intended_api.current(project)["machines"]["feature_count_improver"]
    assert m["engagement"] == {"mode": "scheduled", "at": "00:00", "tz": "America/Los_Angeles", "missed": "skip"}
    assert m["latest"]["before"]["engagement"]["mode"] == "continuous"
    assert any("scheduled once a day at 00:00" in line and "it never runs" in line for line in turn5[0]["said"])
    revs = [op for e in journal(project) for op in (e.get("sent") or e["ops"]) if op["op"] == "revise_machine"]
    assert len(revs) == 8 and m["expected_effect"]["edits"]
    assert m["guessed"] == []                                         # every guess was replaced by the owner's words
    assert '"at": "00:00"' in (project.state_dir / "drafts" / "feature_count_improver" / "agent.json").read_text()
    assert git(repo, "rev-parse", "HEAD") == head                      # nothing committed on the base branch


def test_no_prompt_or_guide_asks_the_owner_to_write_configuration():
    """The Supervisor's prompts (what it says to the owner comes from them) and its guide. The
    guide's `[agents]` block is a reference for the Supervisor, not addressed to the owner, so
    only the phrases are checked there."""
    prompts = PACKAGE / "prompts"
    mine = [*prompts.glob("supervisor*.md"), prompts / "operator.md", prompts / "design.md", *(prompts / "duties").glob("*.md")]
    for p in mine:
        for phrase in FORBIDDEN:
            assert phrase not in p.read_text(encoding="utf-8").lower(), (p.name, phrase)
    guide = (PACKAGE / "guide.md").read_text(encoding="utf-8").lower()
    for phrase in FORBIDDEN[:-1]:
        assert phrase not in guide, ("guide.md", phrase)
