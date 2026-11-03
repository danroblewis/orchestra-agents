"""graduate_machine (machine_graduate.py, graduate_wiring.py): a program proven in
.orchestra/machines/<name>/ moves into the project's own tree, one fake project per language. Each
asserts the destination, the command, the [[machines]] entry as Orchestra's configuration reads it,
and that nothing else in .orchestra/ moves; the dry run first, the owner's quoted yes for the move."""

import json
import subprocess
import sys
import tomllib

import pytest
from design_fixtures import WHY, call, git, say

from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

YES = "Yes, graduate the relator into the project"
RELATOR = "import json\n\ndef main():\n    print(json.dumps({'kind': 'program'}))\n\nif __name__ == '__main__':\n    main()\n"


def tree(project, files: dict[str, str], command: str, *, commit: bool = True):
    """The project's files, a relator in .orchestra/machines/ and a second machine beside it, and the
    [[machines]] entry that runs the relator, committed."""
    root = project.repo_root
    for path, text in files.items():
        f = root / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    other = root / ".orchestra" / "machines" / "jira_poller"
    other.mkdir(parents=True, exist_ok=True)
    (other / "jira_poller.py").write_text("print('poll')\n")
    toml = root / "orchestra.toml"
    toml.write_text(toml.read_text() + f'\n[[machines]]\nname = "relator"\ncommand = "{command}"\nrestart = "always"\n')
    if commit:
        git(root, "add", "-A")
        git(root, "commit", "-qm", "the project")
    project.reload_config()


def graduate(project, store, **over):
    return call(project, store, "graduate_machine", **{"name": "relator", "reason": WHY, **over})


def go(project, store):
    """The dry run, then the owner's yes and the move."""
    dry = graduate(project, store)
    say(store, YES)
    return dry, graduate(project, store, dry_run=False, quote=YES)


def machines_entry(project, name="relator") -> dict:
    project.reload_config()
    return next(e for e in project.orchestra.config.raw["machines"] if e["name"] == name)


def still_there(project):
    """Nothing else in .orchestra/ moved: the other machine is where it was."""
    assert (project.repo_root / ".orchestra" / "machines" / "jira_poller" / "jira_poller.py").is_file()
    assert not (project.repo_root / ".orchestra" / "machines" / "relator").exists()


def test_python_goes_into_the_package_and_runs_as_a_module(project, store):
    tree(project, {"pyproject.toml": '[project]\nname = "acme-tools"\nversion = "0.1"\n', "acme_tools/__init__.py": "",
                   ".orchestra/machines/relator/relator.py": RELATOR},
         "python .orchestra/machines/relator/relator.py")
    dry = graduate(project, store)
    assert dry["dry_run"] and dry["plan"]["to"] == "acme_tools/relator" and dry["plan"]["command"] == "python3 -m acme_tools.relator"
    assert (project.repo_root / ".orchestra" / "machines" / "relator").is_dir()     # the dry run moved nothing
    say(store, YES)
    done = graduate(project, store, dry_run=False, quote=YES)
    assert done["ok"] and not done["dry_run"]
    dest = project.repo_root / "acme_tools" / "relator"
    assert (dest / "relator.py").read_text() == RELATOR                          # the code, and so its announcement, unchanged
    assert (dest / "__init__.py").exists() and "acme_tools.relator" in (dest / "__main__.py").read_text()
    assert machines_entry(project)["command"] == "python3 -m acme_tools.relator"
    assert machines_entry(project)["restart"] == "always"
    still_there(project)
    status = git(project.repo_root, "status", "--porcelain")
    assert "R  .orchestra/machines/relator/relator.py -> acme_tools/relator/relator.py" in status    # git mv, staged
    assert "orchestra.toml" not in status and "?? acme_tools/relator/__main__.py" in status     # clean to the gate but the move
    assert git(project.repo_root, "log", "--oneline").count("\n") == 2          # nothing committed to the base
    files = set(git(project.repo_root, "show", "--no-renames", "--name-only", "--format=", "orchestra/supervisor").split())
    assert files == {".orchestra/machines/relator/relator.py", "acme_tools/relator/relator.py", "acme_tools/relator/__init__.py",
                     "acme_tools/relator/__main__.py", "orchestra.toml"}                       # one commit on its branch
    assert done["branch"] == "orchestra/supervisor" and "pull request is pending" in done["said"][-1]
    assert "heard again under the same source" in done["verify"]
    ran = subprocess.run([sys.executable, "-m", "acme_tools.relator"], cwd=project.repo_root, capture_output=True, text=True,
                         check=False)
    assert ran.returncode == 0 and ran.stdout.strip() == '{"kind": "program"}'     # the module's runner runs the entry


