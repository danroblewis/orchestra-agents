"""The design tools that pass one op of Orchestra's declared layer through (design_passthrough.py),
against the in-process declared layer: attachments (propose, revise), unmarking (unexpect,
unignore) and joining an observed name (alias), each with provenance and a reason."""

import pytest
from design_fixtures import WHY, call, layer, say

from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]


def machine(project, store):
    say(store, "a Postgres cluster the team runs", 1)
    call(project, store, "declare_machine", name="postgres", kind="programmatic", description="The team's Postgres cluster",
         managed="external", mode="continuous", spec={"source": "bot.postgres"}, reason=WHY)


def test_an_instruments_series_are_proposed_as_measuring_a_declared_machine_and_revised(project, store):
    machine(project, store)
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text() + '\n[[collect]]\nname = "pg"\ncommand = "true"\n')
    project.reload_config()
    out = call(project, store, "propose_attachment", machine="postgres", instrument="pg", prefix="pg_",
               labels=["job=postgres"], instance_label="instance", reason=WHY)
    [(aid, att)] = layer(project)["attachments"].items()
    assert (att["machine"], att["instrument"], att["state"]) == ("postgres", "pg", "proposed")
    assert att["match"] == {"prefix": "pg_", "labels": {"job": "postgres"}} and att["instance_label"] == "instance"
    assert any("never a sign it runs" in line for line in out["said"])
    call(project, store, "revise_attachment", attachment=aid, instance_label="host", reason=WHY)
    att = layer(project)["attachments"][aid]          # the Supervisor's revision waits for the owner, as a pending change
    assert att["instance_label"] == "instance" and [pc["set"] for pc in att["pending"]] == [{"instance_label": "host"}]
    with pytest.raises(StoreError, match="label=value"):
        call(project, store, "propose_attachment", machine="postgres", instrument="pg", labels=["postgres"], reason=WHY)
    with pytest.raises(StoreError, match="say what changes"):
        call(project, store, "revise_attachment", attachment=aid, reason=WHY)


def test_a_mark_taken_off_and_an_ignored_one_listed_again(project, store):
    call(project, store, "mark_expected", part="stock", key="acceptance_tests_passing", reason=WHY)
    call(project, store, "ignore", part="flow", key="repo_files->repo_commits", reason=WHY)
    call(project, store, "unexpect", part="stock", key="acceptance_tests_passing", reason=WHY)
    out = call(project, store, "unignore", part="flow", key="repo_files->repo_commits", reason=WHY)
    marks = layer(project)["marks"]
    assert "acceptance_tests_passing" not in marks["expected"] and "repo_files->repo_commits" not in marks["ignored"]
    assert "listed again" in out["said"][0]


def test_a_declared_machine_is_joined_to_the_name_it_is_heard_under(project, store):
    machine(project, store)
    call(project, store, "alias", machine="postgres", observed="bot.pg-primary", reason=WHY)
    assert layer(project)["machines"]["postgres"]["aliases"] == ["bot.pg-primary"]
    with pytest.raises(StoreError, match="refused alias"):
        call(project, store, "alias", role="coordinator", machine="postgres", observed="bot.x", reason=WHY)
