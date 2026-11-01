"""The seam to the declared layer (intended_api.py) and the rules of Orchestra's layer the tools
rely on: keys and names as Orchestra resolves them, a request checked against the layer's
open-request row, an owner's mark that another author cannot overwrite, the deadline of one tool
call, the pages that draw it, and the host's start (design_fixtures)."""

import http.server
import threading
import time
from types import SimpleNamespace

import pytest
from design_fixtures import WHY, call, layer, model_with, request, say

from orchestra_agents import design, design_confirm, design_passes, intended_api, web
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

FCI = "machine:feature_count_improver"
MID = "feature_count_improver"
OWNER = {"author": "owner", "source": "supervisor", "quote": "the owner's words here", "reason": WHY}
MINE = {"author": "supervisor", "source": "supervisor", "reason": WHY}


def improver(project, store, **over):
    say(store, "Create a new Universal Machine called Feature Count Improver.", 3)
    call(project, store, "declare_machine", name="Feature Count Improver", kind="universal", mode="continuous",
         description="Improves how many features get delivered per day", receives=["gate.landed"],
         emits=["work.improvement_proposed"], quote="Create a new Universal Machine called Feature Count Improver.",
         reason=WHY, **over)


def level_model(project):
    """A model whose stock s3 no series measures: the level its counters imply, tickets_open."""
    from orchestra.sd.model import Model, Stock
    from orchestra.sd.store import ModelStore
    m = Model("m", "m", stocks=[Stock("s3", "tickets open", series=None, evidence={"level": {"name": "tickets_open", "labels": {}}}),
                                Stock("s4", "hidden", series=None)])
    return ModelStore.for_state(project.orchestra.state_dir).save(m, "for the test")


def test_a_stock_no_series_measures_is_keyed_by_the_level_it_implies_and_published_by_its_id(project, store):
    level_model(project)
    keys = intended_api.Keys(intended_api.current_model(project))
    assert keys.stocks == {"tickets_open": "s3", "stock:s4": "s4"}
    lay = intended_api.current(project)
    assert intended_api.resolve("tickets_open", lay, keys) == ("s3", "tickets_open", None)
    assert intended_api.resolve("s3", lay, keys)[2] == intended_api.ABSENT                   # never a discovered id
    say(store, "I'm going to create a Features stock.", 1)
    call(project, store, "declare_stock", name="Features", quote="create a Features stock", reason=WHY)
    say(store, "Features is the tickets open.", 2)
    call(project, store, "link", declared="features", discovered="tickets_open", quote="Features is the tickets open.", reason=WHY)
    improver(project, store)
    call(project, store, "declare_expected_effect", machine=MID, horizon="7d", reason=WHY,
         expect=[{"kind": "above", "stock": "features", "value": 5}])
    say(store, "Confirm it.", 6)
    out = call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    made = [e for e in store.events.read(0, 500)[0] if e["type"] == "orchestra.prediction.made"]
    assert out["prediction"] and made[-1]["expect"] == [{"stock": "s3", "above": 5.0}]
    from orchestra_agents.design_passes import _absent
    assert all("tickets_open" in v for v in _absent(project))                     # never flagged absent for its key


def test_a_declared_stock_whose_option_records_waits_for_a_link_and_says_so(project, store):
    model_with(project, {"s7": ("other_series", {})})
    say(store, "I'm going to create a Features stock.", 1)
    call(project, store, "declare_stock", name="Features", quote="create a Features stock", reason=WHY)
    call(project, store, "propose_measurements", element="features", reason=WHY, options=[
        {"label": "acceptance tests", "kind": "script", "how": "a report", "cost": "a file"}])
    call(project, store, "add_instrument", element="features", option="a", name="features.acceptance",
         script="echo 'acceptance_tests_passing 41'\n", reason=WHY)
    say(store, "Keep the acceptance tests one.", 2)
    call(project, store, "keep_instrument", name="features.acceptance", quote="Keep the acceptance tests one.", reason=WHY)
    lay = intended_api.current(project)
    keys = intended_api.Keys(intended_api.current_model(project))
    assert intended_api.resolve("features", lay, keys)[2] == intended_api.WAITS_LINK
    improver(project, store)
    out = call(project, store, "declare_expected_effect", machine=MID, horizon="7d", reason=WHY,
               expect=[{"kind": "above", "stock": "features", "value": 5}])
    assert any("Features is not linked to a discovered stock yet: published once you say which one it is (link)" in line
               and "features.acceptance records it now" in line for line in out["said"])
    assert not any("discovery finds it" in line for line in out["said"])


