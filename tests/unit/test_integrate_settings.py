"""The kits' settings the Supervisor writes into a collector's poller.toml (integrate_kits:
check_settings, set_keys): the promql kit's ``queries`` (a list of tables, ``[[settings.queries]]``)
and the Slack kit's per-channel ``cadence`` (a table), with the same checks as every other setting,
and read back by the bridges' own reader."""

import tomllib

import pytest

from orchestra_agents import integrate_kits as kits
from orchestra_agents.store import StoreError

QUERIES = [{"name": "payments_requests", "query": 'sum by (service) (http_requests_total{namespace="payments"})',
            "help": "Requests served, by service."},
           {"name": "payments_errors", "query": 'sum(http_request_errors_total{namespace="payments"})'}]


def scaffold(tmp_path, kit: str, name: str):
    from orchestra_bridges.kits import scaffold as s
    return s.scaffold(kit, name, tmp_path)


def settings_of(target) -> dict:
    return tomllib.loads(kits.settings_file(target).read_text())["settings"]


def test_queries_replace_the_templates_and_the_bridges_read_them(tmp_path):
    from orchestra_bridges.kits.machinefile import read
    target = scaffold(tmp_path, "promql", "chrono")
    kits.fill(target, kits.check_settings({"queries": QUERIES, "selectors": ['{namespace="payments"}'],
                                           "aliases": {"k8s_cluster": "cluster"}}), None)
    text = kits.settings_file(target).read_text()
    assert text.count("[[settings.queries]]") == 2 and "http_requests" not in text.replace("http_requests_total", "")
    assert "# The allowlist `schema` looks within" in text                    # comments kept
    got = read(kits.settings_file(target))
    assert got.extra["queries"] == QUERIES and got.extra["aliases"] == {"k8s_cluster": "cluster"}
    assert got.extra["selectors"] == ['{namespace="payments"}']
    kits.fill(target, kits.check_settings({"queries": QUERIES[1:], "max_series": 100}), None)   # again: replaced, not added
    s = settings_of(target)
    assert s["queries"] == QUERIES[1:] and s["max_series"] == 100


def test_a_channels_cadence_is_an_inline_table(tmp_path):
    from orchestra_bridges.kits.machinefile import read
    target = scaffold(tmp_path, "slack", "slack_poller")
    kits.fill(target, kits.check_settings({"channels": ["C0PAY", "C0INC"], "cadence": {"C0INC": "30m"},
                                           "max_backoff": "2h"}), None)
    assert 'cadence = { C0INC = "30m" }' in kits.settings_file(target).read_text()
    assert read(kits.settings_file(target)).extra["cadence"] == {"C0INC": "30m"}
    kits.fill(target, kits.check_settings({"cadence": {}}), None)
    assert settings_of(target)["cadence"] == {}


def test_the_tools_rows_are_the_kits_tables():
    got = kits.check_settings({"cadence": [{"channel": "C0INC", "every": "30m"}], "aliases": [{"label": "k8s_cluster", "as": "cluster"}]})
    assert got == {"cadence": {"C0INC": "30m"}, "aliases": {"k8s_cluster": "cluster"}}
    with pytest.raises(StoreError, match="a list of {channel, every}"):
        kits.check_settings({"cadence": [{"channel": "C0INC"}]})


@pytest.mark.parametrize("settings, match", [
    ({"queries": [{"name": "x", "query": "up", "token": "abc"}]}, "credential is never a setting"),
    ({"queries": [{"name": "x", "query": "up", "token_file": "/tmp/t"}]}, "credential is never a setting"),
    ({"queries": [{"name": "bad name", "query": "up"}]}, "series' name"),
    ({"queries": [{"name": "x"}]}, "has no PromQL"),
    ({"queries": [{"name": "x", "query": "up", "step": "1m"}]}, "takes name, query and help"),
    ({"queries": [{"name": "x", "query": "up"}, {"name": "x", "query": "down"}]}, "same name"),
    ({"queries": [{"name": "x", "query": "up{x=\"Bearer abcdefghijklmnop\"}"}]}, "looks like it holds a secret"),
    ({"queries": [{"name": "x", "query": {"nested": "no"}}]}, "is a string, a number"),
    ({"selectors": ['{__name__=~".+"}']}, "would match the whole store"),
    ({"selectors": ["{}"]}, "would match the whole store"),
    ({"cadence": {"general": "30m"}}, "not a channel's id"),
    ({"cadence": {"C0123": "soon"}}, "is a duration"),
    ({"cadence": {"C0123": "xoxb-1234567890-abcdefghij"}}, "looks like it holds a secret"),
    ({"cadence": {"C0123": ["30m"]}}, "is a string, a number"),
    ({"max_backoff": "never"}, "is a duration"),
    ({"aliases": {"token": "x"}}, "credential is never a setting"),
])
def test_the_new_shapes_have_the_same_checks(settings, match):
    with pytest.raises(StoreError, match=match):
        kits.check_settings(settings)


def test_set_keys_puts_blocks_where_the_old_ones_were():
    text = ('name = "c"\n\n[settings]\nbase_url = "https://x"\nqueries = []\n\n[[settings.queries]]  # guess\n'
            'name = "a"\nquery = "up"\n\n[[publishes]]\ntype = "x.y"\n')
    out = kits.set_keys(text, "settings", {"queries": [{"name": "b", "query": "down"}], "every": "5m"})
    got = tomllib.loads(out)
    assert got["settings"]["queries"] == [{"name": "b", "query": "down"}] and got["settings"]["every"] == "5m"
    assert got["publishes"] == [{"type": "x.y"}] and out.index("[[settings.queries]]") < out.index("[[publishes]]")
    assert "# guess" not in out
