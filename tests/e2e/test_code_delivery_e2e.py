"""A code-delivery loop, composed in the test as the Supervisor composes one (no definition ships):
implement (role worker, a worktree) ⇄ ai_review (role reviewer) → open_pr (orchestra-scm's program,
declared with ``declare_program``) → ci (GitHub's checks on the pull request, ``of: children``) →
review (GitHub) → the end. The Workflows plugin's real floor and program host run it (``python -m
orchestra_workflows run`` and ``programs``) against orchestra-scm's FakeForge, a real bare repository
under a fake GitHub. A fake worker commits on the item's branch in the worktree it is handed and
writes ``pr_title`` and ``pr_body``; a fake reviewer asks for changes once, with ``review_notes``,
which the worker is offered back; CI fails once, and the worker's fix goes to the same pull request."""

import os
import subprocess
import sys
import threading
import time

import pytest
from conftest import agents_project

from orchestra_agents import tooling
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore

pytest.importorskip("orchestra_workflows.api")
machines = pytest.importorskip("orchestra_workflows.programs.machines")
pytest.importorskip("orchestra_scm.open_pr", reason="orchestra-scm without open_pr")
if "open_pr" not in machines.shipped():
    pytest.skip("the installed orchestra-scm ships no open_pr to the program host", allow_module_level=True)
from orchestra.events import Events as OrchestraEvents
from orchestra_scm.fakes import FakeForge
from orchestra_scm.naming import branch_for
from orchestra_workflows.api import Record, write

GITHUB = {"outside": "github"}


def child(event, **data):
    return {"event": event, "of": "children", **({"data": data} if data else {})}


ENDS = [{**child("github.pr.merged"), "to": None, "outcome": "merged"},
        {**child("github.pr.closed", merged=False), "to": None, "outcome": "closed_unmerged"}]
DELIVERY = {
    "name": "delivery", "items": "change", "planner": "supervisor", "starts": [{"request": True}],
    "steps": [
        {"name": "implement", "who": {"role": "worker"}, "needs": ["worktree"], "outcomes": {"implemented": "ai_review"}},
        {"name": "ai_review", "who": {"role": "reviewer"}, "needs": ["worktree"],
         "outcomes": {"approved": "open_pr", "changes": "implement"}, "guards": {"rounds": 3, "then": "attention"}},
        {"name": "open_pr", "who": {"program": "open_pr"}, "outcomes": {"opened": "ci", "no_changes": "implement"}},
        {"name": "ci", "who": GITHUB, "done_when": child("github.checks.completed", head_sha={"item": "commit"}),
         "outcomes": {"success": "review", "failure": "implement"}, "on": ENDS},
        {"name": "review", "who": GITHUB, "done_when": child("github.pr.approved"), "outcomes": {"done": None},
         "on": [{**child("github.pr.changes_requested"), "to": "implement"}, *ENDS]}]}


class FakeMachine:
    """A machine of one role on the floor's contract: announce, claim each offer of the role, read
    the claim back, do the work, finish with the outcome and the item's fields."""

    def __init__(self, o, role, work):
        self.o, self.role, self.work = o, role, work
        self.source = f"{role}/1"
        self.events = OrchestraEvents.for_project(o, source=self.source)
        self.offers, self.stop = [], threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def start(self):
        self.events.publish("orchestra.machine.started", {"mode": "ad hoc", "kind": "program", "roles": [self.role],
                                                          "listens_for": ["offer.made", "offer.withdrawn"]})
        self.offset = self.events.end()
        self.thread.start()
        return self

    def loop(self):
        while not self.stop.is_set():
            batch, self.offset = self.events.after(self.offset)
            for ev in batch:
                d = ev.get("data") or {}
                if ev["type"] == "offer.made" and d.get("role") == self.role and d.get("to") in (None, self.source):
                    self.take(d)
            time.sleep(0.1)

    def take(self, d):
        self.events.publish("offer.claimed", {"offer": d["offer"]}, subject=d["item"], causationid=d["offer"])
        self.offers.append(d)
        outcome, summary, fields = self.work(d, len(self.offers))
        body = {"offer": d["offer"], "outcome": outcome, "summary": summary}
        if fields:
            body["facts"] = {"set": fields}
        self.events.publish("offer.finished", body, subject=d["item"], causationid=d["offer"])


