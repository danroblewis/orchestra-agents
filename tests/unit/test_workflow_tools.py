"""The Supervisor's workflow tools against a fake Workflows plugin (``workflow_tools.plugin``
replaced): a refusal says every problem and writes nothing, the root is the state directory unless
the owner's words say the repository (never committed), the loaded definitions are read back, a
missing plugin is said plainly, and only the Supervisor writes. ``explain_item`` runs on the real
plugin's record (tests/e2e/test_workflows_e2e.py)."""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import agents_project, skill_files, supervisor_knows

from orchestra_agents import design_machines, tooling, workflow_tools
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

    def ns(self):
        return SimpleNamespace(define=self.define, problems=self.problems, roots=self.roots)


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
    assert set(props) == {"name", "about", "items", "planner", "starts", "steps", "piles", "settings"}
    step = props["steps"]["items"]["properties"]
    assert set(step) == {"name", "who", "needs", "lease", "done_when", "outcomes", "guards", "on", "depends", "draft"}
    words = pytest.importorskip("orchestra_workflows.define.words")
    assert set(props) == words.WORKFLOW_KEYS and set(step) == words.STEP_KEYS   # the schema offers no key it refuses
    assert set(step["who"]["properties"]) == {"role", "program", "outside", "person", "outbox"}
    assert props["steps"]["items"]["required"] == ["name"]          # a fan-in or an after step has no who


def test_the_text_states_the_plugins_contract():
    """The words since the vocabulary (S3) and the outbox writes (O1), so the Supervisor writes them."""
    d = BY_NAME["declare_workflow"]
    props = d.input_schema["properties"]["definition"]["properties"]
    step = props["steps"]["items"]["properties"]
    who, done = step["who"]["description"], step["done_when"]
    assert "never performs" in who and "awaiting the owner's approval" in who and "sent, rejected or failed" in who
    assert "never offered" in who and "ORCHESTRA_STEP" in who and "leave who out" in who
    assert set(step["draft"]["properties"]) == {"kind", "target", "content", "fields", "recipients"}
    assert step["draft"]["required"] == ["kind"] and "no text template" in step["draft"]["description"]
    assert "after" in done["properties"] and "{after:" in done["description"] and "no who" in done["description"]
    assert done["properties"]["distinct"]["enum"] == ["source"] and "counts events" in done["properties"]["count"]["description"]
    starts = props["starts"]["items"]["properties"]
    assert "data" in starts and "makes no item" in starts["data"]["description"]
    for word in ("distinct: source", "person: owner", "outbox: owner", "draft", "after", "depends"):
        assert word in d.description, word
    assert "Piles page" not in d.description and "Workflows page" not in d.description


def test_the_guide_and_the_coordinator_say_the_outbox_and_the_counts():
    root = Path(workflow_tools.__file__).resolve().parent
    guide = (root / "guide.md").read_text()
    words = guide[guide.index("## Workflows"):guide.index("### Starting a piece of work")]
    for w in ("never performs it", "awaiting the owner's approval", "`distinct: \"source\"`", "`{\"after\": \"5m\"}`",
              "`person: owner`", "ORCHESTRA_FIELDS", "has no who", "only the owner's tickets"):
        assert w in words, w
    assert "Piles page" not in guide and "Workflows page" not in guide
    coord = (root / "prompts" / "coordinator.md").read_text()
    assert "awaiting the owner's approval" in coord and "outbox step's draft" in coord


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


