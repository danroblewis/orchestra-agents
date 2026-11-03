"""machine_ages (machine_ages.py): each machine heard on the bus in its age, joined to the project's
[[machines]] entries by the log it announced, its name, or its directory; the counts, who is due to
graduate, and the digest's line. The machines view is stood in for (/api/machines' shape)."""

import time

import pytest
from design_fixtures import call, git

from orchestra_agents import machine_ages, machines_read

pytest_plugins = ["design_fixtures"]
DAY = 86_400


@pytest.fixture
def heard(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(machines_read, "view", lambda store: {"machines": rows})
    return rows


def project_with(project, entries: dict[str, str], dirs=("relator",)):
    root = project.repo_root
    for d in dirs:
        (root / ".orchestra" / "machines" / d).mkdir(parents=True, exist_ok=True)
        (root / ".orchestra" / "machines" / d / f"{d}.py").write_text("print(1)\n")
    (root / "acme").mkdir(exist_ok=True)
    toml = root / "orchestra.toml"
    toml.write_text(toml.read_text() + "".join(f'\n[[machines]]\nname = "{n}"\ncommand = "{c}"\n' for n, c in entries.items()))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "machines")
    project.reload_config()


def test_each_machine_in_its_age(project, store, heard):
    project_with(project, {"relator": "python .orchestra/machines/relator/relator.py", "placer-1": "python3 -m acme.placer"})
    log = project.orchestra.state_dir / "logs" / "machines" / "placer-1.log"
    store.events.publish("orchestra.machine.started", {"kind": "program", "mode": "continuous", "log": str(log)},
                         source="place_ticket/1")
    now = time.time()
    heard += [{"id": "relator", "kind": "program", "first_seen": now - 9 * DAY},
              {"id": "place_ticket", "kind": "program", "first_seen": now - DAY},
              {"id": "supervisor", "kind": "agent", "first_seen": now},
              {"id": "bot.jira", "kind": None, "first_seen": now},
              {"id": "owner", "kind": "person", "first_seen": now},
              {"id": "orchestra-host", "bootstrap": True}]
    got = call(project, store, "machine_ages")
    by = {r["id"]: r for r in got["machines"]}
    assert by["relator"]["age"] == "program in .orchestra/" and by["relator"]["joined"].startswith("by name (fallback")
    assert by["place_ticket"]["age"] == "graduated" and by["place_ticket"]["joined"].startswith("by the log it announced")
    assert by["supervisor"]["age"] == "agent" and by["owner"]["age"] == "other"
    assert by["bot.jira"]["age"] == "unknown"
    assert "orchestra-host" not in by
    assert got["counts"] == {"agent": 1, "program in .orchestra/": 1, "graduated": 1, "unknown": 1, "other": 1}
    assert got["due_to_graduate"] == ["relator"]                       # heard for nine days; place_ticket graduated already
    assert got["line"] == ("Machines by age: 1 agent, 1 program in .orchestra/, 1 graduated program, 1 unknown; due to "
                           "graduate: relator; agents due to become programs: not measured.")
    assert "not measured" in got["agents_due_to_become_programs"]
    assert got["share"] == "1 of 2 programs live in .orchestra/"


def test_the_majority_in_orchestra_is_said(project, store, heard):
    project_with(project, {"relator": "python .orchestra/machines/relator/relator.py"}, dirs=("relator", "linker"))
    heard += [{"id": "relator", "kind": "program", "first_seen": time.time()},
              {"id": "linker", "kind": "program", "first_seen": None}]
    got = machine_ages.machine_ages(project, store)
    by = {r["id"]: r for r in got["machines"]}
    assert by["linker"]["age"] == "program in .orchestra/" and "no [[machines]] entry" in by["linker"]["joined"]
    assert got["due_to_graduate"] == [] and "the majority" in got["share"]


def test_the_digest_says_the_ages_before_the_model_line(project, store, heard):
    heard.append({"id": "supervisor", "kind": "agent", "first_seen": time.time()})
    store.events.publish("work.done", {}, source="supervisor")
    got = call(project, store, "read_events", since="1h")
    paras = got["digest"].split("\n\n")
    assert paras[-2].startswith("Machines by age: 1 agent, 0 programs in .orchestra/, 0 graduated") and paras[-1] == got["closing_line"]


def test_graduated_by_the_launchers_process(project, store, heard):
    project_with(project, {"placer-1": "python3 -m acme.placer", "relator": "python .orchestra/machines/relator/relator.py"})
    heard += [{"id": "place_ticket", "kind": "program", "processes": ["placer-1"], "first_seen": time.time()},
              {"id": "linker", "kind": "program", "processes": ["relator"], "first_seen": time.time()}]
    got = machine_ages.machine_ages(project, store)
    by = {r["id"]: r for r in got["machines"]}
    assert by["place_ticket"]["age"] == "graduated" and by["place_ticket"]["entry"] == "placer-1"
    assert by["place_ticket"]["by"] == "process" and "launcher's process" in by["place_ticket"]["joined"]
    assert by["linker"]["age"] == "program in .orchestra/" and by["linker"]["entry"] == "relator"   # its process, not its name
    assert got["fell_back"] == [] and "fallback" not in got["note"]


def test_a_plugins_process_is_never_a_machines_age(project, store, heard):
    project_with(project, {"relator": "python .orchestra/machines/relator/relator.py"})
    heard += [{"id": "relator", "kind": "program", "processes": ["workflows.programs"], "first_seen": time.time()},
              {"id": "poller", "kind": "program", "processes": ["bridges.poller"], "first_seen": time.time()}]
    got = machine_ages.machine_ages(project, store)
    by = {r["id"]: r for r in got["machines"]}
    assert "entry" not in by["relator"] and by["relator"]["age"] == "program in .orchestra/"   # its directory, not the entry by name
    assert by["relator"]["plugin_process"] == "workflows.programs"
    assert by["poller"]["age"] == "unknown" and "plugin's process bridges.poller" in by["poller"]["joined"]
    assert got["fell_back"] == [] and got["counts"]["graduated"] == 0


def test_the_fallback_is_used_and_said(project, store, heard):
    project_with(project, {"placer-1": "python3 -m acme.placer"})
    log = project.orchestra.state_dir / "logs" / "machines" / "placer-1.log"
    store.events.publish("orchestra.machine.started", {"kind": "program", "mode": "continuous", "log": str(log)},
                         source="place_ticket")
    heard += [{"id": "place_ticket", "kind": "program", "first_seen": time.time()},
              {"id": "gone", "kind": "program", "processes": ["old-entry"], "first_seen": time.time()}]
    got = machine_ages.machine_ages(project, store)
    by = {r["id"]: r for r in got["machines"]}
    assert by["place_ticket"]["age"] == "graduated" and by["place_ticket"]["by"] == "fallback"
    assert by["place_ticket"]["joined"] == "by the log it announced (fallback: its announcement names no process)"
    assert by["gone"]["by"] == "fallback" and by["gone"]["age"] == "unknown"
    assert got["fell_back"] == ["place_ticket", "gone"] and "the fallback): place_ticket, gone" in got["note"]
