"""Hundreds of people as machines (the owner, 2026-11-04: "We have a problem now where we have way too
many people names sometimes, and it's breaking various things"). 600 Jira people, relayed by the
bridges' poller as person.jira.<account> and each announced with a name, beside three programs: what
the Supervisor reads names the count in one line per system and stays bounded; it names people only
when asked by system and name; the host's minute sweep joins them once and then reads only what the
bus added; and the pages this plugin draws name no person."""

import json
import time

import pytest
from design_fixtures import call
from people_fixtures import PROGRAMS, crowd, display, person

from orchestra_agents import (
    design_integrate,
    integrate_heard,
    intended_api,
    machines_read,
    people,
    prompting,
)
from orchestra_agents.design import MACHINE
from orchestra_agents.model_text import machines_line

pytest_plugins = ["design_fixtures"]

BOUND = 6000          # the trimmed-answer mechanism's cap: a summary stays under it


def size(out) -> int:
    return len(json.dumps(out, default=str))


def declare_jira(project, store) -> list[str]:
    _, lines = design_integrate._declare(project, "jira", "jira_poller", "5m", ["jira.issue.updated"],
                                         {"author": MACHINE, "source": MACHINE, "reason": "the test"}, store)
    return lines


def test_list_machines_says_the_people_in_one_line_and_the_programs_one_by_one(project, store):
    crowd(store, 600)
    out = call(project, store, "list_machines")
    assert size(out) < BOUND
    assert sorted(m["id"] for m in out["machines"]) == sorted(PROGRAMS)
    line = next(x for x in out["summary"] if x.startswith("600 people"))
    assert "person.jira.<account>" in line and "heard today" in line and 'people="jira"' in line
    assert f"{display(0)} ({person(0)}), 21 events" in line                  # the busiest by name
    assert sum(x.count("person.jira.acct") for x in out["summary"]) == people.TOP
    assert out["people"][0]["people"] == 600 and out["people"][0]["group"] == "jira"


def test_people_are_named_when_asked_by_system_and_match(project, store):
    crowd(store, 600)
    out = call(project, store, "list_machines", people="jira")
    assert size(out) < BOUND and out["matched"] == 600 and len(out["machines"]) == people.LISTED
    assert out["summary"][0].startswith("600 people of jira; the 25 heard most")
    one = call(project, store, "list_machines", people="jira", match="number0042")
    assert one["matched"] == 1 and one["summary"][1].startswith(f"{display(42)} ({person(42)}): last heard")
    none = call(project, store, "list_machines", people="github")
    assert none["matched"] == 0 and none["groups"] == ["jira"]


def test_a_note_every_person_carries_is_said_once(project, store):
    crowd(store, 600, notes={"fake": "a recorded Jira, not the live one"})
    out = call(project, store, "list_machines")
    assert out["fakes"] == ["person.jira.<account> (600 people): a recorded Jira, not the live one"]
    assert size(out) < BOUND


def test_a_few_people_stay_listed_one_by_one(project, store):
    crowd(store, people.SUMMARISED)
    out = call(project, store, "list_machines")
    assert len(out["machines"]) == people.SUMMARISED + len(PROGRAMS) and "people" not in out


def test_machine_ages_counts_the_people_without_listing_them(project, store):
    crowd(store, 600)
    out = call(project, store, "machine_ages")
    assert size(out) < BOUND
    assert out["counts"]["other"] == 600 and out["people"][0]["people"] == 600
    assert not any(r["id"].startswith("person.") for r in out["machines"])


def test_read_design_counts_who_was_heard_publishing_a_type(project, store):
    crowd(store, 600)
    declare_jira(project, store)
    out = call(project, store, "read_design")
    assert size(out) < BOUND
    assert out["events"]["jira.issue.updated"]["heard_from"] == ["person.jira.<account>: 600 people"]


