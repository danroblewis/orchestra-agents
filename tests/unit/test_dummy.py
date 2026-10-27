"""The dummy harness: its scripts (parsed, matched, filled) and its turns (a real process per
turn, the normalized events, the transcript, the tools through ``tooling.handle``)."""

import subprocess
import threading
import time
from pathlib import Path

import pytest
from conftest import agents_project

from orchestra_agents.events import Events
from orchestra_agents.harness import SessionSpec, get_harness, read_transcript
from orchestra_agents.harness.dummy_script import DEFAULTS, ScriptError, Turn, load, parse
from orchestra_agents.store import AgentStore

DEMO_SCRIPT = Path(__file__).parents[2] / "demo" / "dummy.toml"


def test_the_built_in_rules_make_a_team():
    s = load(None)
    worker = s.pick(Turn(role="worker", ticket="T-1", checkout=True))
    assert [next(iter(st)) for st in worker.steps] == ["write", "commit", "tool"] and worker.steps[-1]["tool"] == "report_ticket"
    assert [next(iter(st)) for st in s.pick(Turn(role="worker", ticket="T-1")).steps] == ["write", "tool"]   # a task: RESULT.md
    assert s.pick(Turn(role="reviewer")).steps[0]["args"]["verdict"] == "APPROVED"
    assert s.pick(Turn(role="supervisor")).steps == () and "changed nothing" in s.pick(Turn(role="supervisor")).reply
    assert s.pick(Turn(role="coordinator")).reply == "Nothing to do." == s.pick(Turn(role="po")).reply


def test_the_first_matching_rule_is_played_and_the_built_ins_come_last(tmp_path):
    f = tmp_path / "s.toml"
    f.write_text('''
[[rule]]
role = "reviewer"
ticket = "DEMO-*"
turn = [1, 2]
reply = "early"

[[rule]]
role = "reviewer"
prompt = "(?i)count the lines"
checkout = true
reply = "counting"
''')
    s = load(f)
    assert s.pick(Turn(role="reviewer", ticket="DEMO-3", turn=2)).reply == "early"
    assert s.pick(Turn(role="reviewer", ticket="DEMO-3", turn=3, prompt="Count the lines", checkout=True)).reply == "counting"
    assert s.pick(Turn(role="reviewer", ticket="E2E-1", prompt="Count the lines")).reply == "Approved."   # not a checkout: the built-in
    assert len(s.rules) == 2 + len(parse(DEFAULTS.read_text()))


def test_placeholders_are_filled_everywhere():
    t = Turn(role="worker", ticket="T-7", turn=2, instance="worker/1",
             prompt="[Orchestra: notices]\n\n[The human's message]\nadd a backend worker\n")
    assert t.message == "add a backend worker"
    assert Turn(role="supervisor", prompt="# Wake-up\nmore").message == "# Wake-up"
    filled = t.fill({"write": "work/{ticket}.md", "args": {"summary": ["{role} {instance} turn {turn}"]}, "keep": "${HOME} {other}"})
    assert filled == {"write": "work/T-7.md", "args": {"summary": ["worker worker/1 turn 2"]}, "keep": "${HOME} {other}"}


@pytest.mark.parametrize(("text", "says"), [
    ('[[rule]]\nsteps = []', "role is required"),
    ('[[rule]]\nrole = "worker"\nwhen = 1', "unknown keys when"),
    ('[[rule]]\nrole = "worker"\nsteps = [{ say = "a", run = "b" }]', "exactly one of"),
    ('[[rule]]\nrole = "worker"\nsteps = [{ say = "a", text = "b" }]', "does not take text"),
    ('[[rule]]\nrole = "worker"\nsteps = [{ sleep = "long" }]', "number of seconds"),
    ('[[rule]]\nrole = "worker"\nprompt = "("', "not a regular expression"),
    ('[[rule]]\nrole = "worker"\nturn = 0', "turn is a number"),
    ('[rules]\nrole = "worker"', "unknown tables rules"),
    ('[[rule]\n', "not TOML"),
])
def test_a_broken_script_says_what_is_wrong(text, says):
    with pytest.raises(ScriptError, match=says):
        parse(text, "s.toml")


def test_the_demo_script_parses():
    assert len(load(DEMO_SCRIPT).rules) > len(parse(DEFAULTS.read_text()))


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    p = agents_project(repo, harness="dummy")
    (repo / "orchestra.toml").write_text((repo / "orchestra.toml").read_text() + 'dummy_script = "dummy.toml"\n')
    (repo / "dummy.toml").write_text("")
    p.reload_config()
    assert p.config.harness == "dummy" and p.config.dummy_script == "dummy.toml"
    return p


