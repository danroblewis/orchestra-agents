"""The valve (``orchestra_agents/valve/``) against a fake model server in each of its three API
shapes: a good answer is the outcome, a label outside the declared ones or a confidence below the
bound is the fallback, a timeout or a 5xx is tried again with a back-off, a 401 is not; the cache
asks an unchanged item once; the daily cap holds; the token never reaches the log, the output or the
cache; only the declared inputs go in; the environment's proxy is honoured."""

import json
import os
import subprocess
import sys

import pytest
from fake_model import FakeModel

from orchestra_agents.valve import apis, metrics
from orchestra_agents.valve.decide import decide
from orchestra_agents.valve.declaration import exit_map, parse
from orchestra_agents.valve.inputs import fingerprint, gather
from orchestra_agents.valve.ledger import Ledger

TOKEN = "sk-test-0123456789abcdef"
SHAPES = ["openai", "anthropic", "classify"]
ENV = {"ORCHESTRA_WORKFLOW": "oncall", "ORCHESTRA_STEP": "filter", "ORCHESTRA_ITEM": "HK-1",
       "ORCHESTRA_FIELDS": json.dumps({"summary": "the login page is down for every customer", "priority": "P1",
                                       "secret_note": "not for the model"})}


@pytest.fixture
def token_file(tmp_path):
    f = tmp_path / "model.token"
    f.write_text(TOKEN + "\n")
    f.chmod(0o600)
    return f


@pytest.fixture
def fake():
    with FakeModel(TOKEN) as f:
        yield f


def valve(api, base, token_file, **over):
    raw = {"name": "oncall_filter", "question": "Does this ticket need the on-call engineer now?",
           "labels": ["page", "queue", "ignore"], "inputs": {"fields": ["summary", "priority"]},
           "api": api, "base_url": base, "model": "a-model", "token_file": str(token_file), "min_confidence": 0.7,
           "max_calls_per_day": 50, "timeout_s": 0.5, "retries": 2, "backoff_s": 0.01, **over}
    v, problems = parse(raw)
    assert v is not None, problems
    return v


def ask(v, tmp_path, env=ENV):
    return decide(v, gather(v, env, log=[]), Ledger(tmp_path / "state" / v.name), sleep=lambda s: None)


@pytest.mark.parametrize("api", SHAPES)
def test_a_good_answer_is_the_outcome_with_its_score_and_reason(api, fake, token_file, tmp_path):
    fake.script = [{"label": "page", "score": 0.93, "reason": "every customer is affected"}]
    v = valve(api, fake.url, token_file)
    d = ask(v, tmp_path)
    assert (d.outcome, d.label, d.score, d.reason, d.cached) == ("page", "page", 0.93, "every customer is affected", False)
    assert v.code(d.outcome) == 10 and exit_map(v)["10"] == "page" and exit_map(v)["13"] == "ask"
    [r] = fake.requests
    assert r["path"] == {"openai": "/v1/chat/completions", "anthropic": "/v1/messages", "classify": "/classify"}[api]
    if api == "openai":
        fmt = r["body"]["response_format"]["json_schema"]
        assert fmt["strict"] and fmt["schema"]["properties"]["label"]["enum"] == ["page", "queue", "ignore"]
    if api == "anthropic":
        assert r["body"]["tool_choice"] == {"type": "tool", "name": "decide"}
        assert r["headers"]["anthropic-version"] == apis.ANTHROPIC_VERSION
    [line] = Ledger(tmp_path / "state" / v.name).calls()
    assert line["label"] == "page" and line["status"] == 200 and line["attempts"] == 1 and "latency_s" in line
    assert ("cost" in line) == (api == "classify")


@pytest.mark.parametrize("api", SHAPES)
def test_a_label_not_declared_or_a_low_confidence_is_the_fallback(api, fake, token_file, tmp_path):
    fake.script = [{"label": "escalate", "score": 0.99, "reason": "x"}, {"label": "page", "score": 0.4, "reason": "unsure"}]
    v = valve(api, fake.url, token_file)
    d = ask(v, tmp_path)
    assert d.outcome == "ask" and "'escalate', not one of page, queue, ignore" in d.reason
    d = ask(v, tmp_path)                       # a wrong label is not cached: asked again
    assert d.outcome == "ask" and d.label == "page" and "below the declared 0.70" in d.reason and "unsure" in d.reason
    assert len(fake.requests) == 2


