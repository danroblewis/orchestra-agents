"""find_balance's own parts: how a level's words, labels and names are read, and what it declares."""

from orchestra_agents.balance_tools import (
    _copy_of,
    _held,
    _stock_name,
    _with,
    matchers,
    metric_of,
    tokens,
)


def test_words_are_read_from_snake_and_camel_case_without_plurals_or_stop_words():
    assert tokens("fulfilment_email_queueDepth") == {"fulfilment", "email", "queue", "depth"}
    assert tokens("rabbitmq_queue_messages_published_total") == {"rabbitmq", "queue", "message", "published"}
    assert tokens("email.notifications.dlq") == {"email", "notification", "dlq"}
    assert tokens("data-kafka-0") == {"kafka"}                     # data is a stop word, 0 a digit


def test_a_selector_says_its_metric_and_its_equality_matchers():
    sel = 'rabbitmq_queue_messages{queue="email.notifications", vhost=~"/.*"}'
    assert metric_of(sel) == "rabbitmq_queue_messages" and matchers(sel) == {"queue": "email.notifications"}
    assert metric_of('{__name__="x_total",a="b"}') == "x_total"
    assert _with("x_total", {"queue": "q"}) == 'x_total{queue="q"}'
    assert _with('x_total{a="b"}', {"queue": "q"}) == 'x_total{queue="q",a="b"}'


def test_a_stock_is_named_for_what_its_level_is_selected_by():
    assert _stock_name("", 'rabbitmq_queue_messages{queue="email.notifications"}') == "email_notifications"
    assert _stock_name("", "envoy_cluster_upstream_cx_active") == "envoy_cluster_upstream_cx_active"
    assert _stock_name("Email Queue", "x") == "email_queue"
    assert len(_stock_name("", "a_" * 40)) <= 40


def test_a_coarse_read_is_held_on_the_windows_steps():
    held = _held([(0.0, 0.0), (120.0, 8.0), (240.0, 20.0)], 0.0, 240.0, 30.0)
    assert [v for _, v in held] == [0, 0, 0, 0, 8, 8, 8, 8, 20]
    fine = [(0.0, 1.0), (30.0, 2.0)]
    assert _held(fine, 0.0, 30.0, 30.0) == fine


def test_a_gauge_that_is_the_level_again_is_a_copy():
    level = [(t * 30.0, float(v)) for t, v in enumerate([3, 5, 9, 12, 12, 15])]
    assert _copy_of([(t, v + 7) for t, v in level], level)
    assert not _copy_of([(t, v * 2) for t, v in level], level)
