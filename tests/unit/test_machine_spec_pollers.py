"""The agents' spec loader reads only agents' specs: a bridges' scaffolded poller (a ``machine.toml``
with a top-level ``kit``) shares ``machines/<name>/`` and is skipped, so an agent named like it is
never given the poller's publishes or engagement (machine_spec.load_all)."""

import subprocess

from conftest import agents_project
from floor_helpers import universal_machine

from orchestra_agents.machine_spec import load_all, scaffolded_poller

POLLER = '''name = "triage"
kit = "jira"
engagement = "scheduled"
clearance = "needs approval"

[settings]
base_url = "https://jira.example.com"

[[publishes]]
type = "jira.issue.transitioned"
subject = "the issue key"
'''


def test_a_scaffolded_poller_is_no_spec_and_never_lends_an_agent_its_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    project = agents_project(repo)
    universal_machine(project, "triage", "triager", in_state_dir=True)       # the agent's own spec
    poller = project.repo_root / ".orchestra" / "machines" / "triage"        # the bridges' poller, same name
    poller.mkdir(parents=True)
    (poller / "machine.toml").write_text(POLLER)
    assert scaffolded_poller({"machine.toml": POLLER}) and not scaffolded_poller({"machine.toml": 'name = "x"\n'})
    spec = load_all(project)["triage"]
    assert spec.role == "triager" and spec.root.endswith("machines")         # the state directory's, not the poller's
    assert spec.engagement != "scheduled" and "jira.issue.transitioned" not in (spec.emits or [])
    (poller.parent / "only_poller").mkdir()
    (poller.parent / "only_poller" / "machine.toml").write_text(POLLER.replace("triage", "only_poller"))
    assert "only_poller" not in load_all(project)
