"""The Supervisor's bounds (SPEC §10; bootstrap run 2, 2026-11-02): held by Claude Code's settings
where they can be (the deny rules and the read hook the harness writes for the Supervisor's session
alone), by the instrument tools (never the project's build or tests), and by the fixed core of its
prompt for every bound."""

import json
import subprocess
import sys

import pytest

from orchestra_agents import prompting, runs_project
from orchestra_agents.harness import bounds, bounds_hook
from orchestra_agents.harness.base import SessionSpec
from orchestra_agents.harness.claude_code import ClaudeCodeSession
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]


def spec(tmp_path, **kw) -> SessionSpec:
    return SessionSpec(session_id="supervisor", session_dir=tmp_path / "sessions", cwd=tmp_path / "repo",
                       system_prompt="be good", role=kw.get("role", "supervisor"), tool_groups=("design",),
                       env={"ORCHESTRA_ROLE": "supervisor"}, log_path=tmp_path / "logs" / "x.log", model="opus",
                       read_only=True, bounds=kw.get("bounds", ()))


def settings_of(args: list[str]) -> dict:
    return json.loads(open(args[args.index("--settings") + 1]).read())


def test_the_supervisors_session_gets_the_deny_rules_and_the_read_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    (tmp_path / "repo").mkdir()
    state = tmp_path / "state"
    s = ClaudeCodeSession(spec(tmp_path, bounds=(str(tmp_path / "repo"), str(state))))
    s.open()
    got = settings_of(s._args())
    deny = got["permissions"]["deny"]
    for rule in ("Bash(cd ~)", "Bash(cd ~/.*)", "Bash(ls ~/.*)", "Bash(find /)", "Bash(find / *)", "Bash(find ~ *)",
                 "Bash(env)", "Bash(printenv *)",                                  # outside the project, a token's name
                 "Read(~/.config/**)", "Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.netrc)",    # credential stores
                 "Bash(curl *)", "Bash(nc *)", "Bash(nmap *)", "Bash(lsof -i*)", "WebFetch(domain:localhost)",   # probes
                 "Bash(pytest *)", "Bash(python -m pytest *)", "Bash(uv run *)", "Bash(cargo *)", "Bash(just *)",
                 "Bash(make *)", "Bash(npm *)"):                                    # the project's code, build and tests
        assert rule in deny, rule
    hook = got["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "Read|Grep|Glob|NotebookRead" and "bounds_hook.py" in hook["hooks"][0]["command"]
    roots = json.loads((tmp_path / "sessions" / "claude-code" / "supervisor.bounds.json").read_text())
    assert roots[:2] == [str(tmp_path / "repo"), str(state)]
    own = [r for r in roots if r.startswith(str(tmp_path / "claude"))]
    assert own == [str(tmp_path / "claude" / "projects" / s.session_file.split("/")[-2])]    # its own sessions, no other


def test_an_unbounded_session_gets_no_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    s = ClaudeCodeSession(spec(tmp_path, role="worker"))
    s.open()
    assert "--settings" not in s._args()


def test_the_host_bounds_the_supervisor_alone(make_host, project, store):
    host = make_host(store)
    got = host._bounds("supervisor", project.repo_root)
    assert got == (str(project.repo_root), str(project.orchestra.state_dir))
    assert host._bounds("worker", project.repo_root) == () and host._bounds("coordinator", project.repo_root) == ()


def hook_says(event: dict, roots: list[str]) -> dict | None:
    return bounds_hook.decide(event, roots)


@pytest.mark.parametrize("tool,args,denied", [
    ("Read", {"file_path": "{repo}/src/app.py"}, False),
    ("Read", {"file_path": "src/app.py"}, False),                              # relative: under the session's cwd
    ("Read", {"file_path": "{state}/logs/host.log"}, False),
    ("Read", {"file_path": "{home}/.hackriff-ops/notes.md"}, True),
    ("Read", {"file_path": "{home}/.config/orchestra-bridges/jira-token"}, True),
    ("Read", {"file_path": "{repo}/../other-repo/README.md"}, True),
    ("Grep", {"pattern": "TOKEN", "path": "/"}, True),
    ("Grep", {"pattern": "TOKEN"}, False),                                     # no path: the cwd
    ("Glob", {"pattern": "{home}/.config/**"}, True),
    ("Glob", {"pattern": "**/*.py", "path": "{repo}"}, False),
    ("Bash", {"command": "ls"}, False),                                        # the shell is the deny rules' and the prompt's
])
def test_the_read_hook_denies_reads_outside_the_project_and_the_state_dir(tmp_path, tool, args, denied):
    repo, state, home = tmp_path / "repo", tmp_path / "state", tmp_path / "home"
    fill = {k: str(v) for k, v in (("repo", repo), ("state", state), ("home", home))}
    event = {"tool_name": tool, "tool_input": {k: v.format(**fill) for k, v in args.items()}, "cwd": str(repo)}
    got = hook_says(event, [str(repo), str(state)])
    assert bool(got) == denied
    if got:
        out = got["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny" and "outside what this session may read" in out["permissionDecisionReason"]


def test_the_hook_runs_as_claude_code_runs_it_and_fails_closed(tmp_path):
    roots = tmp_path / "roots.json"
    roots.write_text(json.dumps([str(tmp_path / "repo")]))
    event = {"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}, "cwd": str(tmp_path / "repo")}
    p = subprocess.run([sys.executable, "-I", str(bounds.HOOK), str(roots)], input=json.dumps(event),
                       capture_output=True, text=True, check=False)
    assert p.returncode == 0 and json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    p = subprocess.run([sys.executable, "-I", str(bounds.HOOK), str(tmp_path / "missing.json")], input="{}",
                       capture_output=True, text=True, check=False)
    assert p.returncode == 2 and "refused" in p.stderr


@pytest.mark.parametrize("text", [
    "cargo test --workspace", "just check", "make -C . test", "cd py && uv run --locked pytest -q -n 6", "npm test",
    "python -m pytest tests", "go test ./...", "sh -c 'just lint'", "FOO=1 pytest -q",
    "python -m orchestra.instruments.test_reports --run 'cd py && pytest' --history h.jsonl",
    "#!/bin/sh\nn=$(cargo metadata --no-deps | wc -c)\necho \"size $n\"\n",
])
def test_an_instrument_that_runs_the_projects_build_or_tests_is_refused(text):
    assert runs_project.refusal(text)


@pytest.mark.parametrize("text", [
    "echo 'acceptance_tests_passing 41'", "python -m orchestra.instruments.test_reports --junit reports/junit.xml",
    "grep -c TODO app.py | awk '{print \"todos\", $1}'", "echo \"make sure: 1\"", "echo go 1",
    "git rev-list --count main",
])
def test_a_measurement_is_not_refused(text):
    assert runs_project.refusal(text) is None


def test_a_collector_script_in_the_repository_is_read_for_what_it_runs(tmp_path):
    (tmp_path / "machines").mkdir()
    (tmp_path / "machines" / "suite.sh").write_text("#!/bin/sh\ncargo test -q >/dev/null && echo 'suite_ok 1'\n")
    with pytest.raises(StoreError, match=r"not an instrument: it runs the project's build or tests \(cargo test"):
        runs_project.refuse("machines/suite.sh", root=tmp_path)
    runs_project.refuse("machines/missing.sh", root=tmp_path)                 # nothing there: nothing to refuse


def test_the_fixed_core_carries_every_bound():
    core = " ".join((prompting.PROMPTS_DIR / "supervisor_core.md").read_text().lower().split())
    for words in ("never run the project's own code", "never read outside the project", "`find /`",
                  "never probe", "ask the owner which address", "has not named in words", "propose it in one line",
                  "never list or offer credential files", "claude code's settings hold some of these"):
        assert words in core, words
