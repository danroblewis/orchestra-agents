"""The Supervisor's own changes to the repository: each is a commit on its branch
``orchestra/supervisor``, never on the base branch, live at once, and a pull request through
orchestra-scm's provider on the fake forge (the template's Summary, Testing and Jira ID); without a
remote or ``[scm]`` the branch is made and the pull request said pending; with the Outbox
installed the pull request is a draft for the owner's approval."""

from __future__ import annotations

import subprocess

import pytest
from conftest import agents_project
from orchestra_scm.fakes import FakeForge
from orchestra_scm.template import FOOTER, headings

from orchestra_agents import since_digest, supervisor_pr, tooling
from orchestra_agents.events import Events
from orchestra_agents.side_branch import SIDE
from orchestra_agents.store import AgentStore

WHY = "the owner wants two workers so the queue drains before the release"


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def _project(repo, scm: str = ""):
    p = agents_project(repo)
    toml = repo / "orchestra.toml"
    toml.write_text(toml.read_text() + '\n[project]\nbase_branch = "main"\n' + scm)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "the project")
    p.reload_config()
    return p


@pytest.fixture
def forge(tmp_path):
    with FakeForge(tmp_path / "remote") as f:
        yield f


@pytest.fixture
def project(tmp_path, monkeypatch, forge):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(supervisor_pr, "outbox_installed", lambda: False)
    repo = forge.clone(tmp_path / "repo")
    p = _project(repo, f'\n[scm]\nprovider = "branch_pr"\nrepo = "{forge.repo}"\nbase = "main"\napi_url = "{forge.url}"\n')
    git(repo, "push", "-q", "origin", "main")
    return p


@pytest.fixture
def store(project):
    return AgentStore(project, Events.for_project(project, source="supervisor"))


def call(project, store, tool, **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role="supervisor")


def owner_says(store, message: str) -> None:
    store.set_agent_state("supervisor", turn={"id": "t1", "message": message})


