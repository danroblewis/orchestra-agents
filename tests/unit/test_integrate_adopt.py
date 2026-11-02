"""integrate_system on a project that already holds collectors scaffolded from the bridges' kits
(the work demo's ``.orchestra/machines/{jira_team,jira_epics,...}``): the one the system's kit made
is configured in place, never a second scaffolded beside it; several of one kit are named. The
scaffolded test is stood in for, as in test_integrate.py."""

import tomllib

import pytest
from design_fixtures import WHY, call, git, layer, say

from orchestra_agents import design_integrate, integrate_kits
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

ASK = "Connect Jira Data Center read-only, filtered to the sprint board's members"
YES = "I approve reading it read-only, metadata only; the token is in the jira-token file"


@pytest.fixture(autouse=True)
def passing(monkeypatch):
    monkeypatch.setattr(integrate_kits, "run_test", lambda target, name, **_: {
        "ran": True, "passed": True, "summary": "3 passed in 0.5s", "command": f"python -m pytest {target}/test_{name}.py -q"})


def ship(project, kit: str, *names: str, commit: bool = True) -> None:
    """Collectors scaffolded from ``kit`` into the repository, as a project ships them (committed)."""
    from orchestra_bridges.kits import scaffold as s
    for name in names:
        s.scaffold(kit, name, project.repo_root / ".orchestra", ".orchestra")
    if commit:
        git(project.repo_root, "add", "-A")
        git(project.repo_root, "commit", "-qm", f"ship {', '.join(names)}")


def machines(project) -> set[str]:
    return {d.name for d in (project.repo_root / ".orchestra" / "machines").iterdir() if d.is_dir()}


def poller_toml(project, name) -> dict:
    return tomllib.loads((project.repo_root / ".orchestra" / "machines" / name / "poller.toml").read_text())


def connect(project, store, **over):
    say(store, ASK)
    args = {"system": "jira", "wants": "the team's tickets from assignment to Done",
            "settings": {"users": ["amara", "bo"], "jql": "project = PAY"}, "base_url": "https://jira.corp.test",
            "quote": ASK, "reason": WHY}
    return call(project, store, "integrate_system", **{**args, **over})


def test_the_one_collector_of_the_kit_is_configured_in_place(project, store):
    ship(project, "jira", "jira_team")
    before = machines(project)
    got = connect(project, store)
    assert got["ok"] and got["adopted"] is True and got["integration"] == "jira_team"
    assert machines(project) == before                                       # nothing scaffolded beside it
    assert got["dir"] == str(project.repo_root / ".orchestra" / "machines" / "jira_team")
    m = poller_toml(project, "jira_team")
    assert m["settings"]["users"] == ["amara", "bo"] and m["settings"]["jql"] == "project = PAY"
    assert m["settings"]["base_url"] == "https://jira.corp.test" and m["clearance"] == "needs approval"
    assert any("already in the project" in line and "nothing was scaffolded beside it" in line for line in got["said"])
    assert any("committed in the owner's repository" in line for line in got["said"])
    assert layer(project)["machines"]["jira"]["aliases"] == ["bot.jira"]   # the system, never the collector
    assert "jira_team" not in layer(project)["machines"]
    assert got["planned"] == ["[[machines]] jira_team, restart always", "[[collect]] jira_team, every 5m"]
    rec = design_integrate.integrations(store)["jira_team"]
    assert rec["adopted"] and rec["tracked"] and rec["kit"] == "jira"

    say(store, YES, 2)
    ok = call(project, store, "approve_integration", name="jira_team", token_file="/nonexistent/orchestra-test/jira-token",
              quote="I approve reading it read-only, metadata only", reason=WHY)
    assert poller_toml(project, "jira_team")["clearance"] == "approved"
    o = tomllib.loads(project.orchestra.overlay_file.read_text())
    assert next(e for e in o["machines"] if e["name"] == "jira_team")["command"].endswith("jira_team/jira_team.py")
    assert ok["heard"] == [] and "Confirm it is heard" in ok["next"] and "bot.jira" in ok["next"]


def test_heard_once_the_systems_actors_are_on_the_bus(project, store, monkeypatch):
    ship(project, "jira", "jira_team")
    connect(project, store)
    monkeypatch.setattr(design_integrate.design_dynamics, "last_seen",
                        lambda s: {"person.jira.amara": 1.0, "bot.github": 2.0, "relator": 3.0})
    say(store, YES, 2)
    ok = call(project, store, "approve_integration", name="jira_team", token_file="/nonexistent/orchestra-test/jira-token",
              quote="I approve reading it read-only, metadata only", reason=WHY)
    assert ok["heard"] == ["person.jira.amara"] and any("heard on the bus already" in line for line in ok["said"])


def test_several_of_one_kit_are_named_and_each_configured(project, store):
    ship(project, "jira", "jira_team", "jira_epics", "jira_uff")
    with pytest.raises(StoreError, match=r"3 collectors from the jira kit \(jira_epics, jira_team, jira_uff"):
        connect(project, store)
    assert not design_integrate.integrations(store)
    got = connect(project, store, name="jira_epics", settings={"jql": "issuetype = Epic AND updated >= -7d"})
    assert got["adopted"] and got["integration"] == "jira_epics"
    assert poller_toml(project, "jira_epics")["settings"]["jql"] == "issuetype = Epic AND updated >= -7d"
    assert connect(project, store, name="jira_team")["adopted"]
    assert connect(project, store)["integration"] == "jira_uff"              # the one left is the only one
    assert machines(project) == {"jira_team", "jira_epics", "jira_uff"}


def test_a_new_name_still_scaffolds_and_another_kits_name_is_refused(project, store):
    ship(project, "slack", "slack_channels")
    got = connect(project, store)                                            # no jira collector: a new scaffold
    assert got["integration"] == "jira_poller" and "adopted" not in got
    with pytest.raises(StoreError, match="from the slack kit, not jira"):
        connect(project, store, name="slack_channels")


def test_a_store_with_a_collector_is_measured_by_it(project, store):
    ship(project, "promql", "chronosphere")
    say(store, "Connect Chronosphere for this allowlist only")
    got = call(project, store, "integrate_system", system="chronosphere", wants="the team's series, bounded",
               base_url="https://acme-corp.chronosphere.io/data/metrics",
               settings={"selectors": ['{namespace="payments"}'],
                         "queries": [{"name": "payments_errors", "query": 'sum(errors_total{namespace="payments"})'}]},
               quote="Connect Chronosphere for this allowlist only", reason=WHY)
    assert got["adopted"] and got["integration"] == "chronosphere" and got["planned"] == ["[[collect]] chronosphere, every 5m"]
    s = poller_toml(project, "chronosphere")["settings"]
    assert s["queries"] == [{"name": "payments_errors", "query": 'sum(errors_total{namespace="payments"})'}]
    assert "chronosphere" not in layer(project)["machines"]                  # a store has no item events, no machine


def test_a_failing_committed_collector_is_handed_on_where_it_is(project, store, monkeypatch):
    ship(project, "jira", "jira_team")
    monkeypatch.setattr(integrate_kits, "run_test", lambda target, name, **_: {
        "ran": True, "passed": False, "summary": "1 failed", "command": "pytest", "tail": ["E assert"]})
    got = connect(project, store)
    d = project.repo_root / ".orchestra" / "machines" / "jira_team"
    assert d.is_dir() and got["dir"] == str(d)                               # never moved out, never removed
    req = next(e for e in store.events.read(0, 500)[0] if e["type"] == "work.requested")["requirements"]
    assert "committed in the repository at .orchestra/machines/jira_team/" in req and "cp -R" not in req
