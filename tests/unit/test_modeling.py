"""The modeling tools (modeling.py): each answers with a short summary, key numbers first, and
Orchestra's whole answer only on request; the groups (model, experiments, replay, predict) are
granted as the roles need them and enforced on every call; a prediction is the caller's own (a
worker's, of its own ticket), its change announced only by it; and the verdict wakes it.

Orchestra's experiments run in this process on a known system (a chain: arrivals to a queue,
moved on to a ready stock, a side supply, a drain), its history made by the model itself."""

import json
import subprocess

import pytest
from conftest import FakeHarness, agents_project
from orchestra.events import Events as OrchestraEvents
from orchestra.sd.experiments.api import Experiments
from orchestra.sd.experiments.fabricate import as_history, fabricate
from orchestra.sd.model import Equation, Flow, Model, SeriesRef, Stock
from orchestra.sd.store import ModelStore
from orchestra.sd.strategies import sd_installed

from orchestra_agents import access, modeling, tooling
from orchestra_agents.events import Events
from orchestra_agents.harness import dummy_script
from orchestra_agents.harness.tools import BY_NAME, tools_for
from orchestra_agents.host import AgentHost
from orchestra_agents.listening import listens_for
from orchestra_agents.org import AgentDef, built_in_agents, event_matches
from orchestra_agents.roles import RepoRoleStore, RoleDef
from orchestra_agents.store import AgentStore, StoreError

pytestmark = pytest.mark.skipif(not sd_installed(), reason="running the model needs orchestra[sd]")
T0 = 1_800_000_000.0


def eq(form, inputs=(), **params):
    return Equation(form, dict(params), "fitted", "poisson-mle", None, None, {"events": 500.0, "dispersion": 1.0, "live_minutes": 120.0},
                    list(inputs))


def chain() -> Model:
    m = Model("system", "chain", time_unit="minute",
              stocks=[Stock("queue", "queue", series=SeriesRef("queue")), Stock("ready", "ready", series=SeriesRef("ready"))],
              flows=[Flow("arrive", "arrive", None, "queue", SeriesRef("arrive_total"), [eq("constant", c=3.0)]),
                     Flow("move", "move", "queue", "ready", SeriesRef("move_total"), [eq("proportional", ["queue"], k=0.5)]),
                     Flow("side", "side", None, "ready", SeriesRef("side_total"), [eq("constant", c=3.0)]),
                     Flow("drain", "drain", "ready", None, SeriesRef("drain_total"), [eq("proportional", ["ready"], k=0.3)])])
    m.discovery = {"fit_start": T0, "inputs": {}}
    return m


HISTORY = as_history(chain(), fabricate(chain(), {"inputs": {}}, start=T0, end=T0 + 3600, step_s=5.0, seed=3,
                                        levels={"queue": 0.0, "ready": 60.0}))
WINDOW = {"start": T0, "end": T0 + 3600}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    for k in ("ORCHESTRA_SOURCE", "ORCHESTRA_ROLE", access.TOKEN_ENV):
        monkeypatch.delenv(k, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    p = agents_project(repo)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "the project"], cwd=repo, check=True)
    ModelStore.for_state(p.orchestra.state_dir).save(chain(), note="the chain")
    monkeypatch.setattr(modeling, "experiments", lambda project, source: Experiments(project.orchestra, source,
                                                                                       history=lambda model, s, e: HISTORY))
    return p


def as_(project, role, instance=None):
    """A store whose events go out as the agent instance a session of ``role`` runs as."""
    return AgentStore(project, Events.for_project(project, source=instance or role))


def call(project, role, tool, ticket="", instance=None, **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, as_(project, role, instance), tool, args, role=role, ticket_env=ticket)


def text(out) -> str:
    return "\n".join(out["summary"])


def test_the_groups_are_granted_as_each_role_needs_them_and_enforced(project):
    names = lambda role: {t.name for t in tools_for(access.granted_groups(project, role))}
    assert {"read_model", "create_experiment", "run_replay", "run_forward", "commit_prediction", "announce_change"} <= names("supervisor")
    assert {"read_model", "create_experiment", "run_inverse", "run_replay"} <= names("coordinator")
    assert not {"commit_prediction", "announce_change"} & names("coordinator")
    assert {"read_model", "run_replay", "commit_prediction", "report_ticket"} <= names("worker")
    assert {"read_model", "read_predictions", "run_replay", "report_review"} <= names("reviewer")
    assert not {"create_experiment", "run_forward", "commit_prediction"} & names("reviewer")
    with pytest.raises(StoreError, match="refused create_experiment: it needs the 'experiments' tool group, and role 'reviewer'"):
        call(project, "reviewer", "create_experiment", title="mine")
    with pytest.raises(StoreError, match="refused commit_prediction: it needs the 'predict' tool group"):
        call(project, "coordinator", "commit_prediction", id="x-1", horizon="1h")
    # a custom role may be granted the groups like any other
    RepoRoleStore(project).save(RoleDef(name="curator", description="keeps the model honest", tools=["model", "replay"]), "You curate.")
    from orchestra_agents.org_store import RepoOrgStore
    RepoOrgStore(project).save_agent(AgentDef(name="curator", roles=["curator"], kind="continuous"))
    assert "drain" in text(call(project, "curator", "read_model"))
    with pytest.raises(StoreError, match="needs the 'experiments' tool group"):
        call(project, "curator", "create_experiment", title="x")