def session(project, role: str, groups: tuple[str, ...], cwd: Path, ticket: str = ""):
    spec = SessionSpec(session_id=f"{role}-{ticket or 'x'}", session_dir=project.sessions_dir, cwd=cwd, system_prompt="be good",
                       role=role, tool_groups=groups, log_path=project.logs_dir / "agents" / f"{role}.stderr.log",
                       env={"ORCHESTRA_ROLE": role, "ORCHESTRA_REPO": str(project.repo_root), "ORCHESTRA_TICKET": ticket,
                            "ORCHESTRA_SOURCE": f"{role}/1" if ticket else role})
    s = get_harness("dummy").start(spec)
    s.open()
    return s


def test_a_worker_turn_does_real_work_and_reports_through_the_tools(project, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=ws, check=True)
    (project.repo_root / "dummy.toml").write_text('''
[[rule]]
role = "worker"
steps = [
  { say = "Starting {ticket}." },
  { run = "echo made by {instance} > out.txt" },
  { commit = "{ticket}: out" },
  { tool = "create_ticket", args = { title = "x" } },
  { tool = "report_ticket", args = { state = "READY_FOR_REVIEW", summary = "out.txt on turn {turn}" } },
]
''')
    s = session(project, "worker", ("report_ticket",), ws, ticket="T-1")
    seen: list[dict] = []
    res = s.prompt("# Your assignment\nTicket: T-1", timeout=None, on_event=seen.append, idle_timeout=60)
    assert res.error is None and not res.timed_out
    kinds = [e["type"] for e in seen]
    assert kinds[0] == "turn_start" and seen[0]["pid"] and kinds[-1] == "settled"
    assert [e["name"] for e in seen if e["type"] == "tool_start"] == ["shell", "commit", "create_ticket", "report_ticket"]
    assert (ws / "out.txt").read_text() == "made by worker/1\n"
    assert subprocess.run(["git", "log", "--format=%s"], cwd=ws, capture_output=True, text=True, check=True).stdout == "T-1: out\n"
    store = AgentStore(project, Events.for_project(project))
    report = store.pop_report("T-1")                                        # what the runner sends on
    assert report["state"] == "READY_FOR_REVIEW" and report["summary"] == "out.txt on turn 1"
    msgs = read_transcript({"harness": "dummy", "session_file": s.session_file})
    assert [m["role"] for m in msgs][:2] == ["user", "assistant"] and msgs[0]["text"].startswith("# Your assignment")
    refused = next(m for m in msgs if m["role"] == "toolResult" and m["tool_name"] == "create_ticket")
    assert refused["is_error"] and "unknown tool" in refused["text"]        # not in the session's tool groups
    assert res.text == next(m for m in msgs if m["role"] == "toolResult" and m["tool_name"] == "report_ticket")["text"]


def test_turns_are_counted_per_session_and_a_fail_step_fails_the_turn(project, tmp_path):
    (project.repo_root / "dummy.toml").write_text('''
[[rule]]
role = "reviewer"
turn = 1
steps = [{ fail = "the dummy fails once" }]

[[rule]]
role = "reviewer"
reply = "turn {turn}"
''')
    s = session(project, "reviewer", ("report_review",), tmp_path, ticket="T-2")
    first = s.prompt("review it", timeout=None)
    assert first.error == "the dummy fails once"
    assert s.prompt("review it again", timeout=None).text == "turn 2"
    old = s.retire()
    assert old and Path(old).exists() and not Path(s.session_file).exists()
    assert s.prompt("a new session", timeout=None).error == "the dummy fails once"      # its turn 1 again


def test_a_slow_turn_is_cut_by_silence_and_killed_by_a_release(project, tmp_path):
    (project.repo_root / "dummy.toml").write_text('[[rule]]\nrole = "worker"\nsteps = [{ sleep = 30 }]\n')
    s = session(project, "worker", ("report_ticket",), tmp_path, ticket="T-3")
    t0 = time.time()
    res = s.prompt("work", timeout=None, idle_timeout=1.5)
    assert res.timed_out and res.stopped_by == "silence" and time.time() - t0 < 10
    out = {}
    th = threading.Thread(target=lambda: out.update(res=s.prompt("work", timeout=None)))
    th.start()
    deadline = time.time() + 10
    while s.pid is None and time.time() < deadline:
        time.sleep(0.05)
    s.kill()
    th.join(10)
    assert out["res"].error and not th.is_alive()


def test_a_broken_script_fails_the_turn_with_the_reason(project, tmp_path):
    (project.repo_root / "dummy.toml").write_text('[[rule]]\nrole = "worker"\nsteps = [{ dance = 1 }]\n')
    res = session(project, "worker", (), tmp_path, ticket="T-4").prompt("work", timeout=None)
    assert res.error and "exactly one of" in res.error
