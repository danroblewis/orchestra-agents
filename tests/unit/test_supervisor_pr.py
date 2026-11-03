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
    from orchestra_agents import outbox_read
    monkeypatch.setattr(outbox_read, "fetch", lambda project: {"ok": True, "waiting": [{"id": "d-1", "state": "drafted"}],
                                                                "recent": []})
    assert "waiting for your approval on the Outbox page" in supervisor_pr.digest_line(project, store)


def test_a_draft_the_outbox_sent_is_the_pull_request_it_opened_never_waiting(project, store, forge, monkeypatch):
    """T2j s16: draft d938f2ac was sent as #53 at 10:30Z, and the digest said "waiting on the owner"."""
    from orchestra_agents import outbox_read

    class Outbox:
        def draft(self, kind, **kw):
            return {"id": "d938f2ac5272f458", "state": "drafted"}

    monkeypatch.setattr(supervisor_pr, "outbox_installed", lambda: True)
    monkeypatch.setattr(supervisor_pr, "outbox_client", lambda project, drafter: Outbox())
    call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    sent = {"id": "d938f2ac5272f458", "kind": supervisor_pr.KIND, "state": "sent", "approved_by": "policy",
            "remote": {"repo": forge.repo, "number": 53}}
    monkeypatch.setattr(outbox_read, "fetch", lambda project: {"ok": True, "waiting": [], "recent": [sent]})
    line = supervisor_pr.digest_line(project, store)
    assert f"#53 open on {SIDE}, " in line and "waiting" not in line
    assert store.agent_state("supervisor")["pull_request"]["number"] == 53              # kept: the next read knows it
    monkeypatch.setattr(outbox_read, "fetch", lambda project: {"ok": True, "waiting": [],
                                                                "recent": [{**sent, "state": "rejected", "reason": "not now"}]})
    rec = store.agent_state("supervisor")["pull_request"]
    store.set_agent_state("supervisor", pull_request={**rec, "number": None})
    assert "rejected (not now): no pull request is open" in supervisor_pr.digest_line(project, store)

    def down(project):
        raise outbox_read.StoreError("the Outbox's state did not answer")
    monkeypatch.setattr(outbox_read, "fetch", down)                                    # the bus says it instead
    store.events.emit("outbox.sent", subject="d938f2ac5272f458", draft="d938f2ac5272f458", kind=supervisor_pr.KIND,
                      remote={"repo": forge.repo, "number": 53})
    assert f"#53 open on {SIDE}" in supervisor_pr.digest_line(project, store)


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


# [scm] as set_setting writes it (S3): orchestra-scm's schema, the token by file only, and the branch's
# waiting commits pushed as one pull request once it is valid

def _bare(tmp_path, monkeypatch, forge):
    """A project with a remote and no [scm] yet (the standing demo before the owner configured it)."""
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(supervisor_pr, "outbox_installed", lambda: False)
    repo = forge.clone(tmp_path / "repo")
    p = _project(repo)
    git(repo, "push", "-q", "origin", "main")
    return p, AgentStore(p, Events.for_project(p, source="supervisor"))


def scm_value(forge, **more):
    return {"provider": "branch_pr", "repo": forge.repo, "base": "main", "api_url": forge.url, **more}


def test_scm_good_is_written_and_unknown_keys_are_refused_with_the_known_ones(tmp_path, monkeypatch, forge):
    import json as _json

    from orchestra_agents.store import StoreError
    project, store = _bare(tmp_path, monkeypatch, forge)
    with pytest.raises(StoreError) as e:
        call(project, store, "set_setting", key="scm", target="repo", reason="the owner named the repository for PRs",
             value=_json.dumps(scm_value(forge, template_sections=["Summary"])))
    assert "template_sections: not a setting" in str(e.value) and "repo" in str(e.value) and "token_file" in str(e.value)
    with pytest.raises(StoreError, match="not a setting"):
        call(project, store, "set_setting", key="scm.templates", value='"x"', target="repo", reason="a guessed key again")
    assert not git(project.repo_root, "branch", "--list", SIDE).strip()                   # nothing was written
    with pytest.raises(StoreError, match="owner/name"):
        call(project, store, "set_setting", key="scm.repo", value='"just-a-name"', target="repo", reason="a bad repository")
    out = call(project, store, "set_setting", key="scm", value=_json.dumps(scm_value(forge)), target="repo",
               reason="the owner named the repository, the base and the token file")
    assert out["ok"] and project.orchestra.config.raw["scm"]["repo"] == forge.repo


