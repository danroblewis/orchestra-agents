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
    for sub, texts in (("sessions", ["list | show", "a session id from `sessions list`", "archived (its ticket closed)"]), ("roles", ["a role name"]),
                       ("run", ["serves the Supervisor chat"]), ("status", ["running, idle, paused, down"])):
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
    assert "wake rules" not in shown and "interval" not in shown                            # a role has no runtime
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


def test_sessions_list_says_what_each_session_is(project, capsys):
    """A per-ticket session whose turn is over is `ended` while its ticket is open and `archived`
    once the ticket closes (the list still names it, so `sessions show` has an id to be given);
    only a resident between turns is `idle`."""
    store = AgentStore(project)
    store.set_session("supervisor", kind="supervisor", status="idle", last_text="Hello.")
    store.set_session("reviewer-T-2", kind="reviewer", status="idle", ticket_id="T-2", last_text="Rejected.", started_at=3)
    store.set_session("reviewer-T-1", kind="reviewer", status="idle", ticket_id="T-1", last_text="Approved.", started_at=2)
    store.set_session("worker-T-1", kind="worker", status="idle", ticket_id="T-1", last_text="Done.", started_at=1)
    store.purge_ticket_sessions("T-1")
    assert main(["--repo", str(project.repo_root), "sessions", "list"]) == 0
    rows = [ln.split() for ln in capsys.readouterr().out.splitlines()[1:]]
    assert rows == [["supervisor", "idle", "Hello."], ["reviewer-T-2", "ended", "T-2", "Rejected."],
                    ["reviewer-T-1", "archived", "T-1", "Approved."], ["worker-T-1", "archived", "T-1", "Done."]]   # archived newest first


def test_status_reads_without_a_host(project, capsys):
    store = AgentStore(project)
    store.set_agent_state("coordinator", last_woken=1.0)
    assert main(["--repo", str(project.repo_root), "status"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "host:    not running" and out[1].startswith("agents:  dummy · fills worker, reviewer")
    assert any(ln.split()[:2] == ["supervisor", "down"] for ln in out) and any(ln.split()[:2] == ["coordinator", "down"] and "last woken" in ln for ln in out)
    assert out[-1] == "on tickets: 0"


def test_tools_has_a_header_and_whole_descriptions(capsys):
    assert main(["tools", "--groups", "report_ticket"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("tool") and out[1].startswith("report_ticket") and len(out) == 2
    assert out[1].rstrip().endswith((".", ")")) and len(out[1]) > 110                           # not cut at 80 characters


def test_run_takes_wait():
    assert build_parser().parse_args(["run", "--wait"]).wait is True
    assert build_parser().parse_args(["run"]).wait is False


def test_the_command_line_is_a_subcommand_of_orchestra():
    """`orchestra agents ...` (PLUGINS.md §4.10): the entry point orchestra.commands: agents, no
    executable of its own, and the usage names it as Orchestra calls it."""
    import sys
    from importlib.metadata import entry_points

    assert [ep.value for ep in entry_points(group="orchestra.commands") if ep.name == "agents"] == ["orchestra_agents.cli:main"]
    assert not [ep for ep in entry_points(group="console_scripts") if ep.value.startswith("orchestra_agents")]
    out = subprocess.run([sys.executable, "-m", "orchestra", "agents", "--help"], capture_output=True, text=True, check=True).stdout
    assert out.startswith("usage: orchestra agents")


def test_a_reply_read_in_the_terminal_chat_is_marked_seen(project, monkeypatch, capsys):
    import http.server
    import json
    import threading

    class Host(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                               # /health
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def do_POST(self):                                              # /supervisor: one reply, then done
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"type": "assistant_end", "text": "All 4 tickets are done."}).encode() + b"\n")
            self.wfile.write(json.dumps({"type": "done", "text": "All 4 tickets are done."}).encode() + b"\n")

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Host)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        store = AgentStore(project)
        store._mutate_runtime(lambda d: d.__setitem__("control", {"port": srv.server_address[1], "pid": 1}))
        lines = iter(["how is it going?"])

        def typed(prompt=""):
            try:
                return next(lines)
            except StopIteration:
                raise EOFError from None
        monkeypatch.setattr("builtins.input", typed)
        assert not store.runtime().get("supervisor_seen_at")
        assert main(["--repo", str(project.repo_root), "chat"]) == 0
        assert "All 4 tickets are done." in capsys.readouterr().out
        assert store.runtime()["supervisor_seen_at"] > 0                      # read here: no banner on the pages
    finally:
        srv.shutdown()
        srv.server_close()
