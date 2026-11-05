"""The building blocks the Supervisor composes a code-delivery loop from (the owner, 2026-11-04: "my
Supervisor does"): the worker's and the reviewer's skills on a Workflows offer, whatever the
workflow (floor_skills.py), with the item's fields where rework's notes are; ``report_outcome``'s
``fields`` (pr_title, pr_body, review_notes); ``declare_program`` for orchestra-scm's ``open_pr``; the
owner's ``[scm] open_drafts`` that set_setting refuses; and the guide's section that lists them
with no ready-made definition."""

import json
import subprocess

import pytest
from conftest import agents_project
from design_fixtures import WHY, call
from floor_helpers import universal_machine

from orchestra_agents import floor, floor_skills, setup_tools, tooling
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore, StoreError

pytest_plugins = ["design_fixtures"]

IMPLEMENT = {"offer": "T1.implement.aa", "item": "T1", "step": "implement", "workflow": "delivery",
             "brief": "small task: count the widgets on the report page", "outcomes": ["implemented"],
             "fields": {"jira_key": "ABC-12", "review_notes": "widgets.py:12: the total skips hidden rows; count them"}}


@pytest.fixture
def repo_project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def test_a_worker_handed_a_worktree_gets_the_code_change_skill_and_the_items_notes():
    task, _ = floor.task_text(IMPLEMENT, "worker", "/wt/1")
    assert "# Skill: code-change" in task and "report_outcome" in task
    assert "## The item's fields" in task and "the total skips hidden rows" in task and "`jira_key`: ABC-12" in task
    skill = floor_skills.text("code-change")
    for words in ("`pr_title`", "`pr_body`", "## Testing", "## Risks", "Never ask the owner", "`review_notes`",
                  "`ci_notes`", "`pr_comments`", "`triage_notes`", "never push", "gh pr view"):
        assert words in skill, words
    assert "small_task" not in skill and "delivery" not in skill            # tied to no workflow
    assert len(skill) < 3000                                                # kept short


def test_a_review_offer_gets_the_review_skill_and_no_offer_without_a_worktree_gets_code_work():
    review = {**IMPLEMENT, "step": "ai_review", "outcomes": ["approved", "changes"]}
    task, _ = floor.task_text(review, "reviewer", "/wt/1")
    assert "# Skill: review-change" in task and "# Skill: code-change" not in task
    assert "\"review_notes\"" in floor_skills.text("review-change") and "never ask the owner" in task.lower()
    task, _ = floor.task_text({**IMPLEMENT, "fields": {}, "outcomes": ["triaged"]}, "triage", None)
    assert "Skill:" not in task and "The item's fields" not in task


def test_a_long_field_is_cut_and_said():
    text = floor_skills.fields_text({"fields": {"pr_body": "x" * 10_000}})
    assert len(text) < 5000 and "(cut)" in text


def test_report_outcome_carries_the_items_fields(repo_project):
    universal_machine(repo_project)
    store = AgentStore(repo_project, Events.for_project(repo_project, source="triage/1"))
    store.set_session("triage-I1", kind="triage", offer="I1.work.aa", offer_outcomes=["implemented"])
    fields = {"pr_title": "ABC-12: count the widgets", "pr_body": "Counts them.\n\n## Testing\n\n4 passed."}
    with pytest.raises(StoreError, match="fields is an object"):
        tooling.handle(repo_project, store, "report_outcome", {"outcome": "implemented", "summary": "x",
                                                               "fields": {"pr title": "x"}}, role="triage",
                       ticket_env="I1.work.aa")
    out = tooling.handle(repo_project, store, "report_outcome", {"outcome": "implemented", "summary": "done",
                                                                 "fields": fields}, role="triage", ticket_env="I1.work.aa")
    assert "pr_body, pr_title" in out["message"]
    assert store.pop_report("I1.work.aa") == {"outcome": "implemented", "summary": "done", "fields": fields}
    tooling.check_args("report_outcome", {"outcome": "implemented", "summary": "done", "fields": fields})


@pytest.mark.parametrize("key, value", [("scm.open_drafts", True), ("scm", {"repo": "acme/service", "open_drafts": True})])
def test_open_drafts_is_the_owners_alone(repo_project, key, value):
    with pytest.raises(StoreError, match="open_drafts is the owner's alone.*open_drafts = true"):
        setup_tools._set_setting(repo_project, AgentStore(repo_project), {"key": key, "value": value})


def test_the_whole_scm_table_is_never_rewritten_over_the_owners_allowance(repo_project):
    toml = repo_project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text() + '\n[scm]\nrepo = "acme/service"\nopen_drafts = true\n')
    repo_project.reload_config()
    with pytest.raises(StoreError, match="holds the owner's open_drafts"):
        setup_tools._set_setting(repo_project, AgentStore(repo_project), {"key": "scm", "value": {"repo": "acme/other"}})


def test_open_pr_is_declared_with_its_outcomes(project, store):
    pytest.importorskip("orchestra_workflows.programs.machines")
    from orchestra_agents import program_tools
    if "open_pr" not in program_tools.shipped_programs(program_tools.programs_module()):
        pytest.skip("orchestra-scm with open_pr is not installed here")
    out = call(project, store, "declare_program", program="open_pr", reason=WHY)
    body = json.loads((project.orchestra.state_dir / "machines" / "open_pr" / "machine.json").read_text())
    assert out["ok"] and body["spec"] == {"program": "open_pr", "role": "open_pr", "args": [], "timeout": "10m",
                                          "exit": {"0": "opened", "3": "no_changes", "4": "failed", "5": "drafted"}}


def test_the_guide_lists_the_blocks_and_no_ready_made_definition():
    from pathlib import Path

    from orchestra_agents import guides
    g = (Path(guides.__file__).parent / "guide.md").read_text(encoding="utf-8")
    section = g.split("### Composing a code-delivery loop", 1)[1].split("\n### ", 1)[0]
    for words in ("examples/delivery_live", "`open_pr`", "open_drafts = true", "github.checks.completed",
                  "\"of\": \"children\"", "github.pr.changes_requested", "github.pr.labeled", "review_notes",
                  "serves = [\"worker\", \"reviewer\"]", "person: owner", "without asking review questions"):
        assert words in section, words
    assert '"name":' not in section                                        # no definition to copy
