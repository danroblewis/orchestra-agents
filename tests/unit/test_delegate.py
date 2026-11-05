"""`delegate <job> <brief>`: a one-shot sub-session of the Supervisor's harness with a small system
prompt (the job's skill and its tools), on `[agents] delegate_model` (default the Supervisor's), its
spend counted toward the Supervisor's caps, and its short report returned (delegate.py)."""

import subprocess

import pytest
from conftest import FakeHarness, FakeSession, agents_project

import orchestra_agents.harness as harness_pkg
from orchestra_agents import budget, delegate, design, prompting, skills, tooling
from orchestra_agents.builtins import ROLES
from orchestra_agents.harness.base import TurnResult
from orchestra_agents.harness.tools import BY_NAME, tools_for
from orchestra_agents.org import built_in_agents
from orchestra_agents.store import AgentStore, StoreError

BRIEF = "Survey the store prod (declared, match job=shop): its families, the levels and their flows."


class Priced(FakeHarness):
    """A harness whose sub-session reports a cost and what it was started with."""

    def __init__(self, cost: float | None = 0.05):
        super().__init__(reply=None)
        self.cost = cost

    def start(self, spec):
        cost = self.cost

        class S(FakeSession):
            def prompt(self, text, timeout, on_event=None, idle_timeout=None):
                self.prompts.append(text)
                return TurnResult(text="Families: shop_orders_placed_total (counter), orders_open (level, balanced 0.97).",
                                  cost_usd=cost)
        s = S(spec, None)
        self.sessions.append(s)
        return s

    def priced(self, model):
        return True


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(delegate.DELEGATED_ENV, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def run(project, store, monkeypatch, harness, **a):
    monkeypatch.setattr(harness_pkg, "get_harness", lambda name: harness)
    return tooling.handle(project, store, "delegate", {"job": "survey", "brief": BRIEF, **a}, role="supervisor")


def test_delegate_starts_a_sub_session_with_the_small_prompt_and_returns_its_report(project, monkeypatch):
    store, h = AgentStore(project), Priced()
    out = run(project, store, monkeypatch, h)
    assert out["ok"] and out["report"].startswith("Families: shop_orders_placed_total") and out["job"] == "survey"
    (s,) = h.sessions
    full = prompting.with_tools(prompting.agent_prompt(project, built_in_agents()["supervisor"]), ROLES["supervisor"].tools)
    assert s.spec.system_prompt.startswith(delegate.PREAMBLE) and skills.text(project, "survey").strip() in s.spec.system_prompt
    assert len(s.spec.system_prompt) < 6_000 < len(full) and "## Skills: read the one" not in s.spec.system_prompt
    assert s.prompts == [f"[Delegated job: survey]\n\n{BRIEF}"]
    assert set(s.spec.tool_groups) == {"observe", "flows", "guides"} and "delegate" not in s.spec.tool_groups
    assert s.spec.role == "supervisor" and s.spec.read_only and s.spec.env[delegate.DELEGATED_ENV] == "survey"
    assert s._closed and s.spec.session_id not in store.grants()             # one-shot: closed, its token revoked


def test_its_spend_counts_toward_the_cap_of_the_turn_it_runs_in(project, monkeypatch):
    store = AgentStore(project)
    design.begin_turn(store, None)                          # a wake-up: the Supervisor's initiative
    run(project, store, monkeypatch, Priced(0.05))
    s = budget.spent(store)
    assert s["initiative_usd"] == 0.05 and s["delegate_usd"] == 0.05 and s["delegate_turns"] == 1 and s["usd"] == 0.05
    design.begin_turn(store, "survey the shop store")       # the owner's chat
    run(project, store, monkeypatch, Priced(0.25))
    s = budget.spent(store)
    assert s["chat_usd"] == 0.25 and s["delegate_usd"] == 0.30 and s["initiative_usd"] == 0.05
    project.config.chat_usd_per_day = 0.2                  # the chat cap is spent: refused before it starts
    h = Priced()
    with pytest.raises(StoreError, match="not delegated: The Supervisor's chat turns spent"):
        run(project, store, monkeypatch, h)
    assert h.sessions == []


def test_the_model_is_delegate_model_else_the_supervisors(project, monkeypatch):
    store = AgentStore(project)
    project.config.model = "claude-sonnet-5"
    h = Priced()
    assert run(project, store, monkeypatch, h)["model"] == "claude-sonnet-5" and h.sessions[0].spec.model == "claude-sonnet-5"
    project.config.delegate_model = "claude-haiku-5"
    assert run(project, store, monkeypatch, h)["model"] == "claude-haiku-5" and h.sessions[1].spec.model == "claude-haiku-5"


def test_delegate_is_the_supervisors_and_never_nested(project, monkeypatch):
    store = AgentStore(project)
    assert BY_NAME["delegate"].group == "delegate" and "delegate" in {t.name for t in tools_for(ROLES["supervisor"].tools)}
    assert all("delegate" not in groups for _, groups in delegate.JOBS.values())
    for job, (skill, _) in delegate.JOBS.items():
        assert skills.delegated(skill) and skill not in skills.indexed(), job
    with pytest.raises(StoreError, match="no job"):
        run(project, store, monkeypatch, Priced(), job="anything")
    monkeypatch.setenv(delegate.DELEGATED_ENV, "survey")
    with pytest.raises(StoreError, match="never delegates again"):
        run(project, store, monkeypatch, Priced())


def test_the_supervisors_prompt_says_to_delegate_the_bounded_jobs(project):
    prompt = " ".join(prompting.agent_prompt(project, built_in_agents()["supervisor"]).split())
    for job in delegate.JOBS:
        assert f"`{job}`" in prompt, job
    assert "`delegate`" in prompt


def test_a_delegated_turn_on_the_dummy_harness(project):
    """The dummy harness's scripted sub-session: a real one-shot session, its turn played by the
    built-in rule for a delegated job, its report returned."""
    store = AgentStore(project)
    out = tooling.handle(project, store, "delegate", {"job": "judge_experiment", "brief": "Judge experiment x-1."},
                         role="supervisor")
    assert out["ok"] and "judge_experiment" in out["report"] and out["transcript"]
    assert budget.spent(store)["delegate_turns"] == 1
