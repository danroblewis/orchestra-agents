"""A large tool answer never stays whole in the Supervisor's context (answer_store.py): a session that
holds `read_answer` reads at most TRIM characters of it, with its shape and a pointer to the whole,
kept, which `read_answer` reads part by part. A session without it keeps the old bound."""

import json
import subprocess

import pytest
from conftest import agents_project

from orchestra_agents import access, answer_store, tooling
from orchestra_agents.answer_store import TRIM
from orchestra_agents.answers import BOUND
from orchestra_agents.builtins import ROLES
from orchestra_agents.harness import caller as caller_mod
from orchestra_agents.harness.tools import BY_NAME
from orchestra_agents.store import AgentStore, StoreError

SURVEY = {"ok": True, "store": "prod", "families": [{"name": f"shop_orders_family_{i}_total", "kind": "counter",
                                                      "labels": ["service", "queue", "region"], "series": i * 3}
                                                     for i in range(220)], "next": "find_balance with level all"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


def caller(project, groups, monkeypatch, answer=SURVEY):
    store = AgentStore(project)
    token = access.issue(store, "supervisor", "supervisor")
    c = caller_mod.Caller({"ORCHESTRA_REPO": str(project.repo_root), "ORCHESTRA_ROLE": "supervisor", access.TOKEN_ENV: token},
                          list(groups))
    real = c.call
    monkeypatch.setattr(c, "call", lambda name, args: answer if name == "explore_source" else real(name, args))
    return c


def test_a_20kb_survey_is_trimmed_with_its_shape_and_a_pointer(project, monkeypatch):
    whole = json.dumps(SURVEY, indent=1)
    assert len(whole) > 20_000
    c = caller(project, ROLES["supervisor"].tools, monkeypatch)
    got = c.answer("explore_source", {"source": "prod"})
    assert len(got) <= TRIM + 400 and got.startswith("[explore_source's answer, ") and "families: 220 items" in got
    aid = got.rsplit("read_answer with id ", 1)[1].split(" ", 1)[0]
    parts, n = [got.split("]\n", 1)[1].rsplit("\n[answer ", 1)[0]], 2
    while True:                                              # the whole is kept: read part by part, each bounded
        more = c.answer("read_answer", {"id": aid, "part": n})
        assert len(more) <= TRIM
        parts.append(more.rsplit("\n[answer ", 1)[0])
        if "the last." in more:
            break
        n += 1
    assert "".join(parts) == whole
    with pytest.raises(StoreError, match="no part"):
        c.answer("read_answer", {"id": aid, "part": n + 1})
    with pytest.raises(StoreError, match="not an answer's id"):
        tooling.handle(project, AgentStore(project), "read_answer", {"id": "../x"}, role="supervisor")


def test_a_small_answer_and_a_skill_come_back_whole(project, monkeypatch):
    c = caller(project, ROLES["supervisor"].tools, monkeypatch, answer={"ok": True, "families": []})
    assert json.loads(c.answer("explore_source", {"source": "prod"})) == {"ok": True, "families": []}
    skill = json.loads(c.answer("read_skill", {"name": "store-setup"}))     # a skill binds whole: never trimmed
    assert skill["chars"] > TRIM and len(skill["text"]) == skill["chars"]


def test_a_session_without_read_answer_keeps_the_old_bound(project, monkeypatch):
    c = caller(project, ("observe",), monkeypatch)
    got = c.answer("explore_source", {"source": "prod"})
    assert len(got) <= BOUND and "[answer cut at 24,000 characters" in got and "read_answer" not in got   # as before
    assert "read_answer" not in {t.name for t in c.tools} and BY_NAME["read_answer"].group == "guides"


def test_kept_answers_are_pruned(project, monkeypatch):
    monkeypatch.setattr(answer_store, "KEEP", 3)
    ids = [answer_store.keep(project, f"answer {i}") for i in range(5)]
    left = {p.stem for p in (project.state_dir / "answers").glob("a-*.txt")}
    assert len(left) == 3 and ids[-1] in left
