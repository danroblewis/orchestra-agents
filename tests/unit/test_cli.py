"""The command line says what each command and argument is, and its listings read as tables."""

import subprocess

import pytest
from conftest import agents_project

from orchestra_agents.cli import build_parser, main
from orchestra_agents.store import AgentStore


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def test_every_command_and_argument_has_help(capsys):
    top = " ".join(build_parser().format_help().split())        # argparse wraps help text
    assert "the agents' sessions: list them, or show one's transcript" in top
    for sub, texts in (("sessions", ["list | show", "a session id from `sessions list`"]), ("roles", ["a role name"]),
                       ("api", ["a tool name from `orchestra-agents tools`", "one line of JSON"]),
                       ("run", ["exit once every running turn has finished"])):
        with pytest.raises(SystemExit):
            build_parser().parse_args([sub, "--help"])
        out = " ".join(capsys.readouterr().out.split())
        for t in texts:
            assert t in out, (sub, t)


def test_roles_list_and_show(project, capsys):
    assert main(["--repo", str(project.repo_root), "roles", "list"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("role")
    for name in ("supervisor", "coordinator", "worker", "reviewer"):
        assert name in out
    assert "built-in" in out and "report_ticket" in out
    assert main(["--repo", str(project.repo_root), "roles", "show", "worker"]) == 0
    shown = capsys.readouterr().out
    lines = shown.splitlines()
    assert lines[0] == "worker: built in" and lines[1].startswith("used by:") and "report_ticket" in lines[2]
    assert any(ln.startswith("prompt:") and ln.endswith("worker.tasks.md") for ln in lines) and "# You are a Worker" in shown
    assert "created" not in shown and not shown.lstrip().startswith("{")                    # text, and no invented creation record
    with pytest.raises(SystemExit):
        main(["--repo", str(project.repo_root), "roles", "show", "nobody"])


def test_sessions_list_has_a_header_and_show_says_when_there_are_no_turns(project, capsys):
    assert main(["--repo", str(project.repo_root), "sessions", "list"]) == 0
    assert capsys.readouterr().out.splitlines()[0].startswith("session")
    AgentStore(project).set_session("supervisor", kind="supervisor", status="idle", last_text="two\nlines")
    assert main(["--repo", str(project.repo_root), "sessions", "list"]) == 0
    assert "two lines" in capsys.readouterr().out
    assert main(["--repo", str(project.repo_root), "sessions", "show", "supervisor"]) == 0
    assert capsys.readouterr().out.strip() == "supervisor: no turns yet"
    with pytest.raises(SystemExit):
        main(["--repo", str(project.repo_root), "sessions", "show", "nobody"])


def test_sessions_list_includes_finished_ticket_sessions(project, capsys):
    """A finished ticket's sessions are archived; the list still names them, so `sessions show`
    has an id to be given."""
    store = AgentStore(project)
    store.set_session("reviewer-T-1", kind="reviewer", status="idle", ticket_id="T-1", last_text="Approved.", started_at=2)
    store.set_session("worker-T-1", kind="worker", status="idle", ticket_id="T-1", last_text="Done.", started_at=1)
    store.purge_ticket_sessions("T-1")
    assert main(["--repo", str(project.repo_root), "sessions", "list"]) == 0
    rows = [ln.split() for ln in capsys.readouterr().out.splitlines()[1:]]
    assert rows == [["reviewer-T-1", "finished", "T-1", "Approved."], ["worker-T-1", "finished", "T-1", "Done."]]   # newest first


def test_tools_has_a_header_and_whole_descriptions(capsys):
    assert main(["tools", "--groups", "report_ticket"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("tool") and out[1].startswith("report_ticket") and len(out) == 2
    assert out[1].rstrip().endswith((".", ")")) and len(out[1]) > 110                           # not cut at 80 characters
