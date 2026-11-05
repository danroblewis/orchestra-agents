"""The Supervisor's skills: every one its prompt's index names exists and is read with `read_skill`,
bounded like `read_guide`; on Claude Code the same texts are the session's own skills, written
outside the repository (``--add-dir``)."""

import re
import subprocess

import pytest
from conftest import agents_project

from orchestra_agents import prompting, skills, tooling
from orchestra_agents.builtins import ROLES
from orchestra_agents.harness.base import SessionSpec
from orchestra_agents.harness.claude_code import ClaudeCodeSession
from orchestra_agents.harness.tools import BY_NAME, tools_for
from orchestra_agents.org import built_in_agents
from orchestra_agents.store import AgentStore, StoreError

LISTED = re.compile(r"^- `([a-z][a-z0-9-]*)`: (.+)$", re.MULTILINE)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def index_of(project) -> dict[str, str]:
    prompt = prompting.agent_prompt(project, built_in_agents()["supervisor"])
    return dict(LISTED.findall(prompt.split("## Skills: read the one a request needs", 1)[1].split("\n## ", 1)[0]))


def test_every_skill_in_the_index_exists_and_says_when_to_use_it(project):
    listed = index_of(project)
    assert {"setup", "measure", "store-setup", "exploring", "jira", "github", "slack", "naming",
            "machines-from-labels", "digest", "pr-workflow", "design", "refine-map"} <= set(listed)
    for name, when in listed.items():
        assert (skills.SKILLS_DIR / f"{name}.md").is_file(), name
        assert when == skills.when(name) and len(when) < 200, name
        assert skills.text(project, name).startswith(f"# Skill: {name}\n"), name
    assert not [n for n in listed if skills.delegated(n)]                    # a delegated job's is its sub-session's


def test_each_skill_file_has_its_title_and_use_when_line():
    for p in sorted(skills.SKILLS_DIR.glob("*.md")):
        text = skills.raw(p.stem)
        assert f"# Skill: {p.stem}\n" in text and skills.WHEN.search(text), p.name
        assert not re.search(r"\{(operator|rules|skills|ticket_states)\}", text), p.name


def test_read_skill_is_the_supervisors_and_bounded(project, monkeypatch):
    store = AgentStore(project)
    assert "read_skill" in {t.name for t in tools_for(ROLES["supervisor"].tools)} and BY_NAME["read_skill"].group == "guides"
    assert max(len(skills.text(project, n)) for n in skills.names()) < skills.PIECE_CHARS   # each read whole today
    monkeypatch.setattr(skills, "PIECE_CHARS", 2_000)
    whole = skills.text(project, "store-setup")
    got, offset = [], 0
    while True:
        out = tooling.handle(project, store, "read_skill", {"name": "store-setup", "offset": offset}, role="supervisor")
        assert len(out["text"]) <= 2_000 and out["chars"] == len(whole)
        got.append(out["text"])
        if "next" not in out:
            break
        offset = int(re.search(r"offset (\d+)", out["next"]).group(1))
    assert "".join(got) == whole and len(got) >= len(whole) // 2_000
    with pytest.raises(StoreError, match="no skill"):
        tooling.handle(project, store, "read_skill", {"name": "../guide"}, role="supervisor")


def test_a_skill_is_filled_and_gated_as_the_prompt_is(project, monkeypatch):
    from orchestra_agents import installed
    monkeypatch.setattr(installed, "tasking", lambda: False)
    monkeypatch.setattr(installed, "workflows", lambda: False)
    text = skills.text(project, "measure")
    assert "{machines_doc}" not in text and "[[tasking]]" not in text and "[[no-tasking]]" not in text
    assert "not handed on here" in text and "[[no-workflows]]" not in text
    assert "tickets" not in skills.indexed()                                 # it holds only with the task system
    monkeypatch.setattr(installed, "tasking", lambda: True)
    assert "tickets" in skills.indexed() and "Coordinator" in skills.text(project, "measure")


def test_on_claude_code_the_skills_are_the_sessions_own_outside_the_repository(project, tmp_path):
    given = skills.for_session(project)
    spec = SessionSpec(session_id="supervisor", session_dir=tmp_path / "sessions", cwd=project.repo_root,
                       system_prompt="x", role="supervisor", tool_groups=("guides",), env={},
                       log_path=tmp_path / "log", skills=given)
    args = ClaudeCodeSession(spec, binary="claude")._args()
    root = args[args.index("--add-dir") + 1]
    assert not root.startswith(str(project.repo_root))                       # never written into the checkout
    for name, when, text in given:
        skill = (tmp_path / "sessions" / "claude-code" / "supervisor.skills" / ".claude" / "skills" / name / "SKILL.md").read_text()
        assert skill.startswith(f"---\nname: {name}\ndescription: \"Use when: ") and skill.endswith(text)
    plain = SessionSpec(session_id="worker-1", session_dir=tmp_path / "sessions", cwd=project.repo_root, system_prompt="x",
                        role="worker", tool_groups=(), env={}, log_path=tmp_path / "log")
    assert "--add-dir" not in ClaudeCodeSession(plain, binary="claude")._args()       # only a session given skills