@pytest.mark.parametrize("api", SHAPES)
def test_a_5xx_and_a_timeout_are_tried_again_and_a_401_is_not(api, fake, token_file, tmp_path):
    v = valve(api, fake.url, token_file)
    fake.script = [{"status": 503}, {"sleep": 1.0}, {"label": "queue", "score": 0.8, "reason": "next business day"}]
    d = ask(v, tmp_path)
    assert d.outcome == "queue" and len(fake.requests) == 3
    fake.script = [{"status": 500}] * 3
    d = ask(v, tmp_path, {**ENV, "ORCHESTRA_FIELDS": json.dumps({"summary": "another"})})
    assert d.outcome == "ask" and "answered 500 (3 tries)" in d.reason and len(fake.requests) == 6
    bad = token_file.parent / "wrong.token"
    bad.write_text("sk-wrong-0000000000000000")
    bad.chmod(0o600)
    d = ask(valve(api, fake.url, bad), tmp_path, {**ENV, "ORCHESTRA_FIELDS": json.dumps({"summary": "third"})})
    assert d.outcome == "ask" and d.reason == "the model refused the token (401)" and len(fake.requests) == 7


def test_the_model_down_is_the_fallback(token_file, tmp_path):
    d = ask(valve("openai", "http://127.0.0.1:9", token_file, retries=1), tmp_path)
    assert d.outcome == "ask" and "could not be reached" in d.reason and "2 tries" in d.reason


def test_an_unchanged_item_is_never_asked_twice(fake, token_file, tmp_path):
    v = valve("anthropic", fake.url, token_file)
    fake.script = [{"label": "ignore", "score": 0.95, "reason": "a duplicate"}]
    first, again = ask(v, tmp_path), ask(v, tmp_path)
    assert first.outcome == again.outcome == "ignore" and again.cached and not first.cached
    assert len(fake.requests) == 1
    changed = {**ENV, "ORCHESTRA_FIELDS": json.dumps({"summary": "a new summary", "priority": "P1"})}
    ask(v, tmp_path, changed)
    assert len(fake.requests) == 2
    other_field = {**ENV, "ORCHESTRA_FIELDS": json.dumps({"summary": "a new summary", "priority": "P1", "secret_note": "y"})}
    assert ask(v, tmp_path, other_field).cached            # a field not declared changes nothing
    assert len(fake.requests) == 2


def test_the_daily_cap_counts_every_attempt_and_then_falls_back_without_a_call(fake, token_file, tmp_path):
    v = valve("openai", fake.url, token_file, max_calls_per_day=3)
    fake.script = [{"status": 503}, {"label": "page", "score": 0.9, "reason": "r"}]
    assert ask(v, tmp_path).outcome == "page"                       # two attempts
    fake.script = [{"status": 503}, {"status": 503}, {"status": 503}]
    d = ask(v, tmp_path, {**ENV, "ORCHESTRA_FIELDS": json.dumps({"summary": "b"})})
    assert d.outcome == "ask" and "daily cap" in d.reason and len(fake.requests) == 3
    d = ask(v, tmp_path, {**ENV, "ORCHESTRA_FIELDS": json.dumps({"summary": "c"})})
    assert d.outcome == "ask" and d.reason == "the daily cap of 3 calls is reached" and len(fake.requests) == 3
    assert ask(v, tmp_path).cached                                 # the cache answers over the cap


def test_a_token_file_others_can_read_is_refused(fake, token_file, tmp_path):
    token_file.chmod(0o644)
    d = ask(valve("openai", fake.url, token_file), tmp_path)
    assert d.outcome == "ask" and "chmod 600" in d.reason and fake.requests == []