def worktree(d):
    return next(r["handle"] for r in d.get("resources") or [] if r.get("kind") == "worktree")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def worker(d, n):
    wt = worktree(d)
    fix = {1: "widgets: 4\n", 2: "widgets: 4\nhidden: 1\n", 3: "widgets: 5\nhidden: 1\n"}.get(n, f"round {n}\n")
    with open(os.path.join(wt, "widgets.txt"), "w") as f:
        f.write(fix)
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", f"count the widgets (round {n})")
    return "implemented", f"round {n}", {
        "pr_title": "ABC-12: count the widgets on the report page",
        "pr_body": f"Counts the widgets, hidden rows too (round {n}).\n\n## Testing\n\npytest tests/test_widgets.py: 3 passed."}


SEEN = []          # what the reviewer's worktree was on: (branch, the last commit's subject)


def reviewer(d, n):
    wt = worktree(d)
    SEEN.append((git(wt, "rev-parse", "--abbrev-ref", "HEAD"), git(wt, "log", "-1", "--format=%s")))
    if n == 1:
        return "changes", "the total skips hidden rows", {"review_notes": "widgets.txt: count the hidden rows too"}
    return "approved", "it does what the brief asks", {"review_notes": ""}


def wait_for(check, procs, timeout=90.0, what=lambda: ""):
    end = time.time() + timeout
    while time.time() < end:
        if v := check():
            return v
        if any(p.poll() is not None for p in procs):
            break
        time.sleep(0.2)
    logs = "\n".join(" ".join(p.args[-2:]) + ": " + (p.stdout.read() if p.poll() is not None else "(running)")
                     for p in procs)
    raise AssertionError(f"timed out {what()}\n{logs}")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    for k in ("ORCHESTRA_EVENTS_ENDPOINT", "ORCHESTRA_EVENTS_TOKEN", "ORCHESTRA_EVENTS_URL", "ORCHESTRA_METRICS_URL"):
        monkeypatch.delenv(k, raising=False)
    with FakeForge(tmp_path / "remote") as forge:
        clone = forge.clone(tmp_path / "repo")
        project = agents_project(clone)
        toml = clone / "orchestra.toml"
        toml.write_text(toml.read_text() + '\n[workflows]\ntick = "1s"\n\n[scm]\nprovider = "branch_pr"\n'
                        f'repo = "{forge.repo}"\nbase = "main"\napi_url = "{forge.url}"\nopen_drafts = true\n')
        project.reload_config()
        monkeypatch.setenv("ORCHESTRA_REPO", str(clone))
        yield project, forge


