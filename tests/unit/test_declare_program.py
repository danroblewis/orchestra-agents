"""``declare_program`` and a workflow's status (program_tools.py) on the real Workflows plugin's
reader (bootstrap run 7, findings 1 and 2): the shipped relator is declared in the state directory
on the owner's yes, read back by the plugin's own reader, and a workflow whose program step has no
program is never "running", in the tools' words or in a reply the host checks."""

import json

import pytest
from design_fixtures import WHY, call, say

from orchestra_agents import program_tools, truth
from orchestra_agents.facts import SupervisorFacts
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

PR = {"name": "pull_request", "items": {"one": "pull request", "many": "pull requests"},
      "starts": [{"event": "github.pr.opened", "fields": {"tickets": {"data": "tickets"}, "branch": {"data": "branch"}}}],
      "steps": [{"name": "relate", "who": {"program": "relate_prs"}, "outcomes": {"related": "open", "unrelated": "open"}},
                {"name": "open", "who": {"outside": "github"}, "done_when": {"event": "github.pr.merged"},
                 "outcomes": {"done": None}}]}
YES = "Yes, declare the relator, the token is in /secrets/github.token"
ARGS = ["--projects", "HK", "--github", "https://github.example.com/api/v3", "--token-file", "/secrets/github.token"]


def workflow(project, d=PR):
    f = project.orchestra.state_dir / "workflows" / d["name"] / "workflow.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d))


def machine_json(project, mid="relate_prs"):
    return project.orchestra.state_dir / "machines" / mid / "machine.json"


def test_the_shipped_relator_is_declared_on_the_owners_yes_and_its_workflow_then_runs(project, store):
    workflow(project)
    assert program_tools.lacking(project) == {"pull_request": [{"step": "relate", "program": "relate_prs"}]}
    assert program_tools.status(project, "pull_request").startswith("the workflow pull_request is declared but not running")
    say(store, YES)
    out = call(project, store, "declare_program", program="relate_prs", args=ARGS, quote=YES, reason=WHY)
    assert out["ok"] and out["path"] == str(machine_json(project)) and out["steps"] == ["pull_request.relate"]
    body = json.loads(machine_json(project).read_text())
    assert body["kind"] == "programmatic" and body["managed"] == "orchestra"
    assert body["spec"] == {"program": "relate_prs", "role": "relate_prs", "args": ARGS, "timeout": "1m",
                            "exit": {"0": "related", "3": "unrelated"}}
    from orchestra_workflows.programs import machines
    progs, problems = machines.load(project.orchestra)
    assert [p.id for p in progs] == ["relate_prs"] and not problems           # the plugin's reader runs it
    assert program_tools.lacking(project) == {}
    assert out["status"]["pull_request"].startswith("running")


def test_it_takes_the_owners_quoted_yes_and_writes_nothing_without_it(project, store):
    workflow(project)
    say(store, YES)
    with pytest.raises(StoreError, match="missing quote"):
        call(project, store, "declare_program", program="relate_prs", args=ARGS, reason=WHY)
    assert not machine_json(project).exists()
    with pytest.raises(StoreError, match="refused declare_program"):
        call(project, store, "declare_program", role="coordinator", program="relate_prs", args=ARGS, quote=YES, reason=WHY)


@pytest.mark.parametrize(("over", "match"), [
    ({"program": "my_linker"}, "one the Workflows plugin ships"),
    ({"args": ["--github", "https://github.example.com/api/v3", "--token", "ghp_abcdefghijklmnopqrstuvwxyz0123"]},
     "looks like a secret"),
    ({"args": ["--token-file", "github.token"]}, "full path"),
    ({"timeout": "soon"}, "spec.timeout"),
])
def test_a_program_not_shipped_a_token_value_or_what_the_reader_refuses_writes_nothing(project, store, over, match):
    say(store, YES)
    with pytest.raises(StoreError, match=match):
        call(project, store, "declare_program", **{"program": "relate_prs", "args": ARGS, "quote": YES, "reason": WHY, **over})
    assert not machine_json(project).exists() and not machine_json(project).parent.exists()


def test_the_repositorys_copy_wins_and_is_never_shadowed(project, store):
    f = project.repo_root / ".orchestra" / "machines" / "relate_prs" / "machine.json"
    f.parent.mkdir(parents=True)
    f.write_text("{}")
    say(store, YES)
    with pytest.raises(StoreError, match="the repository declares relate_prs already"):
        call(project, store, "declare_program", program="relate_prs", args=ARGS, quote=YES, reason=WHY)
    assert not machine_json(project).exists()


def test_declare_workflow_and_list_workflows_say_the_step_waits_for_its_program(project, store):
    workflow(project)
    listed = call(project, store, "list_workflows")
    st = next(w["status"] for w in listed["workflows"] if w["name"] == "pull_request")
    assert "its step relate waits for its program relate_prs" in st and "declare_program" in st
    assert "Never call it running" in st


CLAIMS = ["Goal one is fully running.", "The pull-request, ticket and on-call workflows are running.",
          "Tracking is now live end to end."]


@pytest.mark.parametrize("reply", CLAIMS)
def test_a_reply_calling_the_goal_running_while_a_step_has_no_program_is_corrected(project, store, reply):
    """Run 7, step 2.1: "Goal one is fully running" while declare_workflow had said the relator was
    not declared. The host appends Orchestra's correction, from the workflow's steps."""
    workflow(project)
    facts = SupervisorFacts(store, "HK", lambda wait_s: {"unavailable": True}, lambda: program_tools.lacking(project))
    fix = facts.after(f"I connected GitHub. {reply}", 0.0)
    assert fix is not None and reply.rstrip(".") in fix and "waits for its program relate_prs" in fix
    say(store, YES)
    call(project, store, "declare_program", program="relate_prs", args=ARGS, quote=YES, reason=WHY)
    assert facts.after(reply, 0.0) is None                                     # every step has its program now


def test_an_honest_reply_is_left_alone():
    lacks = {"pull_request": [{"step": "relate", "program": "relate_prs"}]}
    assert truth.overclaimed("The pull request workflow is not running yet: its relate step waits for relate_prs.", lacks) is None
    assert truth.overclaimed("The pollers are polling every five minutes.", lacks) is None
    assert truth.overclaimed("Goal one is fully running.", {}) is None
    assert truth.overclaimed("All three pollers are running.", lacks) is None             # not a goal or a workflow