def test_scm_a_token_in_the_value_is_refused(tmp_path, monkeypatch, forge):
    from orchestra_agents.store import StoreError
    project, store = _bare(tmp_path, monkeypatch, forge)
    token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    for key, value in (("scm.token", f'"{token}"'), ("scm.token_file", f'"{token}"'),
                       ("scm.token_file", f'"{project.repo_root / "creds" / "github-token"}"')):
        with pytest.raises(StoreError) as e:
            call(project, store, "set_setting", key=key, value=value, target="overlay", reason="the owner's token for PRs")
        assert token not in str(e.value) or "secret" in str(e.value)
    assert "scm" not in (project.orchestra.config.raw or {})
    outside = tmp_path / "creds-home" / "github-token"
    out = call(project, store, "set_setting", key="scm.token_file", value=f'"{outside}"', target="overlay",
               reason="the owner named the file that holds the token")
    assert out["ok"] and project.orchestra.config.raw["scm"]["token_file"] == str(outside)


def test_setting_scm_pushes_the_branchs_pending_commits_as_one_pull_request(tmp_path, monkeypatch, forge):
    import json as _json
    project, store = _bare(tmp_path, monkeypatch, forge)
    first = call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    second = call(project, store, "set_setting", key="agents.draft_expiry", value='"12h"', target="repo",
                  reason="the owner wants drafts to expire after twelve hours")
    assert first["pull_request"]["number"] is None and second["pull_request"]["number"] is None
    assert forge.ref(SIDE) is None                                                       # nothing pushed yet
    out = call(project, store, "set_setting", key="scm", value=_json.dumps(scm_value(forge)), target="repo",
               reason="the owner named the repository, the base and the token file")
    pr = out["pull_request"]
    assert pr["number"] and f"The branch's 3 pending commits are now pushed as PR #{pr['number']}" in out["said"]
    assert forge.ref(SIDE) == git(project.repo_root, "rev-parse", SIDE).strip() and len(forge.data["pulls"]) == 1
    assert supervisor_pr.catch_up(project, store) is None                                # nothing left to push
    later = call(project, store, "set_capacity_limit", workers=3, reason="three workers: the queue still grows")
    assert later["pull_request"]["number"] == pr["number"] and len(forge.data["pulls"]) == 1


def test_scm_set_in_the_overlay_pushes_the_waiting_branch_in_that_answer(tmp_path, monkeypatch, forge):
    import json as _json
    project, store = _bare(tmp_path, monkeypatch, forge)
    call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    call(project, store, "set_capacity_limit", workers=3, reason="three workers: the queue still grows")
    out = call(project, store, "set_setting", key="scm", value=_json.dumps(scm_value(forge)), target="overlay",
               reason="the owner's own repository settings for the pull requests")
    assert out["commit"] is None and out["branch"] == SIDE
    assert f"The branch's 2 pending commits are now pushed as PR #{out['pull_request']['number']}" in out["said"]
    assert forge.ref(SIDE) == git(project.repo_root, "rev-parse", SIDE).strip()
    body = forge.body_of(out["pull_request"]["number"])
    assert "tasks.workers" in body or "workers" in body