def test_only_the_declared_inputs_go_in_and_facts_are_bounded(fake, token_file, tmp_path):
    v = valve("classify", fake.url, token_file, inputs={
        "fields": ["summary"], "max_chars": 100,
        "facts": {"types": ["jira.issue.commented"], "data": ["body"], "last": 2}})
    log = [{"type": "jira.issue.commented", "time": f"t{i}", "source": "person.jira.x", "data": {"body": f"c{i}" * 80,
                                                                                               "author": "x"}}
           for i in range(4)] + [{"type": "jira.issue.transitioned", "time": "t9", "data": {"body": "no"}}]
    got = gather(v, ENV, log)
    assert got["fields"] == {"summary": "the login page is down for every customer"}
    assert [f["time"] for f in got["facts"]] == ["t2", "t3"] and set(got["facts"][0]["data"]) == {"body"}
    assert len(got["facts"][0]["data"]["body"]) == 101
    assert set(got) == {"workflow", "step", "fields", "facts"}
    assert fingerprint(v, got) == fingerprint(v, gather(v, ENV, log))


def test_the_token_never_reaches_the_log_the_output_or_the_cache(fake, token_file, tmp_path, monkeypatch):
    """The program as the host runs it: its stdout (the step's summary and its result line) and
    every file it keeps hold no token, whatever the model answers."""
    from conftest import agents_project
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    project = agents_project(repo)
    decl = tmp_path / "valve.json"
    outs = []
    for api, script in (("openai", [{"label": "page", "score": 0.9, "reason": f"echo {TOKEN}"}]), ("anthropic", [{"status": 401}]),
                        ("classify", [{"status": 503}] * 3)):
        fake.script = list(script)
        decl.write_text(json.dumps({"name": f"v_{api}", "question": "Is it urgent?", "labels": ["page", "queue"],
                                    "inputs": {"fields": ["summary"]}, "api": api, "base_url": fake.url, "model": "m",
                                    "token_file": str(token_file), "max_calls_per_day": 9, "backoff_s": 0.01}))
        env = {**{k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "ORCHESTRATOR_HOME")}, **ENV,
               "ORCHESTRA_REPO": str(repo)}
        p = subprocess.run([sys.executable, "-I", "-m", "orchestra_agents.valve", "--declaration", str(decl)],
                           check=False, env=env, capture_output=True, text=True, timeout=60, cwd=repo)
        outs.append(p.stdout + p.stderr)
        result = json.loads(p.stdout.strip().splitlines()[-1])
        fact = result["facts"]["set"][f"valve_v_{api}"]
        assert p.returncode == {"page": 10, "queue": 11, "ask": 12}[fact["outcome"]], p.stdout + p.stderr
    base = project.orchestra.state_dir / "agents" / "valves"
    kept = "".join(f.read_text() for f in base.rglob("*") if f.is_file())
    assert sorted(f.name for f in base.rglob("calls.jsonl")) == ["calls.jsonl"] * 3
    assert TOKEN not in kept and all(TOKEN not in o for o in outs)
    assert "[the token]" in outs[0]                          # the model's echo of it, redacted on the item
    assert "the login page" not in kept                      # nor the inputs' text
    assert metrics.text(project.orchestra.state_dir).count("valve_decisions_total{") == 3

def test_the_environments_proxy_is_honoured(fake, token_file, tmp_path, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", fake.url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    fake.script = [{"label": "queue", "score": 0.9, "reason": "via the proxy"}]
    d = ask(valve("openai", "http://model.invalid", token_file), tmp_path)
    assert d.outcome == "queue" and fake.requests[0]["raw_path"] == "http://model.invalid/v1/chat/completions"


def test_a_declaration_says_every_problem():
    v, problems = parse({"name": "X", "question": "?", "labels": ["page"], "api": "grpc", "base_url": "ftp://x",
                         "token_file": "rel.token", "extra": 1})
    assert v is None and len(problems) >= 6
    v, problems = parse({"name": "ok_name", "question": "Is it urgent?", "labels": ["a", "b"], "api": "openai",
                         "base_url": "https://x", "model": "m", "token_file": "/t", "min_confidence": 2,
                         "inputs": {"facts": {"types": ["jira.issue.commented"]}}})
    assert v is None and any("min_confidence" in p for p in problems) and any("inputs.facts.data" in p for p in problems)