def test_a_target_on_a_bare_flow_key_is_a_flow_target(project, store):
    say(store, "Land five a day.", 1)
    call(project, store, "set_target", element="tasks_open -> tasks_done by tasks_done_total", compare="above", value=5,
         over="1d", quote="Land five a day.", reason=WHY)
    t = layer(project)["targets"]["tasks_open -> tasks_done by tasks_done_total"]
    assert t["part"] == "flow"


def test_another_authors_mark_never_replaces_the_owners_and_a_key_is_expected_or_ignored(project):
    lay = intended_api.layer(project)
    lay.apply([{"op": "expect", "part": "stock", "key": "tickets_open"}], OWNER)
    lay.apply([{"op": "expect", "part": "stock", "key": "tickets_open"}], MINE)            # a no-op
    marks = lay.read()["layer"]["marks"]
    assert marks["expected"]["tickets_open"]["provenance"]["author"] == "owner"
    with pytest.raises(StoreError, match="ask the owner"):
        lay.apply([{"op": "ignore", "part": "stock", "key": "tickets_open"}], MINE)
    lay.apply([{"op": "ignore", "part": "stock", "key": "tickets_open"}], OWNER)
    marks = lay.read()["layer"]["marks"]
    assert "tickets_open" in marks["ignored"] and "tickets_open" not in marks["expected"]


def test_an_owner_described_event_type_is_changed_only_with_the_owner(project):
    lay = intended_api.layer(project)
    lay.apply([{"op": "declare_event", "type": "work.idea", "description": "an idea"}], OWNER)
    with pytest.raises(StoreError, match="ask the owner"):
        lay.apply([{"op": "declare_event", "type": "work.idea", "description": "something else"}], MINE)


def test_a_target_records_who_set_it(project):
    lay = intended_api.layer(project)
    lay.apply([{"op": "declare_stock", "name": "Features"}], MINE)
    lay.apply([{"op": "set_target", "part": "stock", "id": "features", "target": "above", "value": 5, "over_s": 86_400}], OWNER)
    assert lay.read()["layer"]["stocks"]["features"]["target_by"]["provenance"]["author"] == "owner"
    with pytest.raises(StoreError, match="ask the owner"):
        lay.apply([{"op": "set_target", "part": "stock", "id": "features", "target": "below", "value": 1, "over_s": 3600}],
                  MINE)


def test_a_stock_a_pending_change_names_is_not_retracted(project, store):
    improver(project, store)
    say(store, "Confirm it.", 6)
    call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)
    lay = intended_api.layer(project)
    lay.apply([{"op": "declare_stock", "name": "Features"}], OWNER)
    lay.apply([{"op": "set_expected_effect", "machine": MID, "edits": [{"stock": "features", "above": 5}], "horizon_s": 86_400}],
              MINE)
    assert lay.read()["layer"]["machines"][MID]["pending"]
    with pytest.raises(StoreError, match="pending change"):
        lay.apply([{"op": "retract", "part": "stock", "id": "features"}], OWNER)


def test_a_call_citing_a_request_that_is_not_open_is_refused_and_a_settled_one_closes(project, store):
    improver(project, store)
    lay = intended_api.layer(project)
    with pytest.raises(StoreError, match="not one of the owner's unanswered requests"):
        lay.apply([{"op": "confirm", "part": "machine", "id": MID}], {**MINE, "request": "made-up"})
    rid = request(store, "confirm", FCI)
    assert rid in lay.open_requests() and lay.open_requests()[rid]["control"] == "confirm"
    assert any(i.get("event") == rid for i in lay.design()["open"] if i["what"] == "unanswered request")
    say(store, "Confirm it.", 6)
    call(project, store, "confirm", element=FCI, quote="Confirm it.", reason=WHY)          # confirmed in the chat instead
    assert rid not in lay.open_requests()                                                   # it can no longer be acted on


def test_a_request_is_checked_against_the_layers_row_not_the_bus(project, store, monkeypatch):
    improver(project, store)
    rid = request(store, "confirm", FCI)
    monkeypatch.setattr(design, "recent_events", lambda *a, **k: [])                    # far out of any window of the bus
    say(store, "", 6)
    out = call(project, store, "confirm", element=FCI, request=rid, reason=WHY)
    assert out["ok"] and layer(project)["machines"][MID]["state"] == "declared"


