"""The Supervisor's workflow tools against a fake Workflows plugin (``workflow_tools.plugin``
replaced): a refusal says every problem and writes nothing, the root is the state directory unless
the owner's words say the repository (never committed), the loaded definitions and an item's
explanation are read back trimmed, a missing plugin is said plainly, and only the Supervisor
writes."""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import agents_project

from orchestra_agents import tooling, workflow_tools
from orchestra_agents.builtins import ROLES
from orchestra_agents.events import Events
from orchestra_agents.harness.tools import BY_NAME
from orchestra_agents.store import AgentStore, StoreError

WHY = "the owner described how their work moves"
MESSAGE = "Declare a two-step workflow: write then review, and keep it in the repository please."
DEF = {"name": "write_review", "starts": [{"request": True}],
       "steps": [{"name": "write", "who": {"role": "worker"}, "outcomes": {"ready": "review"}},
                 {"name": "review", "who": {"role": "reviewer"}, "outcomes": {"approved": None, "rejected": "write"}}]}


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@dataclass
class Who:
    kind: str
    name: str


@dataclass
class Step:
    name: str
    actor: Who
    needs: tuple = ()
    outcomes: dict | None = None


@dataclass
class Workflow:
    name: str
    planner: str
    steps: tuple
    raw: dict = field(default_factory=dict)


class DefinitionError(ValueError):
    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


class FakePlugin:
    """The plugin's surface the tools use, writing where the real writer would."""

    def __init__(self):
        self.refuse: list[str] = []
        self.calls: list[tuple] = []
        self.explained: dict | None = None
        self.define = SimpleNamespace(write=self.write, load=self.load, DefinitionError=DefinitionError)

    def problems(self, project, raw):
        self.calls.append(("problems", raw["name"]))
        return list(self.refuse)

    def roots(self, project):
        return [(".orchestra", Path(project.repo_root) / ".orchestra"), (str(project.state_dir), Path(project.state_dir))]

    def write(self, project, definition, root="state"):
        self.calls.append(("write", root))
        base = dict(self.roots(project))[".orchestra" if root == "repository" else str(project.state_dir)]
        path = base / "workflows" / definition["name"] / "workflow.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(definition))
        return SimpleNamespace(path=path, notes=["workflow 'write_review' step 'write' needs 'worktree'"])

    def load(self, project):
        out = []
        for label, root in self.roots(project):
            for f in sorted((root / "workflows").glob("*/workflow.json")):
                raw = json.loads(f.read_text())
                steps = tuple(Step(s["name"], Who(*next(iter(s["who"].items()))), tuple(s.get("needs", ())),
                                   s.get("outcomes")) for s in raw["steps"])
                out.append(Workflow(raw["name"], raw.get("planner", "coordinator"), steps, raw))
        return SimpleNamespace(workflows=out, problems=["x/workflows/broken/workflow.json: no steps"], notes=[])

    def Record(self):
        return self

    def for_project(self, project):
        return self

    def explain(self, item):
        return self.explained if item == "ORC-1" else None

    def item(self, item):
        return {"item": item, "facts": 3, "first": "t1", "last": "t3", "fields": {"title": {"value": "x", "id": "e1"}},
                "children": []}

    def items(self, predicate=None):
        rows = [{"item": "ORC-1", "facts": 3, "fields": {"title": "x"}, "wait": "offered", "closed": [], "requested": {},
                 "at": {"write_review": {"step": "review", "pile": "review.waiting", "since": "t3", "wait": "offered",
                                         "rounds": 2, "not_counted": [{"fact": "e9", "why": "not the holder"}]}}}]
        return [r for r in rows if predicate is None or predicate(r)]

    def ns(self):
        return SimpleNamespace(define=self.define, problems=self.problems, roots=self.roots, Record=self)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    return p


@pytest.fixture
def fake(monkeypatch):
    f = FakePlugin()
    monkeypatch.setattr(workflow_tools, "plugin", f.ns)
    return f


@pytest.fixture
def store(project):
    s = AgentStore(project, Events.for_project(project, source="supervisor"))
    s.set_agent_state("supervisor", turn={"n": 1, "id": "sessions/supervisor/1", "message": MESSAGE, "at": 1.0})
    return s


def call(project, store, tool, role="supervisor", **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role=role)


def test_the_tools_are_the_supervisors_group_and_the_schema_spells_out_the_definition():
    assert {BY_NAME[n].group for n in ("declare_workflow", "list_workflows", "explain_item")} == {"workflows"}
    assert "workflows" in ROLES["supervisor"].tools
    assert all("workflows" not in r.tools for n, r in ROLES.items() if n != "supervisor")
    d = BY_NAME["declare_workflow"].input_schema
    assert d["required"] == ["definition", "reason"] and d["properties"]["root"]["enum"] == ["state", "repository"]
    props = d["properties"]["definition"]["properties"]
    assert set(props) == {"name", "planner", "starts", "steps", "piles", "lifecycle", "settings"}
    step = props["steps"]["items"]["properties"]
    assert set(step) == {"name", "who", "needs", "lease", "done_when", "outcomes", "guards", "on", "depends"}
    assert set(step["who"]["properties"]) == {"role", "program", "outside", "person", "outbox"}


