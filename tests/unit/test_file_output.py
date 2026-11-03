"""``[agents] tools_via = "file"`` (harness/file_output.py): the session holds no Orchestra tools,
writes ``$ORCHESTRA_OUTPUT`` in ``$GITHUB_OUTPUT``'s format, and the harness acts on it after the
turn, through the same calls as the tools, in file order; an invalid line is refused with why and the
rest still go. On the dummy harness and a scripted stand-in for Claude Code, never a model."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from conftest import agents_project
from floor_helpers import universal_machine

from orchestra_agents import access
from orchestra_agents.config import Config
from orchestra_agents.events import Events
from orchestra_agents.harness import file_output, get_harness
from orchestra_agents.harness.base import SessionSpec
from orchestra_agents.harness.claude_code import ClaudeCodeSession
from orchestra_agents.harness.codex import CodexSession
from orchestra_agents.store import AgentStore

GROUPS = ("escalate", "events", "predict", "report_outcome")
FILE = '''events=[{"type": "triage_noted", "subject": "I1", "data": "first"}, {"type": "orchestra.machine.started"}, {"type": "triage_noted", "data": {"note": "second"}}]
prediction={"expect": [], "horizon": "30m", "note": "nothing measured should change"}
summary<<END
It is a bug.
END
not a line of either shape
outcome=triaged
fields={"severity": "high"}
commit=abc123
'''


# the format: the program host's, line for line ------------------------------------------------

CASES = [
    ("a=1\nb=x y\n", {"a": 1, "b": "x y"}),
    ("j={\"k\": [1, 2]}\n", {"j": {"k": [1, 2]}}),
    ("t<<EOF\nline 1\nline=2\nEOF\nafter=yes\n", {"t": "line 1\nline=2", "after": "yes"}),
    ("a=1\na=2\n", {"a": 2}),
    ("junk\n=x\n9a=1\nok-name_2=v\n", {"ok-name_2": "v"}),
    ("u<<END\nnever closed\n", {"u": "never closed"}),
    ("eq=a<<b\n", {"eq": "a<<b"}),
]


@pytest.mark.parametrize(("text", "want"), CASES)
def test_the_file_reads_as_github_output_does(text, want):
    assert file_output.outputs(text) == want


def test_the_parse_is_the_workflows_program_host_s():
    step = pytest.importorskip("orchestra_workflows.programs.step")
    if not hasattr(step, "outputs"):
        pytest.skip("the installed orchestra-workflows has no ORCHESTRA_OUTPUT yet (W7)")
    for text, _ in CASES:
        assert file_output.outputs(text) == step.outputs(text), text
    assert step.OUTPUT == file_output.ENV and step.OUTPUT_MAX == file_output.OUTPUT_MAX


# what is acted on, with the calls stood in ---------------------------------------------------

def test_the_reserved_names_are_acted_on_in_file_order_and_a_bad_line_is_refused():
    calls, amended = [], []

    def call(name, args):
        if args.get("type") == "orchestra.machine.started":
            raise ValueError("Orchestra's own types cannot be raised")
        calls.append((name, args))
        return {"ok": True, "change": "c1"}
    out = file_output.act(FILE, GROUPS, call, amended.append)
    assert calls == [
        ("emit_event", {"type": "triage_noted", "subject": "I1", "note": "first"}),
        ("emit_event", {"type": "triage_noted", "note": "second"}),
        ("predict_fix", {"expect": [], "horizon": "30m", "note": "nothing measured should change"}),
        ("report_outcome", {"outcome": "triaged", "summary": "It is a bug."}),
    ]
    assert amended == [{"severity": "high"}]
    assert out["outputs"] == {"commit": "abc123"}
    assert [(r.get("line"), r.get("name")) for r in out["refused"]] == [(1, "events[1]"), (6, None)]
    assert "Orchestra's own types" in out["refused"][0]["reason"]
    assert out["done"][-1] == "reported triaged (report_outcome) with fields severity"


@pytest.mark.parametrize(("text", "groups", "says"), [
    ("summary=alone\n", GROUPS, "goes with an outcome"),
    ('outcome=READY_FOR_REVIEW\nsummary=x\nfields={"a": 1}\n', ("report_ticket",), "report_ticket carries none"),
    ("outcome=done\n", ("events",), "no exit tool"),
    ('events={"type": "x"}\n', GROUPS, "JSON list"),
    ('events=[{"type": "x", "id": 1}]\n', GROUPS, "not id"),
    ('events=[{"type": "x", "data": {"count": 2}}]\n', GROUPS, "only a note"),
    ("prediction=soon\n", GROUPS, "JSON object"),
    ('outcome=x\nsummary=s\nfields=["a"]\n', GROUPS, "fields is a JSON object"),
])
def test_what_cannot_be_done_is_refused_with_why(text, groups, says):
    out = file_output.act(text, groups, lambda n, a: {"ok": True}, lambda f: None)
    assert out["refused"] and says in out["refused"][0]["reason"], out


def test_a_worker_s_outcome_goes_to_report_ticket_as_its_state():
    calls = []
    file_output.act("outcome=blocked\nsummary=I need the key\n", ("report_ticket",), lambda n, a: calls.append((n, a)), None)
    assert calls == [("report_ticket", {"state": "BLOCKED", "summary": "I need the key", "blocked_reason": "I need the key"})]
    calls.clear()
    file_output.act("outcome=REJECTED\nsummary=1. tests fail\n", ("report_review",), lambda n, a: calls.append((n, a)), None)
    assert calls == [("report_review", {"verdict": "REJECTED", "summary": "1. tests fail", "feedback": "1. tests fail"})]


def test_the_prompt_names_only_what_the_session_could_have_called(tmp_path):
    p = file_output.prompt(tmp_path / "x.out", GROUPS)
    assert str(tmp_path / "x.out") in p and "$ORCHESTRA_OUTPUT" in p and "no Orchestra tools" in p
    assert "`events`" in p and "`prediction`" in p and "report_outcome" in p and "`fields`" in p
    worker = file_output.prompt(tmp_path / "x.out", ("report_ticket", "tickets"))
    assert "READY_FOR_REVIEW" in worker and "`events`" not in worker and "`fields`" not in worker


def test_tools_via_file_is_a_setting():
    assert Config.from_raw(tomllib.loads('[agents]\nharness = "dummy"\ntools_via = "file"\n')).tools_via == "file"


# a session in file mode, on the dummy harness --------------------------------------------------

@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    for k in ("ORCHESTRA_SOURCE", "ORCHESTRA_ROLE", access.TOKEN_ENV):
        monkeypatch.delenv(k, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    p = agents_project(repo, harness="dummy")
    (repo / "orchestra.toml").write_text((repo / "orchestra.toml").read_text() + 'dummy_script = "dummy.toml"\ntools_via = "file"\n')
    (repo / "dummy.toml").write_text("")
    p.reload_config()
    d = universal_machine(p)
    (d / "machine.toml").write_text((d / "machine.toml").read_text() + 'emits = ["triage_noted"]\n')
    from orchestra_agents.roles import RepoRoleStore, RoleDef
    RepoRoleStore(p).save(RoleDef(name="triage_job", description="triages", tools=["escalate", "events", "predict"]), "Triage.")
    return p


def offer_session(project, harness="dummy", groups=GROUPS, sid="triage-I1", offer="o-1", role="triage", **kw):
    store = AgentStore(project, Events.for_project(project))
    store.set_session(sid, offer=offer, kind=role, offer_outcomes=["triaged", "duplicate"])
    token = access.issue(store, sid, role, offer)
    spec = SessionSpec(session_id=sid, session_dir=project.sessions_dir, cwd=project.repo_root, system_prompt="be good",
                       role=role, tool_groups=groups, log_path=project.logs_dir / "agents" / f"{sid}.stderr.log",
                       env={"ORCHESTRA_ROLE": role, "ORCHESTRA_REPO": str(project.repo_root), "ORCHESTRA_TICKET": offer,
                            "ORCHESTRA_SOURCE": f"{role}/1", access.TOKEN_ENV: token}, tools_via="file", **kw)
    s = file_output.start(get_harness(harness), spec)
    s.open()
    return s, store, token


SCRIPT = """
[[rule]]
role = "triage"
prompt = "WRITE"