def test_the_host_wakes_the_supervisor_at_start_for_each_open_request(project, store):
    from orchestra_agents.host import AgentHost
    improver(project, store)
    rid = request(store, "confirm", FCI)
    store.drain_wakeups("supervisor")
    host = AgentHost(project, store, store.events)
    assert host.wake_open_requests() == 1
    woken = store.drain_wakeups("supervisor")
    assert len(woken) == 1 and f"request={rid}" in woken[0]["reason"] and f"confirm with element {FCI}" in woken[0]["reason"]


class _Slow(http.server.BaseHTTPRequestHandler):
    delay, gets = 1.0, []

    def do_GET(self):
        _Slow.gets.append(self.path)
        time.sleep(_Slow.delay)
        body = b'{"version": 0, "open": [], "elements": []}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def slow(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("ORCHESTRA_EVENTS_ENDPOINT", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("ORCHESTRA_EVENTS_TOKEN", "t")
    monkeypatch.setattr(intended_api, "_last_timeout", [0.0])
    _Slow.gets = []
    try:
        yield _Slow
    finally:
        srv.shutdown()
        srv.server_close()


def test_after_one_endpoint_timeout_the_rest_of_the_call_fails_at_once(project, slow, monkeypatch):
    monkeypatch.setattr(intended_api, "TIMEOUT_S", 0.2)
    core = intended_api.Core(project)
    with intended_api.one_call():
        with pytest.raises(StoreError, match="did not answer"):
            core.design()
        began = time.time()
        with pytest.raises(StoreError, match="timed out earlier in this call"):
            core.read()
        assert time.time() - began < 0.1 and len(slow.gets) == 1
    assert intended_api.backing_off()


def test_the_summary_is_read_once_per_call_until_a_write(project, slow):
    slow.delay = 0.0
    core = intended_api.Core(project)
    with intended_api.one_call():
        core.design()
        core.open_requests()
        assert len(slow.gets) == 1
    with intended_api.one_call():
        core.design()
    assert len(slow.gets) == 2


def test_the_host_skips_its_design_tick_while_backing_off(project, store, monkeypatch):
    from orchestra_agents import design_instruments
    from orchestra_agents.host import AgentHost
    host = AgentHost(project, store, store.events)
    ran = []
    monkeypatch.setattr(design_instruments, "expire", lambda *a, **k: ran.append(1) or [])
    monkeypatch.setattr(intended_api, "_last_timeout", [time.time()])
    host._drafts_at = 0.0
    host._expire_drafts()
    assert not ran
    monkeypatch.setattr(intended_api, "_last_timeout", [time.time() - intended_api.BACKOFF_S - 1])
    host._expire_drafts()
    assert ran


def test_a_page_line_names_the_page_that_draws_the_part(project, monkeypatch):
    monkeypatch.setattr(intended_api, "pages", lambda p: {"Machines"})
    assert design.page_line(project, "the Machines page draws it dashed") == "the Machines page draws it dashed"
    assert design.page_line(project, "the Dynamics page shows it") is None


def test_a_scheduled_agent_is_declared_scheduled():
    a = SimpleNamespace(resident=False, at="00:00", every=None)
    assert web.mode_of(a) == "scheduled" and web.mode_of(SimpleNamespace(resident=False, at=None, every="1h")) == "scheduled"
    assert web.mode_of(SimpleNamespace(resident=True, at=None, every=None)) == "continuous"
    assert web.mode_of(SimpleNamespace(resident=False, at=None, every=None)) == "ad hoc"


def test_a_type_a_known_machine_publishes_the_family_of_names_it_as_the_publisher():
    lay = {"machines": {"improver": {"receives": ["gate.rejected"], "emits": [], "expected_effect": {"edits": [{}]}}}, "events": {}}
    events = [{"type": "gate.started", "source": "merge_gate"}, {"type": "gate.landed", "source": "merge_gate"}]
    found, _ = design_passes.check(lay, events, {}, set(), set())
    f = next(x for x in found if x["key"] == "unemitted:gate.rejected")
    assert "not heard on the bus yet" in f["text"] and "merge_gate publishes gate.*" in f["text"]
    assert "merge_gate" in f["propose"] and "declare the machine" not in f["propose"]
    found, _ = design_passes.check(lay, [], {}, set(), set())
    f = next(x for x in found if x["key"] == "unemitted:gate.rejected")
    assert "nothing emits" not in f["text"] and "declare the machine" in f["propose"]


def test_the_wake_up_names_a_change_when_the_request_does():
    ev = {"type": design.REQUESTED, "source": "owner", "id": "abc", "control": "confirm", "change": "c4.0",
          "subject": FCI, "from": "Machines page"}
    said = design_confirm.wake_reason(ev)
    assert "change c4.0" in said and "request abc" in said