def test_a_write_the_plugin_sent_to_the_repositorys_copy_is_said_as_the_repositorys(project, store, fake, monkeypatch):
    """T2f s10: the state directory's copy was shadowed by the repository's; the plugin now writes
    the copy that is read and says which (Written.root), and the answer says it is uncommitted."""
    def to_repository(project, definition, root="state"):
        path = project.repo_root / ".orchestra" / "workflows" / definition["name"] / "workflow.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(definition))
        return SimpleNamespace(path=path, root="repository", notes=["written to the repository's copy, not the state directory"])
    monkeypatch.setattr(fake.define, "write", to_repository)
    out = call(project, store, "declare_workflow", definition=DEF, reason=WHY)
    assert out["root"] == "repository" and out["committed"] is False and "hand_to_coordinator" in out["next"]


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

    record = object()
    Record = SimpleNamespace(for_project=lambda o: record)
    monkeypatch.setattr(workflow_tools, "plugin",
                        lambda: SimpleNamespace(**vars(fake.ns()), Record=Record, item_list=item_list))
    out = call(project, store, "list_items", workflow="delivery_live")
    (w,) = out["workflows"]
    assert seen["workflow"] == "delivery_live" and seen["record"] is record and out["open"] == 2
    assert [r["item"] for r in w["open"]] == ["SCRUM-8", "SCRUM-9"]
    assert w["open"][0] == {"item": "SCRUM-8", "step": "sail_check", "pile": "sail_check.waiting", "since": "t1",
                            "wait": {"reason": "waiting for github.pr.labeled SAILED", "by": "github"}, "age_s": 608,
                            "reworked": 1, "fields": {"pull": 2, "commit": "abc"}}      # long and nested fields left out
    assert len(w["closed_recently"]) == workflow_tools.CLOSED_SHOWN and w["closed_recently"][0]["outcome"] == "closed"
    assert "a pull request's number" in out["shown"]
    assert BY_NAME["list_items"].group == "workflows"
    monkeypatch.setattr(workflow_tools, "plugin", lambda: SimpleNamespace(**vars(fake.ns()), Record=Record, item_list=None))
    with pytest.raises(StoreError, match="no Items list"):
        call(project, store, "list_items")


REVIEW_FOR = [{"event": "github.pr.approved", "via": "done_when"},
              {"event": "github.pr.changes_requested", "via": "on", "to": "write"},
              {"event": "github.pr.commented", "via": "on", "data": {"state": "commented"}, "to": "sail"}]


def test_a_wait_says_every_exit_in_words_and_the_fact_it_entered_on():
    w = workflow_tools.wait_of({"reason": "waiting on github", "fact": "f1", "entered_on": "f1", "by": "github",
                                "until": None, "kind": "outside", "for": REVIEW_FOR})
    assert w == {"reason": "waiting on github", "by": "github", "kind": "outside", "entered_on": "f1",
                 "ends_on": "until github.pr.approved, or github.pr.changes_requested (to write), or "
                            "github.pr.commented with state = commented (to sail)"}
    assert "guard" not in json.dumps(w) and "fact" not in w
    assert workflow_tools.wait_of({"reason": "never offered", "fact": "f0"}) == {"reason": "never offered",
                                                                                "entered_on": "f0"}   # an older plugin's
    assert workflow_tools.wait_of({"for": [{"when": "60 s have passed", "via": "on", "to": None}]})["ends_on"] == \
        "until 60 s have passed (to the end)"
    assert workflow_tools.wait_of(None) is None


def test_a_closed_item_is_never_waiting(project, store, fake, monkeypatch):
    def item_list(record, defs, now, workflow=None):
        live = {"item": "SCRUM-20", "step": "review", "pile": "review.waiting",
                "wait": {"reason": "waiting on github", "fact": "f2", "entered_on": "f2", "for": REVIEW_FOR}}
        done = {"item": "SCRUM-17", "last_step": "sail", "pile": "<end>", "closed": True, "outcome": "sent",
                "wait": {"reason": "stale", "fact": "f1"}}
        return {"workflows": [{"workflow": "delivery_live", "steps": ["review"], "items": [live, done]}], "open": 1}

    monkeypatch.setattr(workflow_tools, "plugin", lambda: SimpleNamespace(
        **vars(fake.ns()), Record=SimpleNamespace(for_project=lambda o: object()), item_list=item_list))
    (w,) = call(project, store, "list_items")["workflows"]
    assert w["open"][0]["wait"]["ends_on"].startswith("until github.pr.approved, or ")
    assert w["closed_recently"] == [{"item": "SCRUM-17", "pile": "<end>", "last_step": "sail", "outcome": "sent"}]
    out = call(project, store, "list_items")
    assert "item.closed" in out["shown"] and "never waiting" in out["shown"]
    assert "item.closed" in BY_NAME["list_items"].description and "item.closed" in BY_NAME["read_events"].description


def test_without_the_plugin_each_tool_says_it_is_not_installed(project, store, monkeypatch):
    monkeypatch.setattr(workflow_tools, "plugin", lambda: None)
    for tool, args in (("declare_workflow", {"definition": DEF, "reason": WHY}), ("list_workflows", {}),
                       ("list_items", {}), ("explain_item", {"item": "ORC-1"})):
        with pytest.raises(StoreError, match="Workflows plugin .* is not installed"):
            call(project, store, tool, **args)