def test_python_with_uv_runs_in_the_projects_own_python(project, store):
    tree(project, {"pyproject.toml": '[project]\nname = "acme"\n', "uv.lock": "", "src/acme/__init__.py": "",
                   ".orchestra/machines/relator/relator.py": RELATOR},
         "python .orchestra/machines/relator/relator.py")
    _, done = go(project, store)
    assert done["plan"]["to"] == "src/acme/relator"
    assert machines_entry(project)["command"] == "uv run python -m acme.relator"


def test_node_gets_a_script_entry_and_runs_with_npm(project, store):
    pkg = {"name": "acme", "version": "1.0.0", "scripts": {"test": "jest"}}
    tree(project, {"package.json": json.dumps(pkg, indent=2) + "\n", ".orchestra/machines/relator/relator.js": "console.log(1)\n"},
         "node .orchestra/machines/relator/relator.js")
    _, done = go(project, store)
    assert done["plan"]["to"] == "scripts/relator"
    assert (project.repo_root / "scripts" / "relator" / "relator.js").is_file()
    got = json.loads((project.repo_root / "package.json").read_text())
    assert got["scripts"] == {"test": "jest", "relator": "node scripts/relator/relator.js"}
    assert machines_entry(project)["command"] == "npm run relator"
    still_there(project)


def test_rust_gets_a_bin_and_runs_with_cargo(project, store):
    tree(project, {"Cargo.toml": '[package]\nname = "acme"\nversion = "0.1.0"\nedition = "2021"\n',
                   "src/main.rs": "fn main() {}\n", ".orchestra/machines/relator/main.rs": "fn main() {}\n"},
         "rust-script .orchestra/machines/relator/main.rs")
    _, done = go(project, store)
    assert done["plan"]["to"] == "src/bin/relator"
    cargo = tomllib.loads((project.repo_root / "Cargo.toml").read_text())
    assert cargo["bin"] == [{"name": "relator", "path": "src/bin/relator/main.rs"}]
    assert machines_entry(project)["command"] == "cargo run --bin relator"
    still_there(project)


def test_a_rust_crate_of_its_own_joins_the_workspace(project, store):
    tree(project, {"Cargo.toml": '[package]\nname = "acme"\nversion = "0.1.0"\nedition = "2021"\n', "src/main.rs": "fn main() {}\n",
                   ".orchestra/machines/relator/Cargo.toml": '[package]\nname = "relator"\nversion = "0.1.0"\n',
                   ".orchestra/machines/relator/src/main.rs": "fn main() {}\n"},
         "cargo run --manifest-path .orchestra/machines/relator/Cargo.toml -- --follow")
    _, done = go(project, store)
    assert done["plan"]["to"] == "crates/relator"
    assert (project.repo_root / "crates" / "relator" / "src" / "main.rs").is_file()
    assert tomllib.loads((project.repo_root / "Cargo.toml").read_text())["workspace"]["members"] == ["crates/relator"]
    assert machines_entry(project)["command"] == "cargo run -p relator -- --follow"
    still_there(project)


