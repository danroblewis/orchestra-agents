"""The project's CI read from its workflow files (project_ci.py) and named in check_setup's answer
(bootstrap run 3: "CI is defined but has never run", naming neither GitHub Actions nor ci.yml)."""

from pathlib import Path

from orchestra_agents import observe, project_ci

pytest_plugins = ["design_fixtures"]

CI = """\
# the team's checks
name: CI
on:
  push:
    branches: [main]
  pull_request:
jobs:
  test:
    name: Unit tests
    runs-on: ubuntu-latest
    steps:
      - run: cargo nextest run
  lint:
    runs-on: ubuntu-latest
"""

NIGHTLY = "name: nightly\non: [schedule, workflow_dispatch]\njobs:\n  survey:\n    runs-on: self-hosted\n"


def fake_project(tmp_path: Path) -> Path:
    d = tmp_path / "repo" / ".github" / "workflows"
    d.mkdir(parents=True)
    (d / "ci.yml").write_text(CI)
    (d / "nightly.yaml").write_text(NIGHTLY)
    return tmp_path / "repo"


def test_each_workflow_is_named_with_its_triggers_and_jobs(tmp_path):
    got = project_ci.summary(fake_project(tmp_path))
    by = {w["file"]: w for w in got["workflows"]}
    assert by[".github/workflows/ci.yml"] == {"file": ".github/workflows/ci.yml", "name": "CI",
                                              "on": ["push", "pull_request"], "jobs": ["test (Unit tests)", "lint"]}
    assert by[".github/workflows/nightly.yaml"]["on"] == ["schedule", "workflow_dispatch"]
    assert got["line"].startswith("The project's CI is GitHub Actions: .github/workflows/ci.yml (\"CI\": on push, "
                                  "pull_request; jobs test (Unit tests), lint)")


def test_no_workflow_is_said_so(tmp_path):
    assert project_ci.summary(tmp_path)["line"].startswith("No CI workflow in .github/workflows")


def test_check_setup_carries_it(project, store):
    d = project.repo_root / ".github" / "workflows"
    d.mkdir(parents=True)
    (d / "ci.yml").write_text(CI)
    got = observe.check_setup(project, store)
    assert "GitHub Actions: .github/workflows/ci.yml" in got["ci"]["line"]


def test_the_first_look_prompt_names_ci_and_keeps_inside_the_project():
    root = Path(project_ci.__file__).parent
    text = (root / "prompts" / "skills" / "setup.md").read_text()
    assert "Name the project's CI as `check_setup`'s `ci` reads it" in text
    assert "never offer to read a folder outside it" in text