def test_a_change_is_a_branch_commit_live_at_once_and_a_pull_request_with_the_template(project, store, forge):
    base = git(project.repo_root, "rev-parse", "main").strip()
    owner_says(store, "Please give us two workers for DEMO-42 before the release.")
    out = call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    assert git(project.repo_root, "rev-parse", "main").strip() == base                 # never on the base
    assert git(project.repo_root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert project.orchestra.config.raw["tasks"]["workers"] == 2                         # live, through the overlay
    assert "workers" not in (project.repo_root / "orchestra.toml").read_text()          # the checkout untouched
    tip = git(project.repo_root, "rev-parse", SIDE).strip()
    assert out["branch"] == SIDE and tip.startswith(out["commit"])
    assert git(project.repo_root, "show", "--name-only", "--format=", SIDE).split() == ["orchestra.toml"]
    assert git(project.repo_root, "status", "--porcelain").strip() == ""                  # clean, so the merge gate lands
    pr = out["pull_request"]
    assert pr["number"] and pr["url"].endswith(f"/pull/{pr['number']}") and f"#{pr['number']}" in out["said"]
    assert forge.ref(SIDE) == tip and forge.ref("main") == base                         # the branch pushed, main not
    body = forge.body_of(pr["number"])
    assert headings(body) == ["Summary", "Testing", "Jira ID"]
    assert WHY in body and "configuration checks" in body
    assert body.split("## Jira ID", 1)[1].strip().splitlines()[0] == "DEMO-42"
    assert body.rstrip().endswith(FOOTER)
    assert not [r for r in forge.requests if r.path.endswith("/merge")]

    again = call(project, store, "set_capacity_limit", workers=3, reason="three workers: the queue still grows")
    assert again["pull_request"]["number"] == pr["number"] and len(forge.data["pulls"]) == 1   # updated, not opened twice
    assert forge.ref(SIDE).startswith(again["commit"]) and git(project.repo_root, "rev-parse", "main").strip() == base


def test_the_jira_id_is_none_when_the_owner_names_no_ticket(project, store, forge):
    out = call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    body = forge.body_of(out["pull_request"]["number"])
    assert body.split("## Jira ID", 1)[1].strip().splitlines()[0] == "none"


def test_without_a_remote_the_branch_is_made_and_the_pull_request_is_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    project = _project(repo)
    store = AgentStore(project, Events.for_project(project, source="supervisor"))
    base = git(repo, "rev-parse", "main").strip()
    out = call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    assert out["ok"] and git(repo, "rev-parse", SIDE).strip().startswith(out["commit"])
    assert git(repo, "rev-parse", "main").strip() == base
    assert out["pull_request"]["number"] is None and "[scm]" in out["pull_request"]["pending"]
    assert "pending" in out["said"] and SIDE in out["said"]
    line = supervisor_pr.digest_line(project, store)
    assert line and "pending" in line and SIDE in line


def test_with_the_outbox_installed_the_pull_request_is_a_draft_for_the_owner(project, store, forge, monkeypatch):
    drafts = []

    class Outbox:
        def draft(self, kind, **kw):
            drafts.append((kind, kw))
            return {"id": "d-1", "state": "pending"}

    monkeypatch.setattr(supervisor_pr, "outbox_installed", lambda: True)
    monkeypatch.setattr(supervisor_pr, "outbox_client", lambda project, drafter: Outbox())
    out = call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    assert forge.ref(SIDE).startswith(out["commit"]) and forge.data["pulls"] == []      # pushed, not opened
    assert out["pull_request"]["draft"] == "d-1" and "Outbox draft d-1" in out["said"]
    [(kind, kw)] = drafts
    assert kind == supervisor_pr.KIND and kw["target"] == {"repo": forge.repo, "head": SIDE, "base": "main"}
    assert headings(kw["content"]) == ["Summary", "Testing", "Jira ID"] and WHY in kw["content"]
    assert "waiting on the owner" in supervisor_pr.digest_line(project, store)


def test_the_digest_names_the_open_pull_request(project, store):
    assert supervisor_pr.digest_line(project, store) is None
    out = call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    line = supervisor_pr.digest_line(project, store)
    assert f"#{out['pull_request']['number']} open on {SIDE}" in line
    text = since_digest.digest([], 0.0, 100.0, "UTC", own_pr=line, model="Model: none.")
    assert line in text and text.endswith("Model: none.")


def test_a_merged_pull_request_leaves_the_digest(project, store, forge):
    out = call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    assert "workers" in project.orchestra.config_edit.text("overlay")                    # live through the overlay
    forge.merge(out["pull_request"]["number"])
    git(project.repo_root, "pull", "-q", "--ff-only", "origin", "main")                # goes through: the checkout is untouched
    assert supervisor_pr.digest_line(project, store) is None                           # merged and pulled
    assert "workers" not in (project.orchestra.config_edit.text("overlay") or "")       # the overlay's copy retired
    project.reload_config()
    assert project.orchestra.config.raw["tasks"]["workers"] == 2                         # from orchestra.toml now
    assert git(project.repo_root, "status", "--porcelain").strip() == ""


CODE = "def override(original):\n    def cpu_count(*a, **kw):\n        return original(*a, **kw) or 1\n    return cpu_count\n"


def test_write_override_is_live_on_the_branch_never_the_base_and_a_pull_request(project, store, forge):
    base = git(project.repo_root, "rev-parse", "main").strip()
    owner_says(store, "Patch it locally please, it is OPS-7.")
    got = call(project, store, "write_override", name="cpu_count_fallback", target="psutil:cpu_count", kind="wrap", code=CODE,
               why="psutil.cpu_count returns None in the container, and the host divides by it",
               reason="the host crashes on every tick without it")
    rel = ".orchestra/overrides/cpu_count_fallback.py"
    assert not (project.repo_root / rel).exists() and "merged" in got["applies"]       # the checkout untouched: once merged
    assert git(project.repo_root, "rev-parse", "main").strip() == base                 # never committed to the base
    assert git(project.repo_root, "show", "--name-only", "--format=", SIDE).split() == [rel]
    pr = got["pull_request"]
    assert pr["number"] and forge.ref(SIDE) == got["commit"] and forge.ref("main") == base
    body = forge.body_of(pr["number"])
    assert "the host crashes on every tick" in body and "pinned to" in body
    assert body.split("## Jira ID", 1)[1].strip().splitlines()[0] == "OPS-7"


def test_a_graduation_is_one_commit_on_the_branch_and_its_pull_request(project, store, forge):
    from design_fixtures import say
    root = project.repo_root
    (root / "pyproject.toml").write_text('[project]\nname = "acme-tools"\nversion = "0.1"\n')
    (root / "acme_tools").mkdir()
    (root / "acme_tools" / "__init__.py").write_text("")
    (root / ".orchestra" / "machines" / "relator").mkdir(parents=True)
    (root / ".orchestra" / "machines" / "relator" / "relator.py").write_text("print('relate')\n")
    toml = root / "orchestra.toml"
    toml.write_text(toml.read_text() + '\n[[machines]]\nname = "relator"\ncommand = "python .orchestra/machines/relator/relator.py"\n')
    git(root, "add", "-A")
    git(root, "commit", "-qm", "the relator")
    project.reload_config()
    base = git(root, "rev-parse", "main").strip()
    reason = "the relator has run for a week unchanged, so it graduates"
    call(project, store, "graduate_machine", name="relator", reason=reason)
    yes = "Yes, graduate the relator into the project"
    say(store, yes)
    done = call(project, store, "graduate_machine", name="relator", reason=reason, dry_run=False, quote=yes)
    assert git(root, "rev-parse", "main").strip() == base
    files = set(git(root, "show", "--no-renames", "--name-only", "--format=", SIDE).split())
    assert {".orchestra/machines/relator/relator.py", "acme_tools/relator/relator.py", "orchestra.toml"} <= files
    pr = done["pull_request"]
    assert pr["number"] and any(f"#{pr['number']}" in x for x in done["said"])
    body = forge.body_of(pr["number"])
    assert reason in body and "files moved unchanged" in body
    assert forge.data["pulls"][0]["title"] == "Supervisor: graduate relator to acme_tools/relator/"


def test_an_edit_in_progress_in_the_checkout_is_never_swept_into_the_branch(project, store):
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text() + "\n# the owner's edit in progress\n")
    typed = toml.read_text()
    out = call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    assert out["commit"] and toml.read_text() == typed
    assert "edit in progress" not in git(project.repo_root, "show", f"{SIDE}:orchestra.toml")


def test_a_spec_change_is_live_from_the_state_directory_until_merged_and_pulled(project, store, forge):
    from orchestra_agents import supervisor_spec
    from orchestra_agents.machine_spec import load
    supervisor_spec.ensure(project)
    out = call(project, store, "add_duty", name="morning_note", text="Every morning, tell the owner what waits on them.",
               about="a morning note", listens=[], tools=["observe"], reason="the owner asked for a morning note")
    assert out["changed"] and not (project.repo_root / ".orchestra").exists()           # the checkout untouched
    assert load(project, "supervisor").duty("morning_note") is not None                 # live from the state directory
    forge.merge(out["pull_request"]["number"])
    git(project.repo_root, "pull", "-q", "--ff-only", "origin", "main")
    assert supervisor_pr.digest_line(project, store) is None
    assert not (project.state_dir / "machines" / "supervisor").exists()                 # the live copy retired
    assert load(project, "supervisor").duty("morning_note") is not None                 # from the checkout now
