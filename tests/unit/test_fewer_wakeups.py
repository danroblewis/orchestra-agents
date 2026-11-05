"""Fewer Supervisor wake-ups by default (the owner, 2026-11-04): review rounds at most every 2h,
notices gathered hourly, explore rounds every 6h, 12 turns of its own a day; each the owner's to change."""

from types import SimpleNamespace

from orchestra_agents.config import Config
from orchestra_agents.review import due_every


def test_the_defaults_wake_it_far_less_often():
    c = Config()
    assert (c.review_every, c.attention_every, c.explore_every, c.review_turns_per_day) == (7200.0, 3600.0, 21600.0, 12)


def test_the_review_round_never_runs_more_often_than_the_owners_floor():
    spec = SimpleNamespace(review_every="30m")
    assert due_every(spec, SimpleNamespace(review_every=7200.0)) == 7200.0
    assert due_every(SimpleNamespace(review_every="4h"), SimpleNamespace(review_every=7200.0)) == 4 * 3600
    assert due_every(spec, SimpleNamespace(review_every=0.0)) == 1800.0