def test_the_model_reads_as_a_summary_and_whole_on_request(project):
    out = call(project, "worker", "read_model", ticket="T-1")
    s = text(out)
    assert "rates are per minute" in s and set(out) == {"ok", "summary"}
    assert "  drain (ready -> outside) = k * ready [k=0.3]; fitted on 500 events, ±4%" in out["summary"]
    assert "  arrive (outside -> queue) = c [c=3]; fitted on 500 events, ±4%" in out["summary"]
    raw = call(project, "worker", "read_model", ticket="T-1", raw=True)
    assert raw["model"]["flows"][3]["equation"] == "k * ready" and "raw" in raw["model"]
    guide = call(project, "reviewer", "modeling_guide")["guide"]
    assert "## Testing a hypothesis about a change" in guide and "`if_then_else` (3)" in guide, "the language comes from Orchestra"


def test_series_are_summarised_with_the_latest_readings_and_paged_on_request(project):
    out = call(project, "reviewer", "read_series", ids=["ready", "drain"], start=T0, end=T0 + 3600)
    ready = next(x for x in out["summary"] if x.startswith("  ready"))
    assert ready.startswith("  ready (stock, items): 60 -> ") and "trend -" in ready and "recent: " in ready and "points" not in out
    page = call(project, "reviewer", "read_series", ids=["ready"], start=T0, end=T0 + 3600, points=True, limit=100)
    assert len(page["points"]["ready"]["points"]) == 100 and page["points"]["ready"]["next"]
    more = call(project, "reviewer", "read_series", ids=["ready"], start=T0, end=T0 + 3600, points=True, limit=100,
                after=page["points"]["ready"]["next"])
    assert more["points"]["ready"]["points"][0][0] > page["points"]["ready"]["points"][-1][0]


def test_an_experiment_is_drafted_replayed_and_run_forward_with_summaries(project):
    w = lambda tool, **a: call(project, "worker", tool, ticket="T-1", instance="worker/1", **a)
    e = w("create_experiment", title="capped drain", purpose="does a cap explain the hour?")["experiment"]
    out = w("edit_experiment", id=e, edit={"op": "replace_flow", "id": "drain", "equation": "min(k * ready, cap)",
                                           "params": {"k": 0.3, "cap": 9}})
    assert "  flow drain changed: k * ready [k=0.3]  ->  min(k * ready, cap) [k=0.3, cap=9]" in out["summary"]
    with pytest.raises(StoreError, match=r"unknown name nope.*\(edit 1; the draft is unchanged\)"):
        w("edit_experiment", id=e, edits=[{"op": "set_params", "flow": "arrive", "params": {"c": 4}},
                                          {"op": "replace_flow", "id": "move", "equation": "k * nope", "params": {"k": 1}}])
    with pytest.raises(StoreError, match="only its owner changes it"):
        call(project, "worker", "edit_experiment", ticket="T-2", instance="worker/2", id=e, edit={"op": "remove_flow", "id": "side"})
    assert "  experiment" not in text(w("diff_experiment", id=e)) and "changes from the base: 1" in text(w("diff_experiment", id=e))

    rep = call(project, "reviewer", "run_replay", ticket="T-1", instance="reviewer/1", id=e, **WINDOW)
    s = text(rep)
    assert "experiment against base: stocks" in s and "WORSE than the base on: " in s and "flow drain" in s and isinstance(rep["run"], str)
    fwd = w("run_forward", id=e, horizon="30m", members=8)
    s = text(fwd)
    assert s.splitlines()[1].startswith("NOT TRUSTED") and "  ready: base " in s and "by +30m (band " in s
    assert "not trusted: flow drain: declared" in s, "the reason sits on the stock it touches"
    assert "result" not in json.dumps(fwd), "no trajectories unless asked"
    whole = w("run_forward", id=e, horizon="30m", members=8, raw=True)
    assert len(whole["run"]["result"]["t"]) > 10
    again = w("read_run", experiment=e, run=whole["run"]["id"])
    assert again["summary"][0].startswith(f"forward run {whole['run']['id']}")
    listed = text(w("read_experiment", id=e))
    assert "forward by worker/1" in listed and "replay by reviewer/1" in listed and "flow drain changed" in listed
    w("discard_experiment", id=e)
    assert "(discarded, yours)" in text(w("read_experiment", id=e))


