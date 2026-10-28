"""A team's probes turn conditions into events on the transition; agents raise declared events
within limits. (The probes in orchestra.toml are the task system's, tested there.)"""

import stat
import subprocess
import time

import pytest
from conftest import agents_project

from orchestra_agents import tooling as api
from orchestra_agents.events import Events
from orchestra_agents.org import AgentDef, OrgError, TeamDef, event_matches
from orchestra_agents.org_store import RepoOrgStore, all_agents, all_teams
from orchestra_agents.probes import Prober, team_probes
from orchestra_agents.roles import RepoRoleStore, RoleDef
from orchestra_agents.store import AgentStore, StoreError


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    (repo / "README.md").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    p = agents_project(repo)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "orchestra"], cwd=repo, check=True)
    return p


def run_probe(prober, spec):
    prober.tick([spec])
    for th in list(prober.running.values()):
        th.join(30)


def test_team_probe_runs_in_the_space_with_tools_on_path(project):
    store, events = AgentStore(project), Events.for_project(project)
    os_ = RepoOrgStore(project)
    RepoRoleStore(project).save(RoleDef(name="owner", tools=["tickets"]), "# You own\n\nThings, carefully and well.")
    os_.save_agent(AgentDef(name="po", roles=["owner"], team="product", kind="scheduled", every="1d"))
    # The probe finds its tool on PATH and runs it through sh: executing a freshly written
    # binary makes macOS scan it first, which takes long enough to make this test flaky.
    os_.save_team(TeamDef(name="product", lead="po", probes=[{"name": "docs_budget", "command": 'sh "$(command -v doc-budget)"', "every": "30m"}]),
                  "# P\n\n1. The docs are under budget.")
    tool = project.repo_root / ".orchestra" / "spaces" / "product" / "tools" / "doc-budget"
    tool.write_text("#!/bin/sh\ntest \"$(pwd)\" = \"$(cd \"$(dirname \"$0\")/..\" && pwd)\" && exit 0\necho wrong cwd; exit 1\n")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    specs = team_probes(project, all_teams(project))
    assert [s.name for s in specs] == ["docs_budget"] and specs[0].every == 1800 and specs[0].team == "product"
    assert specs[0].source == "po"                                   # its events are the team lead's: the host is no machine
    prober = Prober(project, store, events)
    run_probe(prober, specs[0])
    assert store.probe_state("docs_budget")["status"] == "ok", store.probe_state("docs_budget")["output"]
    with pytest.raises(OrgError):
        TeamDef(name="tt", lead="a", probes=[{"name": "Bad Name", "command": "x"}]).validate()


def test_agents_subscribe_to_probes_and_declared_events(project):
    assert event_matches(["probe.failed:docs_budget"], {"type": "probe.failed", "probe": "docs_budget"})
    assert not event_matches(["probe.failed:docs_budget"], {"type": "probe.failed", "probe": "other"})
    assert event_matches(["flaky_test_suspected:test_x"], {"type": "flaky_test_suspected", "subject": "test_x"})
    with pytest.raises(OrgError, match="unknown event type"):
        AgentDef(name="a_b", roles=["r"], wake_on=["lesson_proposed"]).validate()
    AgentDef(name="a_b", roles=["r"], wake_on=["lesson_proposed"]).validate({"lesson_proposed"})
    with pytest.raises(OrgError, match="emits entry"):
        TeamDef(name="tt", lead="a", emits=["gate.landed"]).validate()          # Orchestra's own types are not declarable
    # the coordinator wakes only on attention addressed to it
    assert not event_matches(all_agents(project)["coordinator"].wake_on, {"type": "ticket.created"})


def test_emit_event_is_declared_rate_limited_and_depth_limited(project):
    store = AgentStore(project)
    sup = {"role": "supervisor"}
    api.handle(project, store, "create_role", {"name": "analyst", "description": "d", "prompt": "# You analyse\n\n" + "Carefully. " * 6, "tools": ["events"]}, **sup)
    api.handle(project, store, "create_agent", {"name": "keeper", "roles": ["analyst"], "team": "devex", "kind": "scheduled", "every": "1d"}, **sup)
    api.handle(project, store, "create_team", {"name": "devex", "lead": "keeper", "responsibilities": "# DevEx\n\n1. Docs are accurate.",
                                               "emits": ["lesson_proposed"]}, **sup)
    with pytest.raises(StoreError, match="declared by teams"):
        api.handle(project, store, "create_agent", {"name": "retro", "roles": ["analyst"], "team": "devex", "kind": "ad_hoc",
                                                    "session": "ephemeral", "wake_on": ["nope_event"]}, **sup)
    with pytest.raises(StoreError, match="always up"):
        api.handle(project, store, "create_agent", {"name": "retro", "roles": ["analyst"], "team": "devex", "kind": "continuous",
                                                    "wake_on": ["lesson_proposed"], "session": "ephemeral"}, **sup)
    api.handle(project, store, "create_agent", {"name": "retro", "roles": ["analyst"], "team": "devex", "kind": "ad_hoc",
                                                "wake_on": ["lesson_proposed"], "session": "ephemeral"}, **sup)
    with pytest.raises(StoreError, match="may not emit"):
        api.handle(project, store, "emit_event", {"type": "flaky_test_suspected"}, role="keeper")
    with pytest.raises(StoreError, match="cannot be raised"):
        api.handle(project, store, "emit_event", {"type": "gate.landed"}, role="keeper")
    r = api.handle(project, store, "emit_event", {"type": "lesson_proposed", "subject": "CA-30", "note": "plan before editing"}, role="keeper")
    assert r["event"]["depth"] == 1 and r["event"]["by"] == "keeper" and r["wakes"] == ["retro"]
    assert event_matches(all_agents(project)["retro"].wake_on, r["event"])
    # depth: a turn caused by a three-deep chain may not emit again
    store.set_agent_state("keeper", turn_depth=3)
    with pytest.raises(StoreError, match="deep"):
        api.handle(project, store, "emit_event", {"type": "lesson_proposed"}, role="keeper")
    store.set_agent_state("keeper", turn_depth=0)
    # rate: twenty an hour
    store.set_agent_state("keeper", emits=[time.time()] * 19)
    api.handle(project, store, "emit_event", {"type": "lesson_proposed"}, role="keeper")
    with pytest.raises(StoreError, match="last hour"):
        api.handle(project, store, "emit_event", {"type": "lesson_proposed"}, role="keeper")
    # the supervisor may raise any new type; an old timestamp no longer counts
    store.set_agent_state("keeper", emits=[time.time() - 7200] * 25)
    api.handle(project, store, "emit_event", {"type": "lesson_proposed"}, role="keeper")
    api.handle(project, store, "emit_event", {"type": "direction_changed", "note": "go"}, **sup)
