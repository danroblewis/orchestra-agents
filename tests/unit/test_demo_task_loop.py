"""The live demo's task-loop mode (demo/run_task_loop.py): its command line, its refusal to start
beside the task system, its batches, and the project it makes."""

import importlib.metadata
import importlib.util
import subprocess
from pathlib import Path

import pytest

from demo import run
from demo import run_task_loop as tl


def test_the_defaults_ask_for_five_items_every_five_minutes_without_end():
    a = tl.args([])
    assert (a.port, a.batch, a.every, a.items, a.dummy) == (8072, 5, 300.0, 0, False)
    assert a.target == (Path.home() / "orchestra-task-loop-demo").resolve()


def test_it_takes_run_pys_command_line_and_the_pacing(tmp_path):
    a = tl.args([str(tmp_path / "p"), "--port", "8211", "--dummy", "--batch", "3", "--every", "60", "--items", "9"])
    assert (a.target, a.port, a.dummy, a.batch, a.every, a.items) == (tmp_path / "p", 8211, True, 3, 60.0, 9)


@pytest.mark.parametrize("bad", [["--batch", "0"], ["--every", "0"], ["--every", "-5"], ["--items", "-1"],
                                 ["--port", "0"], ["--port", "70000"], ["--batch", "two"]])
def test_it_refuses_pacing_that_asks_for_nothing(bad, capsys):
    with pytest.raises(SystemExit) as e:
        tl.args(bad)
    assert e.value.code == 2


def test_batches_are_even_until_the_last_and_endless_without_items():
    assert list(tl.batches(5, 12)) == [5, 5, 2]
    assert list(tl.batches(5, 5)) == [5]
    endless = tl.batches(4, 0)
    assert [next(endless) for _ in range(100)] == [4] * 100


def test_items_are_named_as_the_scripted_machines_expect():
    assert [tl.item(n) for n in (3, 6, 120)] == ["task-03", "task-06", "task-120"]


def _no_tasking(monkeypatch):
    real_version, real_find = importlib.metadata.version, importlib.util.find_spec

    def version(name):
        if name == "orchestra-tasking":
            raise importlib.metadata.PackageNotFoundError(name)
        return real_version(name)

    monkeypatch.setattr(importlib.metadata, "version", version)
    monkeypatch.setattr(importlib.util, "find_spec", lambda m, *a: None if m in ("tasking", "orchestra_tasking") else real_find(m, *a))
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: [])


def test_without_the_task_system_nothing_is_refused(monkeypatch):
    _no_tasking(monkeypatch)
    assert tl.tasking_present() is None
    tl.refuse_tasking()


def test_the_task_systems_distribution_is_refused(monkeypatch):
    _no_tasking(monkeypatch)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.1.0")
    assert tl.tasking_present() == "orchestra-tasking 0.1.0 is installed"
    with pytest.raises(SystemExit) as e:
        tl.refuse_tasking()
    assert "--no-install-package orchestra-tasking" in str(e.value)


def test_the_task_systems_module_alone_is_refused(monkeypatch):
    _no_tasking(monkeypatch)
    monkeypatch.setattr(importlib.util, "find_spec", lambda m, *a: object() if m == "tasking" else None)
    assert tl.tasking_present() == "the module tasking is importable"


def test_the_task_systems_plugin_alone_is_refused(monkeypatch):
    _no_tasking(monkeypatch)
    ep = importlib.metadata.EntryPoint("tasks", "tasking.web:plugin", "orchestra.plugins")
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: [ep])
    assert tl.tasking_present() == "the plugin tasks (tasking.web:plugin) is installed"


def test_main_refuses_before_it_makes_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "tasking_present", lambda: "orchestra-tasking 0.1.0 is installed")
    with pytest.raises(SystemExit):
        tl.main([str(tmp_path / "p"), "--port", "8211"])
    assert not (tmp_path / "p").exists()


def test_the_project_is_the_example_with_its_origin_inside_it(tmp_path, monkeypatch):
    pytest.importorskip("orchestra_workflows.define")
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    target = tl.make_project(tmp_path / "p")
    git = lambda *a: subprocess.run(["git", *a], cwd=target, capture_output=True, text=True, check=True).stdout.strip()
    assert git("remote", "get-url", "origin") == str(target / ".git" / "origin.git")
    assert git("rev-parse", "origin/main") == git("rev-parse", "main")
    assert git("status", "--porcelain") == ""
    assert (target / ".orchestra" / "workflows" / "task_loop" / "workflow.json").is_file()
    assert (target / ".orchestra" / "PROJECT.md").read_text() == tl.CONTEXT
    toml = (target / "orchestra.toml").read_text()
    assert 'harness = "dummy"' in toml and "serves = []" in toml and "tasking" not in toml


def test_the_agents_demo_copies_none_of_the_task_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    target = run.make_project(tmp_path / "demo", dummy=True)
    assert not [p.name for p in target.iterdir() if "task_loop" in p.name or "task-loop" in p.name]
