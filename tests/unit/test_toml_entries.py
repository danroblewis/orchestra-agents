"""Edits to orchestra.toml keep what they do not change: comments, blank lines, the order."""

import tomllib

import pytest
import tomlkit

from orchestra_agents.store import StoreError
from orchestra_agents.toml_entries import remove, set_key, table, upsert

TEXT = """[project]
work = "tasks"           # a scratch folder per ticket
base_branch = "main"     # where work lands

# programs
[[machines]]
name = "a"
command = "a.sh"

[[machines]]
name = "b"
# b's own note
command = "b.sh"

# ---- the task system ----

[gate]
timeout = "300s"

[[gate.unit]]            # what a branch must pass
name = "unit"
command = "pytest"
"""


def test_removing_an_entry_keeps_the_comments_that_introduce_what_follows():
    out = remove(TEXT, "machines", "b")
    assert "# ---- the task system ----" in out and "b's own note" not in out and "\n\n\n" not in out
    assert [m["name"] for m in tomllib.loads(out)["machines"]] == ["a"]
    out = remove(out, "machines", "a")
    assert "machines" not in tomllib.loads(out) and "# programs" in out and "# ---- the task system ----" in out
    out = remove(TEXT, "gate.unit", "unit")
    assert tomllib.loads(out)["gate"] == {"timeout": "300s"} and out.endswith('timeout = "300s"\n')
    with pytest.raises(StoreError, match="no \\[\\[machines\\]\\] entry named 'c'"):
        remove(TEXT, "machines", "c")


def test_a_changed_value_loses_its_old_comment_and_an_added_entry_is_set_apart():
    doc = tomlkit.parse(TEXT)
    set_key(table(doc, "project"), "work", "code")
    set_key(table(doc, "project"), "base_branch", "main")                  # unchanged: its comment stays
    assert upsert(doc, "collect", "disk", {"command": "machines/disk.sh"}) is None
    assert upsert(doc, "machines", "a", {"command": "a2.sh"}) == {"name": "a", "command": "a.sh"}
    out = tomlkit.dumps(doc)
    assert 'work = "code"\n' in out and "# where work lands" in out
    assert '\n\n[[collect]]\nname = "disk"\ncommand = "machines/disk.sh"\n' in out
    assert tomllib.loads(out)["machines"][0] == {"name": "a", "command": "a2.sh"}