def test_a_collect_entry_keeps_its_arguments(project, store):
    tree(project, {"pyproject.toml": '[project]\nname = "acme"\n', "acme/__init__.py": "",
                   ".orchestra/machines/relator/relator.py": RELATOR},
         "python .orchestra/machines/relator/relator.py")
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text() + '\n[[collect]]\nname = "relator"\ncommand = "python .orchestra/machines/relator/relator.py measure"\nevery = "5m"\n')
    git(project.repo_root, "commit", "-qam", "collect")
    project.reload_config()
    go(project, store)
    project.reload_config()
    col = next(e for e in project.orchestra.config.raw["collect"] if e["name"] == "relator")
    assert col["command"] == "python3 -m acme.relator measure" and col["every"] == "5m"


def test_an_untracked_scaffold_moves_without_git(project, store):
    tree(project, {"pyproject.toml": '[project]\nname = "acme"\n', "acme/__init__.py": ""},
         "python .orchestra/machines/relator/relator.py")
    d = project.repo_root / ".orchestra" / "machines" / "relator"
    d.mkdir(parents=True)
    (d / "relator.py").write_text(RELATOR)
    project.reload_config()
    dry, _ = go(project, store)
    assert dry["plan"]["how"].startswith("move")
    assert (project.repo_root / "acme" / "relator" / "relator.py").is_file()


def test_the_move_is_the_owners_and_follows_a_dry_run(project, store):
    tree(project, {"pyproject.toml": '[project]\nname = "acme"\n', "acme/__init__.py": "",
                   ".orchestra/machines/relator/relator.py": RELATOR},
         "python .orchestra/machines/relator/relator.py")
    say(store, YES)
    with pytest.raises(StoreError, match="dry run"):
        graduate(project, store, dry_run=False, quote=YES)
    graduate(project, store)
    with pytest.raises(StoreError, match="owner"):
        graduate(project, store, dry_run=False)                 # no quote: the Supervisor's own wish moves nothing
    assert (project.repo_root / ".orchestra" / "machines" / "relator" / "relator.py").is_file()
    assert machines_entry(project)["command"] == "python .orchestra/machines/relator/relator.py"


def test_refused_without_an_entry_that_runs_it_or_with_an_edit_in_progress(project, store):
    tree(project, {"pyproject.toml": '[project]\nname = "acme"\n', "acme/__init__.py": "",
                   ".orchestra/machines/relator/relator.py": RELATOR},
         "python .orchestra/machines/jira_poller/jira_poller.py")
    with pytest.raises(StoreError, match="no \\[\\[machines\\]\\] entry runs"):
        graduate(project, store)
    (project.repo_root / ".orchestra" / "machines" / "relator" / "relator.py").write_text(RELATOR + "# wip\n")
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text().replace("jira_poller/jira_poller.py", "relator/relator.py"))
    git(project.repo_root, "commit", "-qm", "point", "--", "orchestra.toml")
    project.reload_config()
    with pytest.raises(StoreError, match="uncommitted change"):
        graduate(project, store)


def test_an_agents_spec_never_graduates(project, store):
    tree(project, {"pyproject.toml": '[project]\nname = "acme"\n', "acme/__init__.py": "",
                   ".orchestra/machines/relator/relator.py": RELATOR, ".orchestra/machines/relator/role.md": "# role\n"},
         "python .orchestra/machines/relator/relator.py")
    with pytest.raises(StoreError, match="agent's spec"):
        graduate(project, store)


def test_cargo_text_adds_to_an_existing_workspace_and_keeps_the_rest():
    from orchestra_agents.graduate_wiring import cargo_text
    text = '[package]\nname = "acme"  # ours\n\n[workspace]\nmembers = ["crates/a"]\n\n[dependencies]\nserde = "1"\n'
    got = cargo_text(text, {"kind": "cargo_member", "key": "relator", "value": "crates/relator", "what": "m"})
    assert tomllib.loads(got)["workspace"]["members"] == ["crates/relator", "crates/a"] and "# ours" in got
    empty = cargo_text('[package]\nname = "a"\n[workspace]\nmembers = []\n',
                       {"kind": "cargo_member", "key": "r", "value": "crates/r", "what": "m"})
    assert tomllib.loads(empty)["workspace"]["members"] == ["crates/r"]
