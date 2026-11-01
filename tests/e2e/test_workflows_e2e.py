"""The owner asks for a workflow in the chat, with the fake Claude Code: a real host, the turn a fake
`claude -p` whose tool call goes through the real MCP server, and the real Workflows plugin (skipped
when it is not installed) checking and writing the definition, then loading it back as its floor
would. Also: the guide's worked example is a definition the plugin accepts."""

import json
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from conftest import agents_project
from fake_tasks import FakeTasks

from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore

define = pytest.importorskip("orchestra_workflows.define")

FAKE = Path(__file__).parent / "fake_claude.py"
GUIDE = Path(__file__).parents[2] / "orchestra_agents" / "guide.md"
MESSAGE = "Please declare a two-step workflow: write then review."
DEFINITION = {"name": "write_review", "starts": [{"request": True}],
              "steps": [{"name": "write", "who": {"role": "worker"}, "needs": ["worktree"], "outcomes": {"ready": "review"}},
                        {"name": "review", "who": {"role": "reviewer"}, "outcomes": {"approved": None, "rejected": "write"},
                         "guards": {"rounds": 3, "then": "attention"}}]}


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def answers(text: str) -> list[dict]:
    assert text.startswith("What I did with my tools:"), text
    out = []
    for chunk in re.split(r"\n- (?=[a-z_]+: )", text.split("\n", 1)[1].removeprefix("- ")):
        tool, _, body = chunk.partition(": ")
        try:
            out.append({"tool": tool, **json.loads(body)})
        except ValueError:
            pytest.fail(f"{tool} did not answer with its result: {body[:400]}")
    return out


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ORCHESTRA_EVENTS_ENDPOINT", raising=False)
    monkeypatch.delenv("ORCHESTRA_EVENTS_TOKEN", raising=False)
    fake = tmp_path / "claude"
    shutil.copy(FAKE, fake)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "supervisor.json"
    monkeypatch.setenv("CLAUDE_BINARY", str(fake))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("FAKE_CLAUDE_SUPERVISOR", str(script))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    project = agents_project(repo, harness="claude_code")
    toml = repo / "orchestra.toml"
    toml.write_text(toml.read_text() + 'tools_via = "mcp"\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    project.reload_config()
    monkeypatch.setenv("ORCHESTRA_REPO", str(repo))
    events = Events.for_project(project)
    host = AgentHost(project, AgentStore(project, events), events)
    host._refresh_org(host.store.runtime()["agent_state"])
    with FakeTasks(project):
        def say(message: str, steps: list[dict]) -> list[dict]:
            script.write_text(json.dumps(steps))
            out = host.prompt_supervisor(message, lambda e: None)
            assert not out.get("error"), out
            got = answers(out["text"])
            assert [a["tool"] for a in got] == [s["tool"] for s in steps]
            return got
        try:
            yield say, project, repo
        finally:
            host.residents.close_all()


def test_the_owner_asks_for_a_two_step_workflow_and_the_plugin_loads_it(chat):
    say, project, repo = chat
    head = git(repo, "rev-parse", "HEAD")
    got = say(MESSAGE, [
        {"tool": "declare_workflow", "args": {"definition": DEFINITION, "quote": "declare a two-step workflow: write then review",
                                              "reason": "the owner asked for a write-then-review workflow"}},
        {"tool": "list_workflows", "args": {}}])
    declared, listed = got
    assert declared["ok"] is True, declared
    path = project.orchestra.state_dir / "workflows" / "write_review" / "workflow.json"
    assert declared["path"] == str(path) and declared["root"] == "state" and declared["author"] == "owner"
    assert isinstance(declared["notes"], list) and "/workflows" in declared["shown"]
    assert json.loads(path.read_text()) == DEFINITION
    loaded = define.load(project.orchestra)
    (wf,) = [w for w in loaded.workflows if w.name == "write_review"]
    assert [s.name for s in wf.steps] == ["write", "review"]
    assert [(s.actor.kind, s.actor.name) for s in wf.steps] == [("role", "worker"), ("role", "reviewer")]
    assert listed["ok"] and [w["name"] for w in listed["workflows"]] == ["write_review"]
    assert [s["who"] for s in listed["workflows"][0]["steps"]] == [{"role": "worker"}, {"role": "reviewer"}]
    assert git(repo, "rev-parse", "HEAD") == head and not git(repo, "status", "--porcelain").strip()


def test_a_refused_definition_comes_back_with_its_problems(chat):
    say, project, _ = chat
    bad = {**DEFINITION, "steps": [{**DEFINITION["steps"][0], "outcomes": {"ready": "reviw"}}, DEFINITION["steps"][1]]}
    (got,) = say(MESSAGE, [{"tool": "declare_workflow", "args": {"definition": bad, "reason": "the owner asked for it"}}])
    assert got.get("ok") is not True and "nothing written" in json.dumps(got) and "reviw" in json.dumps(got)
    assert not (project.orchestra.state_dir / "workflows" / "write_review").exists()


def test_the_guides_worked_example_is_a_definition_the_plugin_accepts(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    project = agents_project(repo)
    text = GUIDE.read_text(encoding="utf-8")
    example = text.split("A worked example.", 1)[1]
    raw = re.search(r"`declare_workflow` with `(\{.*?\})`,", example, re.DOTALL).group(1)
    definition = json.loads(re.sub(r"\s+", " ", raw))
    from orchestra_workflows.define.check import problems
    assert problems(project.orchestra, definition) == []
