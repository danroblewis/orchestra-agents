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


def test_a_level_with_a_staging_copy_is_kept_to_production():
    from orchestra_agents.balance_tools import _production
    sets = [{"env": "prod"}, {"env": "staging"}]
    assert _production("queue_depth", sets) == 'queue_depth{env="prod"}'
    assert _production('queue_depth{env="staging"}', sets) == 'queue_depth{env="staging"}'   # named: kept as asked
    assert _production("queue_depth", [{"env": "prod"}]) == "queue_depth"


def test_the_store_listing_names_its_levels_with_what_each_holds():
    from orchestra_agents.source_tools import _levels
    fams = {"rabbitmq_queue_messages": 2, "rabbitmq_queue_messages_published_total": 2, "envoy_http_downstream_cx_active": 1,
            "process_resident_memory_bytes": 1, "http_request_duration_seconds_bucket": 9, "fulfilment_email_queueDepth": 1}
    values = {"rabbitmq_queue_messages": {"queue": {"a", "b"}}, "rabbitmq_queue_messages_published_total": {"queue": {"a", "b"}}}
    values = {n: values.get(n, {}) for n in fams}
    got = _levels(fams, values, lambda n: "counter" if n.endswith("_total") else "gauge")
    assert got == ["envoy_http_downstream_cx_active", "fulfilment_email_queueDepth", "rabbitmq_queue_messages by queue: a, b"]


def test_a_list_of_levels_is_read_as_source_schema_writes_it():
    from orchestra_agents.balance_tools import expand
    got = expand(["rabbitmq_queue_messages by queue: a, b.c (+2)", "envoy_http_downstream_cx_active", 'x{q="1"}'])
    assert got == ['rabbitmq_queue_messages{queue="a"}', 'rabbitmq_queue_messages{queue="b.c"}', "envoy_http_downstream_cx_active",
                   'x{q="1"}']
    assert expand("one_level") == ["one_level"]


def test_two_levels_that_are_one_quantity_read_twice_are_told_apart_from_two_that_rise_together():
    from orchestra_agents.balance_tools import _same_quantity
    a = [(t * 30.0, 1000.0 + 5 * t + (t % 3)) for t in range(60)]
    late = [(t * 30.0, 1000.0 + 5 * (t - 2) + (t % 3)) for t in range(60)]       # the same, a minute late
    other = [(t * 30.0, 4000.0 + 20 * t) for t in range(60)]                    # rises with it, another scale
    assert _same_quantity(a, late) and not _same_quantity(a, other)


def test_lateness_is_asked_of_one_metric_per_exporter():
    from orchestra_agents.source_tools import _probes
    assert _probes(["shop_a", "shop_b", "fulfilment_x", "kafka_y", "shop_c", "up", "z_1"]) == ["shop_a", "fulfilment_x", "kafka_y", "up"]