def test_a_drafts_kind_is_one_the_owner_configures_never_an_example_that_exists_nowhere():
    step = BY_NAME["declare_workflow"].input_schema["properties"]["definition"]["properties"]["steps"]["items"]["properties"]
    kind = step["draft"]["properties"]["kind"]["description"]
    assert "[outbox.kinds]" in kind and "sail.label" not in kind and "review.ping" not in kind


def test_an_every_start_takes_at_and_tz_and_the_answer_says_the_clock(project, store, fake):
    """T2b s10: '9am every day' was claimed while the workflow started every 24h at 00:00 UTC."""
    starts = BY_NAME["declare_workflow"].input_schema["properties"]["definition"]["properties"]["starts"]
    props = starts["items"]["properties"]
    assert {"every", "at", "tz"} <= set(props) and "HH:MM" in props["at"]["description"]
    assert "00:00 UTC" in props["at"]["description"] and "America/Los_Angeles" in props["tz"]["description"]
    words = pytest.importorskip("orchestra_workflows.define.words")
    assert {"every", "at", "tz"} == words.START_EVERY_KEYS                    # the schema offers what the plugin takes
    timed = {**DEF, "starts": [{"every": "24h", "at": "09:00", "tz": "America/Los_Angeles"}]}
    out = call(project, store, "declare_workflow", definition=timed, reason=WHY)
    assert out["clock"] == "every 24h at 09:00 America/Los_Angeles: say this run time, and no other"
    assert workflow_tools.clock_words([{"every": "24h"}]).startswith("every 24h, counted from 00:00 UTC")
    assert "the project's local zone" in workflow_tools.clock_words([{"every": "24h", "at": "09:00"}])
    assert workflow_tools.clock_words([{"request": True}]) is None
    [w] = call(project, store, "list_workflows")["workflows"]
    assert w["clock"] == "every 24h at 09:00 America/Los_Angeles"


def test_the_items_at_a_step_waiting_outside_are_listed_for_an_incident(project, store, fake, monkeypatch):
    """T2b s14: an incident on PSVC003 (SAIL's runners) was told as no delivery impact while an item
    waited at sail_check. The plugin serves no setting naming a step's service: the items waiting on
    an outside system are listed by step, and the note says an incident there may hold them."""
    call(project, store, "declare_workflow", reason=WHY, definition={**DEF, "name": "delivery_live", "steps": [
        {"name": "write", "who": {"role": "worker"}}, {"name": "sail_check", "who": {"outside": "github"}}]})

    def item_list(record, defs, now, workflow=None):
        rows = [{"item": "delivery_live-fd37ed", "step": "sail_check", "wait": {"reason": "waiting on github", "by": "github"},
                 "fields": {"key": "SCRUM-22", "pull": 20}},
                {"item": "SCRUM-23", "step": "write", "wait": {"reason": "offered", "by": "worker"}},
                {"item": "SCRUM-21", "last_step": "close_ticket", "closed": True, "step": "sail_check"}]
        return {"workflows": [{"workflow": "delivery_live", "steps": ["write", "sail_check"], "items": rows}], "open": 2}

    monkeypatch.setattr(workflow_tools, "plugin", lambda: SimpleNamespace(
        **vars(fake.ns()), Record=SimpleNamespace(for_project=lambda o: object()), item_list=item_list))
    out = call(project, store, "list_items")
    assert out["waiting_outside"] == [{"workflow": "delivery_live", "step": "sail_check", "system": "github",
                                       "items": [{"item": "delivery_live-fd37ed", "fields": {"key": "SCRUM-22", "pull": 20}}]}]
    assert "holds the items at that step: name them" in out["outside_note"]
    assert "never say an incident leaves them unaffected" in out["outside_note"]


def test_the_alert_line_names_the_items_an_incident_may_hold(project):
    text = " ".join(supervisor_knows(project).split())
    assert "an incident on a service a step waits on holds the items at that step: name them" in text
    assert "never say the work is unaffected while an item waits at such a step" in text


def test_the_owners_this_machines_clock_is_declared_as_the_hosts_zone(project, store, fake, monkeypatch):
    """T2e s10b, s15: "This machine's clock." was neither declared nor taken: it was asked again."""
    monkeypatch.setenv("TZ", "Asia/Tokyo")                           # a faked local zone
    for words in ("This machine's clock.", "local", "here"):
        timed = {**DEF, "starts": [{"every": "24h", "at": "09:00", "tz": words}]}
        out = call(project, store, "declare_workflow", definition=timed, reason=WHY)
        assert out["clock"] == "every 24h at 09:00 Asia/Tokyo: say this run time, and no other"
        assert out["zone"].startswith(f"{words!r} is this machine's zone, Asia/Tokyo: declared 09:00 Asia/Tokyo")
        assert "do not ask for the zone again" in out["zone"]
        [w] = call(project, store, "list_workflows")["workflows"]
        assert w["clock"] == "every 24h at 09:00 Asia/Tokyo"
    out = call(project, store, "declare_workflow", reason=WHY,
               definition={**DEF, "starts": [{"every": "24h", "at": "09:00", "tz": "UTC"}]})
    assert "zone" not in out