[[rule.steps]]
tool = "emit_event"
args = { type = "triage_noted" }

[[rule.steps]]
run = "env > env.txt"

[[rule.steps]]
write = "{output}"
text = '''FILE'''

[[rule]]
role = "triage"
steps = [{ say = "Read it; nothing to write." }]
""".replace("FILE", FILE, 1)


def test_a_file_mode_session_holds_no_tools_and_its_file_is_published_in_order(project):
    (project.repo_root / "dummy.toml").write_text(SCRIPT)
    s, store, token = offer_session(project)
    start = Events.for_project(project).end()
    res = s.prompt("WRITE: a user says the login page is blank", timeout=None, idle_timeout=60)
    assert res.error is None and not res.timed_out
    env = (project.repo_root / "env.txt").read_text()
    assert token not in env and f"ORCHESTRA_OUTPUT={file_output.path_for(project.sessions_dir, 'triage-I1')}" in env
    msgs = get_harness("dummy").read_transcript(s.session_file)
    tried = next(m for m in msgs if m["role"] == "toolResult" and m["tool_name"] == "emit_event")
    assert tried["is_error"]                                              # no tool reaches Orchestra while it runs
    evs = [e for e in Events.for_project(project).read(start)[0] if e["type"] in ("triage_noted", "orchestra.prediction.made")]
    assert [(e["type"], e.get("note")) for e in evs] == [("triage_noted", "first"), ("triage_noted", "second"),
                                                         ("orchestra.prediction.made", None)]
    assert {e["source"] for e in evs} == {"triage/1"}                     # as the machine, not the host
    assert store.pop_report("o-1") == {"outcome": "triaged", "summary": "It is a bug.", "fields": {"severity": "high"}}
    out = store.find_session("triage-I1")["output"]
    assert out["outputs"] == {"commit": "abc123"}
    assert [r.get("name") for r in out["refused"]] == ["events[1]", None]  # Orchestra's own type; the line of neither shape
    assert not file_output.path_for(project.sessions_dir, "triage-I1").exists()   # acted on once


def test_no_file_is_the_usual_end_without_a_result(project):
    (project.repo_root / "dummy.toml").write_text(SCRIPT)
    s, store, _ = offer_session(project)
    res = s.prompt("nothing to do here", timeout=None, idle_timeout=60)
    assert res.error is None and res.text == "Read it; nothing to write."
    assert store.pop_report("o-1") is None and "output" not in store.find_session("triage-I1")
    from orchestra_agents import floor
    assert floor.outcome_of(None, ["triaged"])[0] == "blocked"           # the floor's BLOCKED rule, as today


def test_a_turn_that_did_not_end_on_its_own_publishes_nothing(project):
    (project.repo_root / "dummy.toml").write_text('[[rule]]\nrole = "triage"\n'
                                                  'steps = [{ write = "{output}", text = "outcome=triaged\\nsummary=x\\n" }, { fail = "broke" }]\n')
    s, store, _ = offer_session(project)
    res = s.prompt("FAIL now", timeout=None, idle_timeout=60)
    assert res.error
    assert store.pop_report("o-1") is None
    assert "did not end on its own" in store.find_session("triage-I1")["output"]["refused"][0]["reason"]


# the other harnesses offer nothing in file mode ------------------------------------------------

def _spec(tmp_path, via: str) -> SessionSpec:
    (tmp_path / "wt").mkdir(exist_ok=True)
    return SessionSpec(session_id="worker-T-1", session_dir=tmp_path / "sessions", cwd=tmp_path / "wt", system_prompt="be good",
                       role="worker", tool_groups=("report_ticket",),
                       env={"ORCHESTRA_ROLE": "worker", "ORCHESTRA_REPO": str(tmp_path), "ORCHESTRA_TICKET": "T-1",
                            access.TOKEN_ENV: "tok-secret"}, log_path=tmp_path / "logs" / "x.log", tools_via=via)


class _Harness:
    def __init__(self, cls):
        self.cls = cls

    def start(self, spec):
        return self.cls(spec)


def test_claude_and_codex_in_file_mode_have_no_server_no_shell_tools_and_no_token(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    claude = file_output.start(_Harness(ClaudeCodeSession), _spec(tmp_path, "file")).inner
    args = claude._args()
    assert json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())["mcpServers"] == {}
    system = Path(args[args.index("--append-system-prompt-file") + 1]).read_text()
    assert "agents tool <tool>" not in system and "tok-secret" not in " ".join(args)
    env = claude._env()
    assert access.TOKEN_ENV not in env and env["ORCHESTRA_TOOLS"] == "" and env[file_output.ENV].endswith("worker-T-1.out")
    codex = file_output.start(_Harness(CodexSession), _spec(tmp_path, "file")).inner
    cargs = codex._args()
    assert not any(a.startswith("mcp_servers.") for a in cargs) and "tok-secret" not in " ".join(cargs)


def _fake_claude(tmp_path) -> str:
    """A scripted stand-in for ``claude -p``: it checks it was given no MCP server and no token, and
    writes the worker's result to the file it was named."""
    f = tmp_path / "fake-claude"
    f.write_text(f"""#!{sys.executable}
import json, os, sys
sys.stdin.read()
a = sys.argv
servers = json.load(open(a[a.index("--mcp-config") + 1]))["mcpServers"]
assert not servers and "{access.TOKEN_ENV}" not in os.environ, (servers, sorted(os.environ))
print(json.dumps({{"type": "system", "subtype": "init", "mcp_servers": []}}))
with open(os.environ["ORCHESTRA_OUTPUT"], "a") as out:
    out.write("summary=Wrote the note.\\noutcome=READY_FOR_REVIEW\\n")
print(json.dumps({{"type": "result", "subtype": "success", "result": "done"}}))
""")
    f.chmod(0o755)
    return str(f)


def test_a_claude_code_worker_in_file_mode_reports_through_the_file(project, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_BINARY", _fake_claude(tmp_path))
    store = AgentStore(project, Events.for_project(project))
    s, _, _ = offer_session(project, harness="claude_code", groups=("report_ticket",), sid="worker-T-1", offer="T-1", role="worker")
    res = s.prompt("# Your assignment\nTicket: T-1", timeout=30, idle_timeout=30)
    assert res.error is None and res.text == "done"
    assert store.pop_report("T-1") == {"state": "READY_FOR_REVIEW", "summary": "Wrote the note.", "blocked_reason": None}
