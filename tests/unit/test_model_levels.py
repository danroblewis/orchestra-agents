"""``read_model``'s levels (model_levels.py): a stock fed by a source has the level the Dynamics page
read through that source, said to be "measured by source <name>", never the own store's; a stock
of the own store keeps the own store's level, said so; a source the page cannot read is said so
with the page's reason, never as "no data". The page's answer is faked here; the e2e file reads a
real web server over a fake store."""

import subprocess
import time

import pytest
from conftest import agents_project
from orchestra.metrics import Point, for_project
from orchestra.sd.model import Equation, Flow, Model, SeriesRef, Stock
from orchestra.sd.store import ModelStore

from orchestra_agents import access, model_levels, tooling
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore, StoreError


def eq(form, inputs=(), **params):
    return Equation(form, dict(params), "fitted", "poisson-mle", None, None, {"events": 500.0}, list(inputs))


def mixed() -> Model:
    """A queue fed by the source rig, drained into a store the own metrics store measures."""
    return Model("system", "mixed", time_unit="minute",
                 stocks=[Stock("jobs", "jobs", series=SeriesRef("rig:queue_depth", source="rig")),
                         Stock("done", "done", series=SeriesRef("done_level"))],
                 flows=[Flow("arrive", "arrive", None, "jobs", SeriesRef("rig:jobs_in", source="rig"), [eq("constant", c=3.0)]),
                        Flow("work", "work", "jobs", "done", SeriesRef("rig:jobs_out", source="rig"), [eq("proportional", ["jobs"], k=0.5)])])


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
    ModelStore.for_state(p.orchestra.state_dir).save(mixed(), note="mixed")
    m = for_project(p.orchestra)
    now = time.time()
    m.write([Point("done_level", 7.0, now), Point("queue_depth", 999.0, now), Point("rig:queue_depth", 999.0, now)])
    # a same-named series in the own store is never the source's level
    m.close()
    return p


def read(project, **args):
    tooling.check_args("read_model", args)
    store = AgentStore(project, Events.for_project(project, source="supervisor"))
    return tooling.handle(project, store, "read_model", args, role="supervisor")


def page(levels: dict, error: str | None = None) -> dict:
    return {"ok": True, "model": {"levels": levels, "levels_error": error}}


def line(out, stock: str) -> str:
    return next(x for x in out["summary"] if x.startswith(f"  {stock}: "))


def test_a_source_fed_stocks_level_is_the_pages_with_its_source_and_the_own_stores_unchanged(project, monkeypatch):
    asked = []
    monkeypatch.setattr(model_levels, "fetch", lambda p, v=None: asked.append(v) or page(
        {"jobs": {"t": 1.0, "v": 42.0, "measured_by": "source rig"}, "done": {"t": 1.0, "v": 7.0, "measured_by": "own store"}}))
    out = read(project, raw=True)
    assert line(out, "jobs") == "  jobs: 42 items (measured by source rig)"
    assert line(out, "done") == "  done: 7 items (own store)", "the own store's level, read as before"
    rows = {s["id"]: s for s in out["model"]["stocks"]}
    assert rows["jobs"]["measured_by"] == "source rig" and rows["jobs"]["level"] == 42.0
    assert rows["done"] == {**rows["done"], "level": 7.0, "measured_by": "own store"}
    assert asked == [None]


def test_an_own_store_model_never_asks_the_page(project, monkeypatch):
    ModelStore.for_state(project.orchestra.state_dir).save(
        Model("system", "own", time_unit="minute", stocks=[Stock("done", "done", series=SeriesRef("done_level"))],
              flows=[Flow("out", "out", "done", None, SeriesRef("out_total"), [eq("proportional", ["done"], k=0.1)])]), note="own")

    def never(*a):
        raise AssertionError("the page is read only for a source-fed stock")
    monkeypatch.setattr(model_levels, "fetch", never)
    assert line(read(project), "done") == "  done: 7 items (own store)"


def test_a_source_that_cannot_be_reached_says_why_never_no_data(project, monkeypatch):
    monkeypatch.setattr(model_levels, "fetch", lambda p, v=None: page(
        {"done": {"t": 1.0, "v": 7.0, "measured_by": "own store"}}, "source rig: connection refused; source other: HTTP 500"))
    out = read(project)
    assert line(out, "jobs") == "  jobs: level not read (measured by source rig): connection refused"
    assert line(out, "done") == "  done: 7 items (own store)"
    assert "999" not in "\n".join(out["summary"]), "never the own store's same-named series"

    monkeypatch.setattr(model_levels, "fetch", lambda p, v=None: page({}))
    assert line(read(project), "jobs") == f"  jobs: level not read (measured by source rig): {model_levels.NOT_YET}"

    def down(p, v=None):
        raise StoreError("Orchestra's web server is not running (orchestra web), and a source's levels are read through it")
    monkeypatch.setattr(model_levels, "fetch", down)
    out = read(project)
    assert line(out, "jobs").startswith("  jobs: level not read (measured by source rig): Orchestra's web server is not running")
    assert line(out, "done") == "  done: 7 items (own store)", "the own store's level does not wait on the web server"


def test_without_a_web_server_recorded_the_fetch_says_so(project):
    with pytest.raises(StoreError, match="web server is not running"):
        model_levels.fetch(project)
