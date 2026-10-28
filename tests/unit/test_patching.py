"""Self-healing, the Supervisor's for now: a bug in an installed plugin is patched in place as a
project-local override pinned to the installed version (and Orchestra applies it), with the
upstream issue drafted in the outbox for the owner's approval; a bug in the project's own code is
patched in a scratch worktree on a branch of its own and handed to the Coordinator to land
through a review and the gate. Neither touches the owner's checkout."""

import importlib.metadata
import subprocess

import pytest
from conftest import agents_project

from orchestra_agents import tooling, upstream
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore, StoreError

WHY = "psutil.cpu_count raises on this machine's container, which stops the agents' host from starting"
REASON = "the host cannot start without it, so the flow is stopped until it is repaired"
CODE = '''def override(original):
    def cpu_count(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except OSError:
            return 1
    return cpu_count
'''


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    (repo / "app.py").write_text("def total(xs):\n    return sum(xs) + 1\n")
    p = agents_project(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return p


@pytest.fixture
def store(project):
    return AgentStore(project, Events.for_project(project, source="supervisor"))


def call(project, store, tool, role="supervisor", **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role=role)


class FakeOutbox:
    def __init__(self):
        self.drafts = []

    def draft(self, kind, **kw):
        self.drafts.append({"kind": kind, **kw})
        return {"id": "d-1", "state": "drafted"}


def test_an_installed_plugin_is_patched_by_an_override_and_the_issue_is_drafted_for_the_owner(project, store, monkeypatch):
    got = call(project, store, "write_override", name="cpu_count_fallback", target="psutil:cpu_count", kind="wrap", code=CODE,
               why=WHY, reason=REASON)
    version = importlib.metadata.version("psutil")
    assert got["package"] == f"psutil=={version}" and got["note"] == "committed to main"
    text = (project.repo_root / ".orchestra/overrides/cpu_count_fallback.py").read_text()
    assert "TARGET = 'psutil:cpu_count'" in text and f"PACKAGE = 'psutil=={version}'" in text and "KIND = 'wrap'" in text
    assert git(project.repo_root, "show", "--name-only", "--format=", "HEAD").split() == [".orchestra/overrides/cpu_count_fallback.py"]
    [ev] = [e for e in store.events.read(0, 100)[0] if e["type"] == "system.changed"]
    assert ev["subject"] == "override:cpu_count_fallback" and ev["source"] == "supervisor"     # an announced change: an era
    overrides = pytest.importorskip("orchestra.overrides")                                     # Orchestra applies it by file
    try:
        [rep] = [r for r in overrides.apply(project.repo_root) if r.get("name") == "cpu_count_fallback"]
        assert rep["status"] == "applied"
    finally:
        (project.repo_root / ".orchestra/overrides/cpu_count_fallback.py").unlink()
        overrides.apply(project.repo_root)                                                     # and puts the original back

    outbox = FakeOutbox()
    monkeypatch.setattr(upstream, "outbox_client", lambda project, drafter: outbox)
    git(project.repo_root, "checkout", "--", ".orchestra/overrides/cpu_count_fallback.py")
    got = call(project, store, "draft_upstream_issue", repo="giampaolo/psutil", title="cpu_count raises in containers",
               body="cpu_count raises OSError in a container without /proc/cpuinfo; it should fall back.", override="cpu_count_fallback")
    [d] = outbox.drafts
    assert d["kind"] == "upstream.issue" and d["target"] == {"repo": "giampaolo/psutil"} and d["fields"] == {"title": "cpu_count raises in containers"}
    assert "def override(original)" in d["content"] and got["draft"] == "d-1"


def test_without_the_outbox_the_issue_is_kept_for_the_owner_and_nothing_is_sent(project, store, monkeypatch):
    def missing(project, drafter):
        raise ImportError("no orchestra_outbox")
    monkeypatch.setattr(upstream, "outbox_client", missing)
    got = call(project, store, "draft_upstream_issue", repo="o/r", title="A bug in the gate's rerun",
               body="The gate reruns a failing spec forever when rerun_failures is negative; it should refuse it.")
    assert got["draft"] is None and "not installed" in got["why_not_drafted"] and 'sender = "github.issue_create"' in got["next"]
    from pathlib import Path
    assert "A bug in the gate's rerun" in Path(got["kept"]).read_text()


def test_the_project_s_own_bug_is_patched_in_a_scratch_worktree_and_handed_on_to_land_through_the_gate(project, store):
    toml = project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text() + '\n[project]\nwork = "code"\n')
    git(project.repo_root, "commit", "-qam", "code work")
    project.reload_config()
    diff = ("--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n def total(xs):\n-    return sum(xs) + 1\n+    return sum(xs)\n")
    got = call(project, store, "propose_patch", name="total_off_by_one", diff=diff, why="total() adds one to every sum: orders are overcounted",
               test="python -c 'import app; assert app.total([1, 2]) == 3'", reason=REASON)
    assert got["branch"] == "supervisor/patch-total-off-by-one" and got["files"] == ["app.py"]
    assert git(project.repo_root, "show", f"{got['commit']}:app.py").endswith("return sum(xs)\n")
    assert (project.repo_root / "app.py").read_text().endswith("+ 1\n")                       # the checkout is untouched
    assert git(project.repo_root, "status", "--porcelain") == "" and "patch-" not in git(project.repo_root, "worktree", "list")
    [req] = [e for e in store.events.read(0, 100)[0] if e["type"] == "work.requested"]
    assert got["commit"] in req["requirements"] and req["to"] == "coordinator" and req["source"] == "supervisor"
    with pytest.raises(StoreError, match="already a branch"):
        call(project, store, "propose_patch", name="total_off_by_one", diff=diff, why="the same patch again, by mistake", reason=REASON)


def test_what_a_repair_may_never_be(project, store):
    with pytest.raises(StoreError, match="work is tasks"):
        call(project, store, "propose_patch", name="x_y", diff="@@ -1 +1 @@\n-a\n+b\n", why="something broke here", reason=REASON)
    with pytest.raises(StoreError, match="never overridden"):
        call(project, store, "write_override", name="quiet_core", target="orchestra.events:Events", kind="replace", code=CODE,
             why="Orchestra's events are noisy today", reason=REASON)
    with pytest.raises(StoreError, match="define `def override"):
        call(project, store, "write_override", name="no_def", target="psutil:cpu_count", kind="wrap", code="x = 1", why=WHY, reason=REASON)
    with pytest.raises(StoreError, match="repairs are the Supervisor's alone|needs the 'patch' tool group"):
        call(project, store, "write_override", role="coordinator", name="x_y", target="psutil:cpu_count", kind="wrap", code=CODE,
             why=WHY, reason=REASON)
