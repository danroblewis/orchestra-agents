"""The agents read and change configuration only through Orchestra's objects (Orchestra's AGENTS.md
rule 10): no module opens orchestra.toml, a section set only in the owner's private overlay reaches
the host, and the Supervisor's setup tools write to the overlay when asked and to the repository
(committed) otherwise, keeping comments, validated, and followed by the host."""

import ast
import builtins
import inspect
import subprocess
import tomllib
from pathlib import Path

import pytest
from conftest import FakeHarness, agents_project
from orchestra.config import load_project as load_orchestra_project

import orchestra_agents
from orchestra_agents import tooling
from orchestra_agents.config import load_project
from orchestra_agents.events import Events
from orchestra_agents.harness import dummy_agent
from orchestra_agents.host import AgentHost
from orchestra_agents.store import AgentStore, StoreError
from orchestra_agents.web import plugin, processes

PACKAGE = Path(orchestra_agents.__file__).parent
NAMES = {"orchestra.toml", "orchestra.local.toml"}
FILE_IO = {"read_text", "read_bytes", "write_text", "write_bytes", "open", "unlink", "replace", "rename"}
WHY = "the owner keeps their own settings out of the team's repository"


def _opens_the_config(tree: ast.AST) -> list[str]:
    """Where code builds a path to the configuration file, or opens or edits one."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and (
                isinstance(node.right, ast.Constant) and node.right.value in NAMES
                or isinstance(node.right, ast.Name) and node.right.id in ("CONFIG_FILE", "OVERLAY_FILE")):
            out.append(f"line {node.lineno}: a path to {ast.unparse(node.right)}")
        elif isinstance(node, ast.Call) and any(isinstance(a, ast.Constant) and a.value in NAMES for a in node.args):
            out.append(f"line {node.lineno}: {ast.unparse(node.func)}({ast.unparse(node.args[0])})")
        elif (isinstance(node, ast.Attribute) and node.attr in FILE_IO and isinstance(node.value, ast.Attribute)
              and node.value.attr in ("config_file", "overlay_file")):
            out.append(f"line {node.lineno}: {ast.unparse(node)}")              # naming the file (git, a record) is fine
        elif isinstance(node, ast.Call) and ast.unparse(node.func) == "open" and node.args and any(
                isinstance(n, ast.Attribute) and n.attr in ("config_file", "overlay_file") for n in ast.walk(node.args[0])):
            out.append(f"line {node.lineno}: open({ast.unparse(node.args[0])})")
        elif isinstance(node, ast.Name) and node.id == "tomlkit" or isinstance(node, ast.Import) and any(
                a.name == "tomlkit" for a in node.names):
            out.append(f"line {node.lineno}: edits TOML itself (tomlkit)")
    return out


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ORCHESTRA_CONFIG_OVERLAY", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    toml = repo / "orchestra.toml"
    toml.write_text(toml.read_text() + '\n# the task system\n[tasks]\nworkers = 1          # one at a time for now\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    return p


def overlay(project, text: str) -> Path:
    path = project.orchestra.overlay_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_no_module_of_the_agents_opens_or_edits_orchestra_toml():
    found = {str(f.relative_to(PACKAGE)): bad for f in sorted(PACKAGE.rglob("*.py"))
             if (bad := _opens_the_config(ast.parse(f.read_text(encoding="utf-8"))))}
    assert found == {}, f"read and change configuration through Orchestra's objects (project.config, config_edit): {found}"


def test_loading_reads_no_config_file_from_the_agents(project, monkeypatch):
    readers = []
    real_open, real_read = builtins.open, Path.read_text

    def caller() -> str:
        return inspect.currentframe().f_back.f_back.f_globals.get("__name__", "")

    def watch_open(file, *a, **k):
        if Path(str(file)).name in NAMES:
            readers.append(caller())
        return real_open(file, *a, **k)

    def watch_read(self, *a, **k):
        if self.name in NAMES:
            readers.append(caller())
        return real_read(self, *a, **k)

    monkeypatch.setattr(builtins, "open", watch_open)
    monkeypatch.setattr(Path, "read_text", watch_read)
    p = load_project(project.repo_root)
    p.reload_config()
    plugin(load_orchestra_project(project.repo_root))
    monkeypatch.setenv("ORCHESTRA_REPO", str(project.repo_root))
    dummy_agent.script_path()
    assert readers and all(not r.startswith("orchestra_agents") for r in readers), readers


def test_a_section_set_only_in_the_overlay_reaches_the_host(project, monkeypatch):
    overlay(project, '[agents]\nserves = ["reviewer"]\ndummy_script = "mine.toml"\n')
    assert load_project(project.repo_root).config.serves == ["reviewer"]
    host = AgentHost(project, AgentStore(project), Events.for_project(project))
    host.harness = FakeHarness()
    host.tick()                                                       # the host reloads on every tick
    assert host.cfg.serves == ["reviewer"]
    overlay(project, '[agents]\nserves = ["worker"]\n')
    host.tick()
    assert host.cfg.serves == ["worker"]
    monkeypatch.setenv("ORCHESTRA_REPO", str(project.repo_root))
    overlay(project, '[agents]\ndummy_script = "mine.toml"\n')
    assert dummy_agent.script_path() == project.repo_root / "mine.toml"
    overlay(project, '[agents]\nserves = ["worker"]\n')
    assert dummy_agent.script_path() is None
    (project.repo_root / ".orchestra").mkdir(exist_ok=True)
    (project.repo_root / ".orchestra" / "dummy.toml").write_text("")
    assert dummy_agent.script_path() == project.repo_root / ".orchestra" / "dummy.toml"     # the repository's own, by default


def test_the_host_runs_without_an_agents_section_and_enabled_false_in_the_overlay_stops_it(tmp_path, monkeypatch):
    """[agents] is optional (the owner, 2026-10-28): installing the plugin runs the host, so the
    Supervisor can set a new project up; enabled = false, wherever it is set, turns it off."""
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "bare"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "orchestra.toml").write_text('[events]\nurl = "file"\n')
    o = load_orchestra_project(repo)
    assert [x.name for x in processes(load_project(repo))] == ["host"]
    o.config_edit.set("agents.enabled", False, target="overlay")
    assert plugin(load_orchestra_project(repo)).processes == []


def call(project, store, tool, **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role="supervisor")


def test_the_supervisor_writes_to_the_overlay_when_asked_and_to_the_repository_otherwise(project):
    store = AgentStore(project, Events.for_project(project, source="supervisor"))
    repo_toml = project.repo_root / "orchestra.toml"
    before, head = repo_toml.read_text(), git(project.repo_root, "rev-parse", "HEAD")
    host = AgentHost(project, AgentStore(project), Events.for_project(project))
    host.harness = FakeHarness()

    out = call(project, store, "set_setting", key="desk.channels", value='["#mine", "@me"]', target="overlay", reason=WHY)
    assert out["ok"] and out["target"] == "overlay" and out["commit"] is None
    assert repo_toml.read_text() == before and git(project.repo_root, "rev-parse", "HEAD") == head    # nothing shared
    assert tomllib.loads(project.orchestra.overlay_file.read_text())["desk"]["channels"] == ["#mine", "@me"]
    assert project.orchestra.config.raw["desk"]["channels"] == ["#mine", "@me"]
    [ev] = [e for e in store.events.read(0, 1000)[0] if e["type"] == "config.changed"]
    assert ev["target"] == "overlay" and "#mine" not in str(ev)                                    # its values stay private

    out = call(project, store, "set_agent_roles", serves=["reviewer"], target="overlay", reason=WHY)
    host.tick()
    assert host.cfg.serves == ["reviewer"] and out["commit"] is None and repo_toml.read_text() == before

    out = call(project, store, "set_capacity_limit", workers=3, reason="three tickets can run side by side now")
    assert out["target"] == "repo" and out["commit"]
    text = repo_toml.read_text()
    assert "workers = 3\n" in text and "# the task system" in text and len(text.splitlines()) == len(before.splitlines())
    assert git(project.repo_root, "show", "--name-only", "--format=", "HEAD").split() == ["orchestra.toml"]
    assert project.orchestra.config.raw["tasks"]["workers"] == 3

    out = call(project, store, "set_agent_roles", serves=["worker"], reason="the repository's own default changes")
    assert out["commit"] and "overlay" in out["note"]                                   # the owner's overlay still wins, and it is said
    host.tick()
    assert host.cfg.serves == ["reviewer"]

    with pytest.raises(StoreError, match="restart must be one of"):              # what Orchestra refuses is not written
        call(project, store, "set_machine", name="pager", command="pager.sh", restart="sometimes", target="overlay",
             reason="the owner's own pager")
    with pytest.raises(StoreError, match="target is repo"):
        call(project, store, "set_setting", key="desk.me", value="U1", target="elsewhere", reason=WHY)
    with pytest.raises(StoreError, match="looks like a secret"):
        call(project, store, "set_setting", key="desk.token", value="xoxb-1234567890-abcdefghij", target="overlay", reason=WHY)

    call(project, store, "set_setting", key="desk.me", value="U0123", target="overlay", reason=WHY)          # a bare word
    assert project.orchestra.config.raw["desk"]["me"] == "U0123"
    out = call(project, store, "set_setting", key="desk.channels", value="null", target="overlay", reason=WHY)
    assert "desk" not in project.orchestra.config.raw or "channels" not in project.orchestra.config.raw["desk"]


def test_the_supervisor_is_told_where_each_setting_goes(project):
    from orchestra_agents.prompting import role_prompt
    text = role_prompt(project, "supervisor")
    assert "`target`" in text and "overlay" in text and "identities" in text