def test_an_inverse_run_answers_with_a_new_draft_on_the_callers_ticket(project):
    w = lambda tool, **a: call(project, "worker", tool, ticket="T-1", instance="worker/1", **a)
    e = w("create_experiment", title="what makes ready larger?")["experiment"]
    out = w("run_inverse", id=e, targets=[{"stock": "ready", "scale": 1.5}], hold=[{"flow": "side"}, {"flow": "drain"}], deadline_s=30,
            **WINDOW)
    s = text(out)
    assert "were REACHED with 1 change(s)" in s and "  change: flow arrive: c 3 -> " in s and "target stock ready scale 1.5" in s
    solved = next(x for x in out["summary"] if x.startswith("the solution is a new draft of yours: experiment "))
    new = solved.split("experiment ")[1].split(" ")[0]
    assert {"id": new, "ticket": "T-1"} in as_(project, "worker").agent_state("worker")["experiments"]
    loose = w("run_inverse", id=e, targets=[{"stock": "ready", "scale": 1.5}], deadline_s=30, **WINDOW)
    assert "AMBIGUOUS" in text(loose)


def test_a_worker_commits_only_its_own_tickets_experiment_and_only_it_announces_the_change(project):
    w = lambda tool, ticket="T-1", **a: call(project, "worker", tool, ticket=ticket, instance="worker/1", **a)
    e = w("create_experiment", title="double arrivals")["experiment"]
    w("edit_experiment", id=e, edit={"op": "replace_flow", "id": "arrive", "equation": "2 * c", "params": {"c": 3}})
    other = w("create_experiment", ticket="T-2", title="another ticket's")["experiment"]
    with pytest.raises(StoreError, match="was not made on your ticket T-1"):
        w("commit_prediction", id=other, horizon="30m")
    out = w("commit_prediction", id=e, horizon="30m")
    change = out["change"]
    assert out["summary"][0].startswith(f"prediction {out['prediction']} recorded for change {change}")
    assert "flow arrive = 2 * c [c=3]" in out["summary"][1] and out["done"].startswith("Changed: committed experiment")
    with pytest.raises(StoreError, match="is not the change of a prediction you committed"):
        call(project, "supervisor", "announce_change", change=change)
    ann = w("announce_change", change=change, note="arrivals doubled")
    assert "announced: system.changed with id " + change in ann["summary"][0]
    with pytest.raises(StoreError, match="already announced"):
        w("announce_change", change=change)
    [ev] = [x for x in OrchestraEvents.for_project(project.orchestra).read(0, 1000)[0] if x["type"] == "system.changed"]
    assert ev["id"] == change and ev["source"] == "worker/1" and ev["subject"] == "T-1" and ev["data"]["prediction"] == out["prediction"]
    preds = text(call(project, "reviewer", "read_predictions", change=change))
    assert f"prediction {out['prediction']} by worker/1 for change {change} (about T-1)" in preds and ": waiting" in preds


def test_the_supervisor_announces_its_predicted_configuration_change_with_the_setup_tool(project):
    s = lambda tool, **a: call(project, "supervisor", tool, **a)
    e = s("create_experiment", title="more reviewers")["experiment"]
    made = s("commit_prediction", id=e, horizon="1h")
    with pytest.raises(StoreError, match="is not the change of a prediction you committed"):
        s("set_setting", key="desk.channels", value='["#ops"]', target="overlay", reason="the owner asked for it", change="nope")
    s("set_setting", key="desk.channels", value='["#ops"]', target="overlay", reason="the owner asked for it", change=made["change"])
    [ev] = [x for x in OrchestraEvents.for_project(project.orchestra).read(0, 1000)[0] if x["type"] == "config.changed"]
    assert ev["id"] == made["change"] and ev["source"] == "supervisor"
    assert as_(project, "supervisor").find_prediction(made["change"])[1]["announced"] == "with config.changed"


def test_a_change_said_to_take_effect_on_landing_is_announced_when_the_gate_lands_it(project):
    w = lambda tool, **a: call(project, "worker", tool, ticket="T-1", instance="worker/3", **a)
    e = w("create_experiment", title="x")["experiment"]
    change = w("commit_prediction", id=e, horizon="1h")["change"]
    assert "will be announced when ticket T-1 ends done" in text(w("announce_change", change=change, when="landed"))
    store = AgentStore(project, Events.for_project(project))
    assert modeling.announce_landed(store, {"type": "gate.landed", "ticket": "T-2"}, "code") == []
    assert modeling.announce_landed(store, {"type": "gate.landed", "ticket": "T-1"}, "code") == [f"announced change {change} of T-1"]
    [ev] = [x for x in OrchestraEvents.for_project(project.orchestra).read(0, 1000)[0] if x["type"] == "system.changed"]
    assert ev["id"] == change and ev["source"] == "worker/3"
    assert modeling.announce_landed(store, {"type": "gate.landed", "ticket": "T-1"}, "code") == [], "once"


