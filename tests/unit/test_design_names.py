"""The owner names a discovered part in words (``name_part``, design_names.py): found by the name
the page shows and its tags, declared with the owner's name and linked (a flow by its ends), quoted,
answered with what the page shows; two matches are one question; a rename re-declares, "drop the
name" retracts. On Orchestra's declared layer through its endpoint, served in process
(design_fixtures), and checked with Orchestra's own ``declared_names``, as the Dynamics page reads it."""

import pytest
from design_fixtures import WHY, call, changes, layer, say

from orchestra_agents.harness.tools_design import DESIGN_TOOLS
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

ASK = "call the stock between write and sail_check 'tickets in flight'"


def model(project):
    """A discovered model with generated place names and tags: two stocks read "held at write",
    told apart by their workflow tag."""
    from orchestra.sd.model import Flow, Model, SeriesRef, Stock
    from orchestra.sd.store import ModelStore
    pile = "workflow_pile_items"
    stocks = [Stock("w1", "held at write", series=SeriesRef(pile, {"place": "write", "workflow": "delivery_sail"}),
                    evidence={"named": "places", "tags": ["workflow=delivery_sail"]}),
              Stock("w2", "held at write", series=SeriesRef(pile, {"place": "write", "workflow": "hotfix"}),
                    evidence={"named": "places", "tags": ["workflow=hotfix"]}),
              Stock("btw", "between write and sail_check", series=SeriesRef("between_items", {}), evidence={"named": "places"}),
              Stock("done", "done", series=SeriesRef("done_items", {}))]
    flows = [Flow("f_in", "outside → write", None, "w1", counter=SeriesRef("entered_total", {})),
             Flow("f_ws", "write → sail_check", "w1", "btw", counter=SeriesRef("moved_total", {})),
             Flow("f_sd", "sail_check → done", "btw", "done", counter=SeriesRef("done_total", {}))]
    m = Model("m", "m", stocks=stocks, flows=flows)
    ModelStore.for_state(project.orchestra.state_dir).save(m, "for the test")
    return m


def names(project) -> dict:
    """What the Dynamics page shows as each part's declared name: Orchestra's own reading."""
    from orchestra.sd.intended.display import declared_names
    from orchestra.sd.intended.keys import Keys
    from orchestra.sd.store import ModelStore
    lent = declared_names(layer(project), Keys(ModelStore.for_state(project.orchestra.state_dir).load()))
    return {pid: n["name"] for group in lent.values() for pid, n in group.items()}


def name(project, store, message, quote=None, **kw):
    say(store, message)
    return call(project, store, "name_part", quote=quote or message, reason=WHY, **kw)


def test_the_stock_is_declared_with_the_owners_name_linked_to_its_key_and_quoted(project, store):
    model(project)
    out = name(project, store, ASK, shown="between write and sail_check", part="stock", name="tickets in flight")
    assert out["said"][0] == "tickets in flight, over 'between write and sail_check', named by you"
    sent = changes(project)
    assert [c["op"] for c in sent] == ["declare_stock", "link"]
    assert sent[0]["name"] == "tickets in flight" and sent[1]["found_as"] == "between_items" and sent[1]["id"] == sent[0]["id"]
    el = layer(project)["stocks"][out["id"]]
    assert el["state"] == "declared" and el["found_as"] == "between_items"
    assert el["provenance"]["author"] == "owner" and el["provenance"]["source"] == "supervisor"
    assert el["provenance"]["quote"] == ASK
    assert names(project) == {"btw": "tickets in flight"}


def test_a_part_is_found_by_its_tags_and_two_that_read_alike_are_one_question(project, store):
    model(project)
    msg = "call the stock held at write 'hotfix queue'"
    out = name(project, store, msg, shown="held at write", name="hotfix queue")
    assert out["changes"] == [] and "asked" in out
    assert out["asked"] == ("Which one do you mean: the stock held at write · workflow=delivery_sail, or the stock held at "
                            "write · workflow=hotfix?")
    assert names(project) == {} and changes(project) == []                 # nothing declared on a guess
    out = name(project, store, msg + ", the hotfix one", quote=msg, shown="held at write · workflow=hotfix", name="hotfix queue")
    assert out["said"][0] == "hotfix queue, over 'held at write · workflow=hotfix', named by you"
    assert names(project) == {"w2": "hotfix queue"}
    out = name(project, store, "call the delivery one 'sail queue' please", shown="held at write", tags=["workflow=delivery_sail"],
               name="sail queue")
    assert names(project) == {"w2": "hotfix queue", "w1": "sail queue"}


def test_a_flow_is_declared_between_its_stocks_keys_with_the_name(project, store):
    m = model(project)
    msg = "name the flow write → sail_check 'handed to the check'"
    out = name(project, store, msg, shown="write -> sail_check", name="handed to the check")
    op = changes(project)[0]
    assert op["op"] == "declare_flow" and op["name"] == "handed to the check"
    from orchestra.sd.intended.keys import Keys
    keys = Keys(m)
    assert (op["from"], op["to"]) == (keys.stock_of_id["w1"], keys.stock_of_id["btw"]) and op["id"] == out["id"]
    assert names(project) == {"f_ws": "handed to the check"}
    with pytest.raises(StoreError, match="outside"):
        name(project, store, "name the flow outside → write 'arrivals now'", shown="outside → write", name="arrivals now")