def test_the_hosts_zone_is_its_iana_name(monkeypatch):
    from orchestra_agents import clock
    monkeypatch.setenv("TZ", ":Europe/Berlin")
    assert clock.host_zone() == "Europe/Berlin" and clock.resolve_tz("this machine's clock") == "Europe/Berlin"
    assert clock.resolve_tz("America/New_York") == "America/New_York" and clock.resolve_tz(None) is None
    monkeypatch.setenv("TZ", "nowhere")
    monkeypatch.setattr("os.path.realpath", lambda p: "/usr/share/zoneinfo/Pacific/Auckland")
    assert clock.host_zone() == "Pacific/Auckland"
    e = design_machines.engagement({"mode": "scheduled", "at": "09:00", "tz": "This machine's clock."})
    assert e == {"mode": "scheduled", "at": "09:00", "tz": "Pacific/Auckland"}


def test_the_design_prompt_takes_the_zone_answer():
    text = " ".join(skill_files().split())
    assert "when it is \"this machine's clock\", \"local\" or \"here\", pass those words as `tz`" in text
    assert "Declare it in that turn, say what was declared, and never ask for the zone again" in text


SLACK_QUEUES = {   # orchestra-workflows' examples/slack_queues definition, as it ships (T14)
    "name": "slack_queues", "about": "Threads in the SRE team's Slack channels, answered within the channel's policy.",
    "items": {"one": "thread", "many": "threads"}, "planner": "coordinator",
    "starts": [{"event": "slack.thread.started"}],
    "steps": [
        {"name": "awaiting_reply", "who": {"outside": "slack"},
         "done_when": {"event": "slack.thread.replied", "data": {"first": True}}, "outcomes": {"done": "awaiting_check"},
         "guards": {"within": {"setting": {"data": "channel_name"}}, "then": "late"}},
        {"name": "late", "who": {"outside": "slack"},
         "done_when": {"event": "slack.thread.replied", "data": {"first": True}}, "outcomes": {"done": "awaiting_check"}},
        {"name": "awaiting_check", "who": {"outside": "slack"},
         "done_when": {"event": "slack.thread.checked", "data": {"emoji": {"setting": "check_emoji"}}},
         "outcomes": {"done": None}, "guards": {"within": {"setting": "check_within"}, "then": "unchecked"}},
        {"name": "unchecked", "who": {"outside": "slack"},
         "done_when": {"event": "slack.thread.checked", "data": {"emoji": {"setting": "check_emoji"}}},
         "outcomes": {"done": None}}],
    "settings": {"sre-help": {"type": "string", "default": "2h"}, "oncall": {"type": "string", "default": "30m"},
                 "check_emoji": {"type": "string", "default": "white_check_mark"},
                 "check_within": {"type": "string", "default": "3h"}}}

JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list,
              "null": type(None)}