def test_a_definition_is_written_to_the_state_directory_by_default(project, store, fake):
    out = call(project, store, "declare_workflow", definition=DEF, reason=WHY)
    path = project.orchestra.state_dir / "workflows" / "write_review" / "workflow.json"
    assert out["ok"] and out["root"] == "state" and out["path"] == str(path) and path.is_file()
    assert fake.calls == [("problems", "write_review"), ("write", "state")]
    assert "worktree" in out["notes"][0] and "/workflows" not in out["shown"] and "/piles" not in out["shown"] and "Items" in out["shown"]
    assert out["author"] == "supervisor" and "committed" not in out
    assert "wrote the workflow write_review" in out["done"] and "as your proposal" in out["done"]
    assert store.agent_state("supervisor")["actions"][-1]["tool"] == "declare_workflow"


def test_a_refusal_says_every_problem_and_writes_nothing(project, store, fake):
    fake.refuse = ["the definition: step 'review' has 'whom', which is not a word of the vocabulary",
                   "the definition: outcome 'rejected' of step 'review' leads to 'wrte', which is no step"]
    with pytest.raises(StoreError) as e:
        call(project, store, "declare_workflow", definition=DEF, reason=WHY)
    msg = str(e.value)
    assert "nothing written" in msg and "2 problems" in msg and "(1)" in msg and "'whom'" in msg and "'wrte'" in msg
    assert fake.calls == [("problems", "write_review")]
    assert not (project.orchestra.state_dir / "workflows" / "write_review").exists()


def test_a_writer_refusal_is_said_the_same_way(project, store, fake, monkeypatch):
    def refuse(*a, **k):
        raise DefinitionError(["the definition: a lease of 1s is not longer than the heartbeat plus a tick"])
    monkeypatch.setattr(fake.define, "write", refuse)
    with pytest.raises(StoreError, match="nothing written.*lease of 1s"):
        call(project, store, "declare_workflow", definition=DEF, reason=WHY)


def test_the_repository_needs_the_owners_words_and_is_never_committed(project, store, fake):
    head = git(project.repo_root, "rev-parse", "HEAD")
    with pytest.raises(StoreError, match="owner's choice"):
        call(project, store, "declare_workflow", definition=DEF, reason=WHY, root="repository")
    with pytest.raises(StoreError, match="not the owner's words"):
        call(project, store, "declare_workflow", definition=DEF, reason=WHY, root="repository",
             quote="put it in the repo for me")
    out = call(project, store, "declare_workflow", definition=DEF, reason=WHY, root="repository",
               quote="keep it in the repository")
    path = project.repo_root / ".orchestra" / "workflows" / "write_review" / "workflow.json"
    assert out["path"] == str(path) and path.is_file() and out["committed"] is False and out["author"] == "owner"
    assert "hand_to_coordinator" in out["next"] and "(not committed)" in out["done"]
    assert git(project.repo_root, "rev-parse", "HEAD") == head
    assert "?? .orchestra/" in git(project.repo_root, "status", "--porcelain")


def test_only_the_supervisor_declares(project, store, fake):
    with pytest.raises(StoreError, match="needs the 'workflows' tool group"):
        call(project, store, "declare_workflow", role="coordinator", definition=DEF, reason=WHY)
    with pytest.raises(StoreError, match="Supervisor's alone"):
        workflow_tools.handle(project, store, "declare_workflow", {"definition": DEF, "reason": WHY}, "worker")
    with pytest.raises(StoreError, match="needs the 'workflows' tool group"):
        call(project, store, "list_workflows", role="worker")
    assert fake.calls == []


def test_a_reason_is_required(project, store, fake):
    with pytest.raises(StoreError, match="reason"):
        workflow_tools.handle(project, store, "declare_workflow", {"definition": DEF, "reason": "x"}, "supervisor")


def test_the_loaded_definitions_are_listed_with_where_and_the_loaders_problems(project, store, fake):
    call(project, store, "declare_workflow", definition={**DEF, "steps": [{**DEF["steps"][0], "needs": ["worktree"]},
                                                                         {**DEF["steps"][1], "guards": {"rounds": 3}}]},
         reason=WHY)
    out = call(project, store, "list_workflows")
    (w,) = out["workflows"]
    assert w["name"] == "write_review" and w["planner"] == "coordinator" and w["starts"] == [{"request": True}]
    assert w["where"].endswith("/workflows/write_review/workflow.json") and str(project.orchestra.state_dir) in w["where"]
    assert w["steps"][0] == {"name": "write", "who": {"role": "worker"}, "needs": ["worktree"], "outcomes": {"ready": "review"}}
    assert w["steps"][1]["guards"] == {"rounds": 3} and w["steps"][1]["outcomes"] == {"approved": None, "rejected": "write"}
    assert out["problems"] == ["x/workflows/broken/workflow.json: no steps"]