def test_renaming_redeclares_the_same_element_and_drop_the_name_retracts_it(project, store):
    model(project)
    first = name(project, store, ASK, shown="between write and sail_check", name="tickets in flight")
    out = name(project, store, "rename tickets in flight to 'work at the check'", shown="tickets in flight",
               name="work at the check")
    assert out["id"] == first["id"] and out["said"][0].startswith("work at the check, over 'between write and sail_check'")
    assert changes(project)[-1] == {**changes(project)[-1], "op": "declare_stock", "id": first["id"], "name": "work at the check"}
    assert list(layer(project)["stocks"]) == [first["id"]] and names(project) == {"btw": "work at the check"}
    out = name(project, store, "drop the name on between write and sail_check", shown="between write and sail_check", drop=True)
    assert changes(project)[-1]["op"] == "retract" and layer(project)["stocks"] == {} and names(project) == {}
    assert "generated name again" in out["said"][0]


def test_never_a_name_the_owner_did_not_give_nor_without_their_words(project, store):
    model(project)
    with pytest.raises(StoreError, match="not in the owner's words"):
        name(project, store, ASK, shown="between write and sail_check", name="in-flight work")
    say(store, ASK)
    with pytest.raises(StoreError, match="missing quote|quote their words"):
        call(project, store, "name_part", shown="between write and sail_check", name="tickets in flight", reason=WHY)
    with pytest.raises(StoreError, match="no stock or flow is shown as 'between write and ci'"):
        name(project, store, "call the stock between write and ci 'tickets in flight'", shown="between write and ci",
             name="tickets in flight")
    assert changes(project) == []


def test_drop_refuses_a_declaration_that_is_more_than_a_name(project, store):
    model(project)
    say(store, "I'm going to create a Features stock.")
    call(project, store, "declare_stock", name="Features", quote="create a Features stock", reason=WHY)
    call(project, store, "link", declared="features", discovered="done_items", quote="create a Features stock", reason=WHY)
    assert names(project) == {"done": "Features"}
    with pytest.raises(StoreError, match="more than a name"):
        name(project, store, "drop the name on done please", shown="Features", drop=True)
    assert "features" in layer(project)["stocks"]


def test_the_tool_is_listed_with_the_design_tools():
    tool = {t.name: t for t in DESIGN_TOOLS}["name_part"]
    assert set(tool.required) == {"quote", "reason"} and "never one you made up" in tool.description
    assert "before ever saying a stock is not in the model" in tool.description


def run3_model(project):
    """Bootstrap run 3's v1: the ticket workflow's in-progress pile, shown as "in in_progress", beside
    its done pile (both tagged by their workflow)."""
    from orchestra.sd.model import Model, SeriesRef, Stock
    from orchestra.sd.store import ModelStore
    pile = "workflow_step_items"
    stocks = [Stock("wip", "in in_progress", series=SeriesRef(pile, {"step": "in_progress", "workflow": "ticket_delivery"}),
                    evidence={"named": "places", "tags": ["workflow=ticket_delivery"]}),
              Stock("fin", "in done", series=SeriesRef(pile, {"step": "done", "workflow": "ticket_delivery"}),
                    evidence={"named": "places", "tags": ["workflow=ticket_delivery"]})]
    ModelStore.for_state(project.orchestra.state_dir).save(Model("m", "m", stocks=stocks, flows=[]), "for the test")


def test_the_owners_words_alone_find_the_discovered_stock_and_name_it(project, store):
    """Run 3, step 10: asked for the stock that matters, the Supervisor said it was not in the model
    while v1 held it. The owner's words are matched to the shown name and tags; it is declared,
    linked and "named by you"."""
    run3_model(project)
    msg = "The one that matters most is tickets in flight"
    out = name(project, store, msg, name="tickets in flight")
    assert out["said"][0] == "tickets in flight, over 'in in_progress · workflow=ticket_delivery', named by you"
    assert [c["op"] for c in changes(project)] == ["declare_stock", "link"]
    assert names(project) == {"wip": "tickets in flight"}


def test_a_stock_declared_before_is_linked_not_declared_twice(project, store):
    run3_model(project)
    say(store, "add a stock Tickets in flight")
    call(project, store, "declare_stock", name="Tickets in flight", quote="add a stock Tickets in flight", reason=WHY)
    out = name(project, store, "call it tickets in flight", name="tickets in flight", quote="call it tickets in flight")
    assert "named by you" in out["said"][0]
    assert [c["op"] for c in changes(project)] == ["declare_stock", "link"]
    assert names(project) == {"wip": "Tickets in flight"}


def test_no_match_says_so_with_the_discovered_stocks_listed(project, store):
    run3_model(project)
    with pytest.raises(StoreError, match=r"the model's stocks now: in in_progress · workflow=ticket_delivery; in done"):
        name(project, store, "call it release health", name="release health")
    assert changes(project) == []
