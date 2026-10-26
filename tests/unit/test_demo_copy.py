"""The demo's shell worker is a copy of the task system's (the task system's wheel does not ship
its demo, and this demo must show a program and an agent on one queue). The copy may differ only
in its module docstring."""

import ast
from pathlib import Path

import pytest

OURS = Path(__file__).resolve().parents[2] / "demo" / "machines" / "shell_worker.py"


def body(path: Path) -> str:
    """The module's source after its docstring."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    first = tree.body[1] if ast.get_docstring(tree) is not None and len(tree.body) > 1 else tree.body[0]
    return "\n".join(src.splitlines()[first.lineno - 1:])


def test_the_shell_worker_is_the_task_systems():
    tasking = pytest.importorskip("tasking")
    theirs = Path(tasking.__file__).resolve().parents[1] / "demo" / "machines" / "shell_worker.py"
    if not theirs.exists():
        pytest.skip("orchestra-tasking is installed without its demo")
    assert body(OURS) == body(theirs), f"demo/machines/shell_worker.py differs from the task system's: change {theirs} and copy it to {OURS}"