def test_an_item_is_explained_trimmed(project, store, fake):
    fake.explained = {"item": "ORC-1", "facts": 3, "positions": {}, "not_counted": [],
                      "fields": {"title": {"value": "x", "set_by": {"id": "e1", "source": "tasks", "time": "t1"},
                                           "outranked": [{"id": "e0"}]}},
                      "timeline": [{"id": f"e{i}", "type": "t", "source": "s", "time": f"t{i}"} for i in range(30)],
                      "relations": [], "ended": {}, "requested": {"workflow": "write_review", "by": {"id": "e0"}}}
    out = call(project, store, "explain_item", item="ORC-1", last=5)
    assert out["fields"] == {"title": {"value": "x", "set_by": "tasks t1", "outranked": 1}}
    assert out["positions"] == {"write_review": {"step": "review", "pile": "review.waiting", "since": "t3",
                                                 "wait": "offered", "rounds": 2}}
    assert out["not_counted"] == [{"workflow": "write_review", "fact": "e9", "why": "not the holder"}]
    assert [e["id"] for e in out["last_facts"]] == ["e25", "e26", "e27", "e28", "e29"]
    assert out["summary"]["open"] is True and out["summary"]["wait"] == "offered" and out["requested"]["workflow"]
    with pytest.raises(StoreError, match="no item 'ORC-2'"):
        call(project, store, "explain_item", item="ORC-2")


def test_the_open_items_are_listed_as_the_items_page_lists_them(project, store, fake, monkeypatch):
    seen = {}

    def item_list(record, defs, now, workflow=None):
        seen.update(record=record, workflows=[w.name for w in defs.workflows], workflow=workflow)
        rows = [{"item": f"SCRUM-{i}", "step": "sail_check", "pile": "sail_check.waiting", "held": False,
                 "wait": {"reason": "waiting for github.pr.labeled SAILED", "by": "github"}, "holder": None,
                 "since": "t1", "age_s": 600 + i, "reworked": 1 if i == 8 else 0, "priority": None, "facts": 9,
                 "fields": {"pull": i - 6, "commit": "abc", "log": "x" * 500, "list": [1, 2]}} for i in (8, 9)]
        rows += [{"item": f"SCRUM-{i}", "step": None, "last_step": "close_ticket", "pile": "<end>", "closed": True,
                  "done": True, "finished": "t0", "outcome": "closed", "fields": {"pull": i}} for i in range(1, 15)]
        return {"workflows": [{"workflow": "delivery_live", "steps": ["write", "sail_check"], "items": rows}], "open": 2}

    monkeypatch.setattr(workflow_tools, "plugin", lambda: SimpleNamespace(**vars(fake.ns()), item_list=item_list))
    out = call(project, store, "list_items", workflow="delivery_live")
    (w,) = out["workflows"]
    assert seen["workflow"] == "delivery_live" and seen["record"] is fake and out["open"] == 2
    assert [r["item"] for r in w["open"]] == ["SCRUM-8", "SCRUM-9"]
    assert w["open"][0] == {"item": "SCRUM-8", "step": "sail_check", "pile": "sail_check.waiting", "since": "t1",
                            "wait": {"reason": "waiting for github.pr.labeled SAILED", "by": "github"}, "age_s": 608,
                            "reworked": 1, "fields": {"pull": 2, "commit": "abc"}}      # long and nested fields left out
    assert len(w["closed_recently"]) == workflow_tools.CLOSED_SHOWN and w["closed_recently"][0]["outcome"] == "closed"
    assert "a pull request's number" in out["shown"]
    assert BY_NAME["list_items"].group == "workflows"
    monkeypatch.setattr(workflow_tools, "plugin", lambda: SimpleNamespace(**vars(fake.ns()), item_list=None))
    with pytest.raises(StoreError, match="no Items list"):
        call(project, store, "list_items")


def test_without_the_plugin_each_tool_says_it_is_not_installed(project, store, monkeypatch):
    monkeypatch.setattr(workflow_tools, "plugin", lambda: None)
    for tool, args in (("declare_workflow", {"definition": DEF, "reason": WHY}), ("list_workflows", {}),
                       ("list_items", {}), ("explain_item", {"item": "ORC-1"})):
        with pytest.raises(StoreError, match="Workflows plugin .* is not installed"):
            call(project, store, tool, **args)