def test_the_digest_and_the_supervisor_context_stay_short_and_count_them(project, store):
    crowd(store, 600)
    out = call(project, store, "read_events", since="24h")
    assert size(out) < 2 * BOUND and len(out["digest"]) < BOUND
    evs, _ = store.events.read(0, 10_000)
    ctx = prompting.supervisor_context({"counts": {}, "absent": True}, evs)
    assert len(ctx) < 1500 and "people announced by a relay: 600 of person.jira.<account>" in ctx


def test_the_first_join_aliases_everyone_once_and_says_it_short(project, store):
    crowd(store, 600)
    lines = declare_jira(project, store)
    assert sum(len(x) for x in lines) < 1500 and "person.jira.<account>: 600 people" in lines[0]
    t = time.time()
    aliases = intended_api.current(project)["machines"]["jira"]["aliases"]
    assert time.time() - t < 0.5 and len(aliases) == 601                      # bot.jira and the 600


def test_the_minute_sweep_reads_only_what_the_bus_added_and_joins_only_the_new(project, store, monkeypatch):
    crowd(store, 600)
    declare_jira(project, store)
    applied, reads = [], []
    monkeypatch.setattr(integrate_heard, "apply", lambda project, ops, prov: applied.append(ops))
    monkeypatch.setattr(machines_read, "view", lambda store: pytest.fail("the sweep built the machines view"))
    bus = store.events.bus
    real = bus.read
    monkeypatch.setattr(bus, "read", lambda since=0, limit=500: reads.append(since) or real(since, limit))
    t = time.time()
    for _ in range(5):
        assert integrate_heard.sweep(project, store) == []
    assert time.time() - t < 0.5 and applied == [] and reads == []              # nothing new on the bus: nothing read
    store.events.publish("jira.issue.created", {"key": "SCRUM-900"}, source="person.jira.newcomer")
    assert integrate_heard.sweep(project, store) == ["machine jira heard as person.jira.newcomer"]
    assert [len(ops) for ops in applied] == [1] and reads == [bus.end() - 1]


def test_the_pages_this_plugin_draws_name_no_person(project, store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from orchestra_agents import web
    crowd(store, 600)
    app = FastAPI()
    app.include_router(web.router(project), prefix="/api/plugins/agents")
    client = TestClient(app)
    for path in ("/state", "/org", "/agents", "/supervisor/wakes", "/attention"):
        r = client.get("/api/plugins/agents" + path)
        assert r.status_code == 200 and "person.jira" not in r.text, path


def test_the_models_machines_line_says_a_systems_people_once():
    ms = [{"id": person(i), "flows": ["ISSUES_MOVED"] if i % 2 else ["ISSUES_OPENED"]} for i in range(600)]
    line = machines_line([*ms, {"id": "review_nudger", "flows": ["NUDGES"]}])
    assert line == "review_nudger: NUDGES; 600 people of person.jira.<account>: ISSUES_MOVED, ISSUES_OPENED"


def test_compact_counts_a_big_group_and_keeps_a_small_one():
    ids = ["bot.jira", *(person(i) for i in range(30)), "person.github.ana", "person.amara"]
    assert people.compact(ids) == ["bot.jira", "person.jira.<account>: 30 people", "person.github.ana", "person.amara"]
    assert people.group_of("person.amara") == people.MAPPED and people.group_of("worker/1") is None
    assert people.named([f"x{i}" for i in range(30)], 3) == "x0, x1, x2, and 27 more"


def test_the_hosts_minute_costs_little_with_600_people(project, store, make_host):
    crowd(store, 600, acts=10)
    declare_jira(project, store)
    host = make_host(store)
    host._drafts_at = 0
    host.tick()                                    # the first: the bus read once
    spent = []
    for _ in range(3):
        host._drafts_at = 0                        # the minute's design work runs each time
        t = time.time()
        host.tick()
        spent.append(time.time() - t)
    assert max(spent) < 0.3, spent