def test_the_verdict_wakes_the_agent_that_made_the_prediction(project):
    store = AgentStore(project, Events.for_project(project))
    host = AgentHost(project, store, store.events)
    host.harness = FakeHarness(reply="It passed: arrivals doubled as predicted.")
    host._refresh_org(store.runtime()["agent_state"])
    store.add_prediction("supervisor", {"id": "p1", "change": "c-sup", "experiment": "x-1", "ticket": "", "source": "supervisor"})
    store.add_prediction("worker", {"id": "p2", "change": "c-work", "experiment": "x-2", "ticket": "T-9", "source": "worker/1"})
    o = OrchestraEvents.for_project(project.orchestra, source="orchestra-host")
    for change, verdict in (("c-sup", "passed"), ("c-work", "side effect"), ("someone-else", "passed")):
        o.publish("orchestra.prediction.judged", {"verdict": verdict}, correlationid=change, subject="p", causationid="p")
    host._wake_on_events()
    [w] = [x for x in store.runtime()["wakeups"] if x["target"] == "supervisor"]
    assert "your prediction p1 (experiment x-1) of change c-sup was judged: passed" in w["reason"]
    assert [r for r, *_ in host.verdicts.waiting] == ["worker"]
    host.verdicts.tick()
    for th in list(host.agent_threads.values()):
        th.join(10)
    [s] = [x for x in host.harness.sessions if "verdict" in x.spec.session_id]
    assert s.spec.tool_groups == ("model",) and s.spec.env["ORCHESTRA_TICKET"] == "T-9" and s.spec.env["ORCHESTRA_SOURCE"] == "worker/1"
    assert "judged: side effect" in s.prompts[0] and "Do not change" in s.prompts[0]
    said = [e for e in store.events.read(0, 1000)[0] if e["type"] == "agent.said"]
    assert said[-1]["summary"].startswith("It passed") and said[-1]["source"] == "worker/1"
    host._wake_on_events()
    assert store.find_prediction("c-work")[1]["judged"] == "side effect" and not host.verdicts.waiting


def test_the_verdict_is_in_the_wake_vocabulary_and_in_what_predicting_agents_announce(project):
    a = AgentDef(name="curator", roles=["curator"], wake_on=["orchestra.prediction.judged:side effect"])
    a.validate()
    assert event_matches(a.wake_on, {"type": "orchestra.prediction.judged", "verdict": "side effect"})
    assert not event_matches(a.wake_on, {"type": "orchestra.prediction.judged", "verdict": "passed"})
    b = built_in_agents()
    assert {"orchestra.prediction.judged", "gate.landed"} <= set(listens_for(b["worker"], "code"))
    assert "orchestra.prediction.judged" in listens_for(b["supervisor"], "code")
    assert "orchestra.prediction.judged" not in listens_for(b["reviewer"], "code") + listens_for(b["coordinator"], "code")


def test_a_dummy_script_keeps_a_tools_answer_for_the_next_steps():
    kept = {"e": {"experiment": "x-1", "summary": ["a", "b"], "n": 3}}
    assert dummy_script.fill_kept({"id": "{kept.e.experiment}", "note": "run {kept.e.summary.1}", "k": "{kept.e.n}"}, kept) == \
        {"id": "x-1", "note": "run b", "k": 3}
    with pytest.raises(dummy_script.ScriptError, match="has no 'nope'"):
        dummy_script.fill_kept("{kept.e.nope}", kept)
    with pytest.raises(dummy_script.ScriptError, match="no earlier step of this turn kept an answer as 'm'"):
        dummy_script.fill_kept("{kept.m}", kept)
    [rule] = dummy_script.parse('[[rule]]\nrole = "worker"\nsteps = [{ tool = "read_model", keep = "m" }]\n')
    assert rule.steps[0]["keep"] == "m"
    with pytest.raises(dummy_script.ScriptError, match="keep names the answer"):
        dummy_script.parse('[[rule]]\nrole = "worker"\nsteps = [{ tool = "read_model", keep = "1 bad" }]\n')


def test_every_modeling_tool_has_its_operation(project):
    for name in modeling.COMMANDS:
        assert BY_NAME[name].group in ("model", "experiments", "replay", "predict"), name
    assert {t.name for t in tools_for(["model", "experiments", "replay", "predict"])} == set(modeling.COMMANDS)