def test_a_change_goes_round_the_review_and_ci_loops_into_one_draft_pull_request(world):
    project, forge = world
    o = project.orchestra
    write(o, DELIVERY, "state")
    main = forge.ref("main")
    store = AgentStore(project, Events.for_project(project, source="supervisor"))
    out = tooling.handle(project, store, "declare_program", {"program": "open_pr", "reason": "the owner's loop opens "
                         "a draft pull request after the AI review"}, role="supervisor")
    assert out["ok"] and out["steps"] == ["delivery.open_pr"]

    env = {**os.environ, "ORCHESTRA_REPO": str(o.repo_root)}
    procs = [subprocess.Popen([sys.executable, "-m", "orchestra_workflows", part, "--wait"], cwd=o.repo_root, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for part in ("run", "programs")]
    fakes = []
    try:
        bus = OrchestraEvents.for_project(o, source="test")
        wait_for(lambda: [e for e in bus.after(0)[0] if e["type"] == "orchestra.machine.started"
                          and e["source"] == "open_pr/1"], procs)
        fakes = [FakeMachine(o, "worker", worker).start(), FakeMachine(o, "reviewer", reviewer).start()]
        w, r = fakes
        item = "delivery-widgets"
        OrchestraEvents.for_project(o, source="owner").publish(
            "item.requested", {"workflow": "delivery", "brief": "small task: count the widgets on the report page"},
            subject=item)
        record = Record.for_project(o)

        def step():
            s = next((s for s in record.items() if s["item"] == item), None)
            if s is None or s.get("closed"):
                return "<end>" if (record.item(item) or {}).get("ended") else None
            return ((s.get("at") or {}).get("delivery") or {}).get("step")

        def fields():
            return {k: v.get("value") for k, v in ((record.item(item) or {}).get("fields") or {}).items()}

        # the AI review loop: changes once, with notes the worker is offered back; then approved
        wait_for(lambda: step() == "ci", procs, what=lambda: (step(), fields()))
        assert len(w.offers) == 2 and len(r.offers) == 2
        assert (w.offers[1].get("fields") or {}).get("review_notes") == "widgets.txt: count the hidden rows too"
        assert SEEN[:2] == [(branch_for(item), "count the widgets (round 1)"),       # the reviewer reads the branch
                            (branch_for(item), "count the widgets (round 2)")]
        [pr] = forge.data["pulls"]
        n = pr["number"]
        f = fields()
        assert pr["draft"] is True and pr["branch"] == branch_for(item)
        assert forge.title_of(n) == "ABC-12: count the widgets on the report page"
        assert "hidden rows too (round 2)" in forge.body_of(n) and "3 passed" in forge.body_of(n)
        assert f["pr_number"] == n and f["pr_url"] == f"https://github.com/{forge.repo}/pull/{n}"
        assert f["commit"] == forge.ref(branch_for(item))
        related = [e for e in bus.after(0)[0] if e["type"] == "item.related"]
        assert [(e["subject"], e["data"]["other"], e["source"]) for e in related] == [
            (f"{forge.repo}#{n}", item, "open_pr/1")]

        # the CI loop: a failure on the pull request's head sends it back; the fix goes to the same pull request
        actions = OrchestraEvents.for_project(o, source="bot.github.github-actions")
        actions.publish("github.checks.completed", {"repository": forge.repo, "number": n, "head_sha": f["commit"],
                                                    "outcome": "failure", "via": "check_run"}, subject=f"{forge.repo}#{n}")
        wait_for(lambda: step() == "ci" and fields().get("commit") != f["commit"], procs,
                 what=lambda: (step(), fields()))
        assert len(w.offers) == 3 and len(r.offers) == 3
        again = fields()
        assert len(forge.data["pulls"]) == 1 and again["pr_number"] == n           # never a second pull request
        assert forge.ref(branch_for(item)) == again["commit"]
        assert "round 3" in forge.body_of(n)                                       # the description rewritten

        actions.publish("github.checks.completed", {"repository": forge.repo, "number": n, "head_sha": again["commit"],
                                                    "outcome": "success", "via": "check_run"}, subject=f"{forge.repo}#{n}")
        wait_for(lambda: step() == "review", procs, what=lambda: (step(), fields()))
        OrchestraEvents.for_project(o, source="person.github.owner").publish(
            "github.pr.approved", {"repository": forge.repo, "number": n, "state": "approved",
                                   "commit_id": again["commit"]}, subject=f"{forge.repo}#{n}")
        wait_for(lambda: step() == "<end>", procs, what=lambda: (step(), fields()))
        assert fields()["pr_url"] == f"https://github.com/{forge.repo}/pull/{n}"
        closed = [e for e in bus.after(0)[0] if e["type"] == "item.closed" and e.get("subject") == item]
        assert closed and closed[-1]["data"].get("last_step") == "review"
        assert forge.ref("main") == main and set(forge.branches()) == {"main", branch_for(item)}   # main never pushed
        assert not [x for x in forge.requests if x.path.endswith("/merge")]
    finally:
        for m in fakes:
            m.stop.set()
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