def conforms(value, schema: dict, where: str = "definition") -> list[str]:
    """The JSON Schema subset the catalogue uses (type, enum, anyOf, properties, required, items,
    additionalProperties): every place ``value`` breaks ``schema``, so a shape the plugin accepts
    and the tool's schema refuses (a model's client may enforce it) is found here, not at the owner's."""
    if "anyOf" in schema:
        return [] if any(not conforms(value, s, where) for s in schema["anyOf"]) else [f"{where}: no shape fits {value!r}"]
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        ok = any(isinstance(value, JSON_TYPES[t]) and not (t in ("integer", "number") and isinstance(value, bool))
                 for t in types)
        if not ok:
            return [f"{where}: {value!r} is not {'/'.join(types)}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where}: {value!r} not in {schema['enum']}"]
    out = []
    if isinstance(value, dict):
        out += [f"{where}: {k} missing" for k in schema.get("required", []) if k not in value]
        props, extra = schema.get("properties", {}), schema.get("additionalProperties")
        for k, v in value.items():
            if k in props:
                out += conforms(v, props[k], f"{where}.{k}")
            elif isinstance(extra, dict):
                out += conforms(v, extra, f"{where}.{k}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, v in enumerate(value):
            out += conforms(v, schema["items"], f"{where}[{i}]")
    return out


@pytest.mark.parametrize("within", ["2h", {"setting": "check_within"}, {"setting": {"data": "channel_name"}}])
def test_the_schema_takes_every_within_the_plugin_takes(within):
    """e (O1): ``within`` is a duration, a setting or the setting a field names (workflows
    ``define/steps.py`` ``within_ref``); a schema saying only "string" refused slack_queues."""
    schema = BY_NAME["declare_workflow"].input_schema["properties"]["definition"]
    d = json.loads(json.dumps(SLACK_QUEUES))
    d["steps"][0]["guards"]["within"] = within
    assert conforms(d, schema) == []
    assert conforms({**d, "steps": [{"name": "x", "guards": {"within": 7200}}]}, schema)   # a number is no duration


def test_the_shipped_definitions_fit_the_schema_and_the_plugin_accepts_them():
    """Value shapes, not only key names: what fits the tool's schema is what the plugin parses."""
    schema = BY_NAME["declare_workflow"].input_schema["properties"]["definition"]
    for d in (DEF, SLACK_QUEUES):
        assert conforms(d, schema) == [], d["name"]
    pytest.importorskip("orchestra_workflows")
    import importlib
    parse = importlib.import_module("orchestra_workflows.define.parse").parse
    for d in (DEF, SLACK_QUEUES):
        wf, problems = parse(json.loads(json.dumps(d)))
        assert problems == [] and wf is not None, problems


@pytest.mark.parametrize("tasking", [True, False])
def test_the_owner_is_never_asked_to_commit_discard_or_land_a_file(project, store, fake, monkeypatch, tasking):
    """R3 e (O5), M2: T2i's answer said "Commit it yourself if you want it kept". The answer says
    where the definition lives and that it is live, with or without the task system."""
    from orchestra_agents import installed, integrate_handoff, patching
    from orchestra_agents.harness.tools import BY_NAME
    monkeypatch.setattr(installed, "tasking", lambda: tasking)
    out = call(project, store, "declare_workflow", definition=DEF, reason=WHY, root="repository",
               quote="keep it in the repository")
    nxt = out["next"]
    assert ".orchestra/workflows/write_review/workflow.json" in nxt and "live there now" in nxt
    assert "never ask them to commit, discard or land it" in nxt and "theirs to commit" not in nxt
    assert ("hand_to_coordinator" in nxt) is tasking
    root = Path(workflow_tools.__file__).resolve().parent
    guide = " ".join((root / "guide.md").read_text().split())
    assert "the owner commits it" not in guide and "never ask them to commit, discard or land it" in guide
    assert "the owner commits it" not in BY_NAME["integrate_system"].description
    assert "the owner commits it" not in integrate_handoff.who_lands({"root": "repository", "name": "jira_poller"})
    assert ": ask the owner to commit" not in Path(patching.__file__).read_text()
    text = " ".join(supervisor_knows(project).split())
    assert "Never ask the owner to commit, discard or land a file." in text


WHY_WAIT = {"reason": "attention raised", "why": "not past step ci of delivery_live within 30 min", "by": "coordinator",
            "kind": "attention", "entered_on": "f9"}


def test_an_attention_waits_why_reaches_list_items_and_explain_item(project, store, fake, monkeypatch):
    """R3 b (O4): asked what was stuck, the Supervisor said "the attention events carry no reason".
    The plugin's walk puts the attention's reason on the wait as why (T16a); both tools pass it on."""
    row = {"item": "SCRUM-34", "step": "ci", "pile": "ci.waiting", "wait": WHY_WAIT, "since": "t1", "age_s": 7200,
           "at": {"delivery_live": {"step": "ci", "pile": "ci.waiting", "wait": WHY_WAIT}}}

    class Rec:
        def explain(self, item):
            return {"fields": {}, "timeline": [], "facts": 3}

        def items(self, pred):
            return [row] if pred(row) else []

        def item(self, item):
            return {"item": item}

    record = Rec()
    item_list = lambda record, defs, now, workflow=None: {
        "workflows": [{"workflow": "delivery_live", "steps": ["ci"], "items": [row]}], "open": 1}
    monkeypatch.setattr(workflow_tools, "plugin", lambda: SimpleNamespace(
        **vars(fake.ns()), Record=SimpleNamespace(for_project=lambda o: record), item_list=item_list))
    listed = call(project, store, "list_items")["workflows"][0]["open"][0]["wait"]
    assert listed["why"] == WHY_WAIT["why"] and listed["reason"] == "attention raised"
    ex = call(project, store, "explain_item", item="SCRUM-34")
    assert ex["summary"]["wait"]["why"] == WHY_WAIT["why"]
    assert ex["positions"]["delivery_live"]["wait"]["why"] == WHY_WAIT["why"]
    assert "why (an attention's own cause" in BY_NAME["list_items"].description
    text = " ".join(supervisor_knows(project).split())
    assert "a wait's `why`" in text and "is the cause to report" in text
    assert "never say the attention events carry no reason" in text


JOINED = {"name": "ticket_delivery", "starts": [{"event": "jira.issue.assigned"}],
          "steps": [{"name": "open_pr", "who": {"outside": "github"}, "done_when": {"event": "github.pr.opened", "of": "children"},
                     "outcomes": {"done": "sail"}},
                    {"name": "sail", "who": {"outside": "bot.github.sail-bot"},
                     "done_when": {"event": "github.pr.labeled", "of": "children", "data": {"label": "SAILED"}},
                     "outcomes": {"done": None}}]}
STATUS_ONLY = {"name": "ticket_delivery", "starts": [{"event": "jira.issue.assigned"}],
               "steps": [{"name": "in_progress", "who": {"outside": "jira"},
                          "done_when": {"event": "jira.issue.transitioned", "data": {"to": "In Review"}},
                          "outcomes": {"done": "in_review"}},
                         {"name": "in_review", "who": {"outside": "jira"},
                          "done_when": {"event": "jira.issue.transitioned", "data": {"to": "Done"}}, "outcomes": {"done": None}}]}


def test_a_missing_link_is_asked_for_never_hidden_by_dropping_the_relation(project, store, fake):
    """Bootstrap run 5, step 8: tickets waited at open_pr because no pull request carried its ticket's
    key, and the Supervisor re-declared ticket_delivery to follow Jira's status only, on the feed's
    "Proceed as you think best." That drops the owner's goal (the ticket joined to its pull requests)
    and hides the gap: refused, with the linker to ask for; only the owner's words about the link drop it."""
    call(project, store, "declare_workflow", definition=JOINED, reason="the owner's goal one: each ticket with its pull requests")
    with pytest.raises(StoreError) as e:
        call(project, store, "declare_workflow", definition=STATUS_ONLY, reason="the tickets follow Jira, each carrying the ticket key")
    said = str(e.value)
    assert "nothing written" in said and "github.pr.opened of its children" in said
    assert "linker" in said and "item.related {kind: parent}" in said and "branch or title" in said and "one sentence" in said
    assert "relate_prs" in said and "declare_program" in said and "never ask for one to be written" in said
    path = project.orchestra.state_dir / "workflows" / "ticket_delivery" / "workflow.json"
    assert json.loads(path.read_text()) == JOINED                        # the definition, and its gap, stay as they were
    store.set_agent_state("supervisor", turn={"n": 2, "id": "sessions/supervisor/2", "message": "Proceed as you think best.", "at": 2.0})
    with pytest.raises(StoreError, match="not a general go-ahead"):
        call(project, store, "declare_workflow", definition=STATUS_ONLY, reason="the owner said to proceed",
             quote="Proceed as you think best.")
    kept = {**JOINED, "steps": [*JOINED["steps"][:1], {**JOINED["steps"][1], "guards": {"within": "2h"}}]}
    assert call(project, store, "declare_workflow", definition=kept, reason="a guard on the sail step")["ok"]   # relations kept
    store.set_agent_state("supervisor", turn={"n": 3, "id": "sessions/supervisor/3",
                                              "message": "No linker for now: follow Jira's status only, and show the gap.", "at": 3.0})
    out = call(project, store, "declare_workflow", definition=STATUS_ONLY, reason="the owner chose Jira's status until a linker",
               quote="follow Jira's status only")
    assert out["ok"] and out["author"] == "owner" and json.loads(path.read_text()) == STATUS_ONLY


def test_the_prompt_says_to_ask_for_the_link_a_goal_needs():
    text = " ".join(skill_files().split())
    assert "never silently remove the goal" in text and "linker" in text and "item.related" in text
    assert "`relate_prs`" in text and "`declare_program`" in text and "Never ask for one to be written" in text
    assert "may I have one written" not in text and 'never "fully running"' in text
