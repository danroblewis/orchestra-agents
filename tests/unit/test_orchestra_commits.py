"""The agents' commits under .orchestra/ hold only the files the store wrote: a workflow definition
or a scaffolded poller left in the checkout (which their tools say nothing here commits) stays
untracked when a role, an agent, a team or a machine spec is committed beside it."""

import subprocess

import pytest
from conftest import agents_project

from orchestra_agents import spec_git
from orchestra_agents.org import AgentDef
from orchestra_agents.org_store import RepoOrgStore
from orchestra_agents.roles import RepoRoleStore, RoleDef

UNTRACKED = (".orchestra/workflows/x/workflow.json", ".orchestra/machines/jira_poller/poller.toml",
             ".orchestra/machines/jira_poller/jira_poller.py")


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


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
    for rel in UNTRACKED:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("not the store's\n")
    return p


def untracked(project) -> set[str]:
    return set(git(project.repo_root, "ls-files", "--others", "--exclude-standard").split())


def committed(project) -> set[str]:
    return set(git(project.repo_root, "show", "--name-only", "--format=", "HEAD").split())


def test_a_role_commit_leaves_the_others_untracked(project):
    rs = RepoRoleStore(project)
    rs.save(RoleDef(name="analyst", description="reads data", tools=["tickets"]), "# You read data\n")
    assert rs.last_commit_note.startswith("committed")
    assert committed(project) == {".orchestra/roles/analyst.json", ".orchestra/roles/analyst.md"}
    assert set(UNTRACKED) <= untracked(project)
    rs.delete("analyst")
    assert committed(project) == {".orchestra/roles/analyst.json", ".orchestra/roles/analyst.md"}   # the removal
    assert set(UNTRACKED) <= untracked(project)


def test_agent_and_team_commits_leave_the_others_untracked(project):
    org = RepoOrgStore(project)
    org.save_agent(AgentDef(name="scout", roles=["worker"]))
    assert committed(project) == {".orchestra/agents/scout.json"} and set(UNTRACKED) <= untracked(project)
    org.delete_agent("scout")
    assert committed(project) == {".orchestra/agents/scout.json"} and set(UNTRACKED) <= untracked(project)


def test_a_spec_commit_leaves_a_poller_beside_it_untracked(project):
    head = git(project.repo_root, "rev-parse", "HEAD").strip()
    got = spec_git.commit(project, {".orchestra/machines/supervisor/duties.md": "- check the gate\n"}, "agents: a duty")
    assert got["where"] == "side" and git(project.repo_root, "rev-parse", "HEAD").strip() == head   # the workflow file is another change
    assert set(UNTRACKED) <= untracked(project)
    (project.repo_root / UNTRACKED[0]).unlink()
    got = spec_git.commit(project, {".orchestra/machines/supervisor/duties.md": "- check the gate daily\n"}, "agents: a duty")
    assert got["where"] == "base"
    assert set(git(project.repo_root, "log", "--name-only", "--format=", f"{head}..HEAD").split()) == {
        ".orchestra/machines/supervisor/duties.md"}                                         # the side branch merged, the spec alone
    assert set(UNTRACKED[1:]) <= untracked(project)