def test_a_refused_push_is_said_exactly_and_the_digest_pushes_once_fixed(tmp_path, monkeypatch, forge):
    import json as _json
    project, store = _bare(tmp_path, monkeypatch, forge)
    call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    hook = forge.origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'remote: Permission to acme/service.git denied to worker.' >&2\nexit 1\n")
    hook.chmod(0o755)
    out = call(project, store, "set_setting", key="scm", value=_json.dumps(scm_value(forge)), target="overlay",
               reason="the owner's own repository settings for the pull requests")
    assert out["pull_request"]["number"] is None and "Permission to acme/service.git denied" in out["said"]
    assert forge.ref(SIDE) is None
    assert supervisor_pr.catch_up(project, store) is None                                # the same push: not tried again
    hook.unlink()
    monkeypatch.setattr(supervisor_pr, "RETRY_S", 0.0)                                  # the next round's time
    line = supervisor_pr.digest_line(project, store)                                     # the digest names the branch: pushed first
    assert line and "open on" in line and forge.ref(SIDE) is not None


def test_scm_on_a_protected_base_is_refused_by_name(tmp_path, monkeypatch, forge):
    import json as _json
    project, store = _bare(tmp_path, monkeypatch, forge)
    call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    out = call(project, store, "set_setting", key="scm", target="overlay", reason="the owner's own repository settings",
               value=_json.dumps(scm_value(forge, protect=[SIDE])))
    assert out["pull_request"]["number"] is None and f"refusing to push to {SIDE!r}" in out["said"]


def _with_update_pr(monkeypatch, forge) -> list:
    """The provider with ``update_pr`` on the fake forge (orchestra-scm's provider has none yet): it
    rewrites the pull request's title and description as the forge keeps them."""
    real, calls = supervisor_pr.provider, []

    def provider(project, section):
        scm = real(project, section)

        def update_pr(change, *, title, body):
            calls.append(change.number)
            forge._pull(change.number)["title"] = title
            forge.bodies[change.number] = body
        scm.update_pr = update_pr
        return scm
    monkeypatch.setattr(supervisor_pr, "provider", provider)
    return calls


def test_the_pull_request_describes_the_whole_branch_and_is_updated_when_it_grows(project, store, forge, monkeypatch):
    calls = _with_update_pr(monkeypatch, forge)
    first = call(project, store, "set_setting", key="agents.draft_expiry", value='"12h"', target="repo",
                 reason="the owner wants drafts to expire after twelve hours")
    n = first["pull_request"]["number"]
    assert forge._pull(n)["title"] == "Supervisor: draft expiry" and not calls
    call(project, store, "set_setting", key="outbox.kinds", value='{"supervisor.pull_request": {"sender": "github.pr_open"}}',
         target="repo", reason="the Supervisor's pull requests are drafts the owner approves")
    last = call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    assert last["pull_request"]["number"] == n and len(forge.data["pulls"]) == 1 and calls == [n, n]
    assert forge._pull(n)["title"] == "Supervisor: draft expiry, an Outbox kind, capacity"
    summary = forge.body_of(n).split("## Summary", 1)[1].split("## Testing", 1)[0].strip().splitlines()
    assert len(summary) == 3 and summary[0].startswith("- draft expiry") and "twelve hours" in summary[0]
    assert "drafts the owner approves" in summary[1] and WHY in summary[2]


def test_a_provider_that_cannot_edit_a_pull_request_says_so(project, store, forge):
    first = call(project, store, "set_setting", key="agents.draft_expiry", value='"12h"', target="repo",
                 reason="the owner wants drafts to expire after twelve hours")
    out = call(project, store, "set_capacity_limit", workers=2, reason=WHY)
    assert out["pull_request"]["number"] == first["pull_request"]["number"]
    assert "cannot edit an open pull request yet" in out["said"]


def test_the_title_lists_what_the_branch_changes_in_sixty_characters():
    assert supervisor_pr.title_of(["draft expiry", "[scm]", "[scm]", "an Outbox kind"]) == \
        "Supervisor: draft expiry, [scm], an Outbox kind"
    long = supervisor_pr.title_of([f"the collector poller_{k}" for k in range(6)])
    assert len(long) <= supervisor_pr.TITLE_MAX and long.endswith("more") and "poller_0" in long
    assert supervisor_pr.label_of("Supervisor: add duty x — The owner said", "") == "add duty x"
    assert supervisor_pr.label_of("Supervisor: anything", "why\n\nSupervisor-For: [scm]") == "[scm]"
