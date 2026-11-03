"""The Supervisor's configuration changes, as run 4 found them wanting: each names what it is for, in
its commit and on the bus; a change that changes nothing is neither committed nor announced (it
would begin an era); and an era filter that fires faster than discovery can fit a stock is refused
with the numbers."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime

import pytest
from conftest import agents_project

from orchestra_agents import era_guard, tooling
from orchestra_agents import integrate_schedule as schedule
from orchestra_agents.events import Events
from orchestra_agents.prompting import PROMPTS_DIR
from orchestra_agents.side_branch import SIDE
from orchestra_agents.store import AgentStore, StoreError


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False).stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    return p


@pytest.fixture
def store(project):
    return AgentStore(project, Events.for_project(project, source="supervisor"))


def call(project, store, tool, **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role="supervisor")


def changes(store) -> list[dict]:
    evs, _ = store.events.read(0, 1000)
    return [e for e in evs if e["type"] in ("config.changed", "instruments.changed")]


def _at(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat()


def test_a_change_names_what_it_is_for_in_its_commit_and_on_the_bus(project, store):
    out = call(project, store, "set_setting", key="agents.draft_expiry", value='"12h"', target="repo",
               reason="The owner wants a draft instrument kept half a day before it expires.")
    assert out["commit"]
    subject, body = git(project.repo_root, "log", "-1", "--format=%s%n%b", SIDE).split("\n", 1)
    assert subject == 'Supervisor: draft expiry: agents.draft_expiry set to "12h"'
    assert "Supervisor-For: draft expiry" in body and "half a day" in body
    [ev] = changes(store)
    assert ev["type"] == "config.changed" and ev["subject"] == "setting:agents.draft_expiry" and ev["for"] == "draft expiry"
    named = call(project, store, "set_setting", key="outbox.kinds", value='{"supervisor.pull_request": {"sender": "x"}}',
                 target="repo", reason="The Supervisor's pull requests go through the Outbox.")
    assert git(project.repo_root, "log", "-1", "--format=%s", SIDE).startswith("Supervisor: an Outbox kind: outbox.kinds set")
    assert named["commit"]


def test_a_change_that_changes_nothing_is_not_committed_or_announced_and_says_so(project, store):
    first = call(project, store, "set_setting", key="agents.draft_expiry", value='"12h"', target="repo",
                 reason="The owner wants a draft instrument kept half a day.")
    tip, seen = git(project.repo_root, "rev-parse", SIDE), len(changes(store))
    again = call(project, store, "set_setting", key="agents.draft_expiry", value='"12h"', target="repo",
                 reason="The owner wants a draft instrument kept half a day.")
    assert first["commit"] and again["commit"] is None and not again["written"]
    assert again["changed"].startswith("nothing:") and "nothing was committed or announced" in again["applies"]
    assert git(project.repo_root, "rev-parse", SIDE) == tip and len(changes(store)) == seen
    # the integrations' steps (an overlay entry each) announce nothing for a step already there
    step = [{"how": "entry", "section": "collect", "entry": {"name": "poll", "command": "python poll.py measure", "every": "5m"}}]
    lines = schedule.write(project, store, step, "The owner's tracker, read every five minutes.")
    assert len(changes(store)) == seen + 1 and changes(store)[-1]["subject"] == "collector:poll"
    lines += schedule.write(project, store, step, "The owner's tracker, read every five minutes.")
    assert len(changes(store)) == seen + 1 and "not written or announced" in lines[-1]


def test_an_era_filter_faster_than_discovery_can_fit_a_stock_is_refused_with_the_numbers(project, store):
    from orchestra.sd.discover import MIN_COVER
    assert era_guard.minimum_s(3600, 30) == MIN_COVER * 3600 == 1800
    now = __import__("time").time()
    for k in range(8):                                  # a merge every 5.2 minutes, as run 4's week played
        store.events.publish("github.pr.merged", {"n": k}, source="bot.github.sail-bot", time=_at(now - 3600 + k * 312))
    for k in range(4):                                  # a release every two hours
        store.events.publish("release.published", {"n": k}, source="ci", time=_at(now - 9 * 3600 + k * 7200))
    with pytest.raises(StoreError, match=r"one every 5\.2 min .* at least 30\.0 min .*MIN_COVER.*not every merge"):
        call(project, store, "set_setting", key="discovery.eras_from", value='["config.changed", "github.pr.merged"]',
             target="repo", reason="Every merge is a release, so each begins an era.")
    assert not git(project.repo_root, "rev-parse", "--verify", "-q", SIDE).strip()        # nothing committed
    with pytest.raises(StoreError, match="one every 5.2 min"):          # a shorter window does not let it through either
        call(project, store, "set_setting", key="discovery", value='{"window": "15m", "eras_from": ["github.pr.*"]}',
             target="repo", reason="A short window so every merge can be its own era.")
    ok = call(project, store, "set_setting", key="discovery.eras_from", value='["config.changed", "release.published"]',
              target="repo", reason="A release changes the code that runs, so it begins an era.")
    assert ok["commit"] and "Caution" not in ok["applies"]
    unseen = call(project, store, "set_setting", key="discovery.eras_from", value='["config.changed", "deploy.finished"]',
                  target="repo", reason="A deploy changes what runs, so it begins an era.")
    assert "not known" in unseen["applies"]


def test_the_guide_says_what_an_era_is_for():
    text = (PROMPTS_DIR / "operator.md").read_text()
    assert "not for every merge" in text and "MIN_COVER" in text and "set_setting` refuses it" in text


def test_the_bus_subject_says_whether_a_change_can_alter_the_systems_behaviour(project, store):
    """The core's C3: an era begins only for config.changed about machine:, definition: or override:;
    a setting, a collector, a source or an instrument never begins one."""
    from orchestra_agents import supervisor_spec
    supervisor_spec.ensure(project)
    call(project, store, "add_duty", name="keep_gate_moving", text="Keep the gate landing.", listens=["gate.rejected"],
         tools=["pipeline", "observe"], reason="The owner said: watch the gate, it keeps getting stuck")
    duty = [e for e in changes(store) if e["type"] == "config.changed"]
    assert [e["subject"] for e in duty] == ["machine:supervisor"]
    call(project, store, "set_setting", key="scm", target="repo", reason="The owner named the repository and its base.",
         value='{"provider": "branch_pr", "repo": "acme/service", "base": "main"}')
    assert changes(store)[-1]["subject"] == "setting:scm"
    call(project, store, "set_collector", name="todos", command="echo todos 1", reason="The owner wants the TODO count.")
    assert changes(store)[-1]["subject"] == "collector:todos" and changes(store)[-1]["type"] == "instruments.changed"
    call(project, store, "set_machine", name="worker", command="python worker.py", reason="The owner's worker program runs.")
    assert changes(store)[-1]["subject"] == "machine:worker"
