"""The owner's subsystems in words (``declare_subsystem``, design_subsystems.py): a label's value
named from the model's partitions, parts gathered by the names the page shows (two that match are
one question; one already in another subsystem refused, saying which), one dropped; and the
Supervisor's proposal, which draws no box until the owner confirms it, said in the digest's one
line at most once a day. On Orchestra's declared layer through its endpoint, served in process
(design_fixtures), and checked with Orchestra's own ``with_declared``, as both maps read it."""

import pytest
from design_fixtures import WHY, call, changes, layer, say

from orchestra_agents import subsystem_offer
from orchestra_agents.harness.tools_design import DESIGN_TOOLS
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

NAME_IT = "call the pr_watch workflow 'release watch'"
GATHER = "put the stock between write and sail_check, the review → sail flow and bot.sail together as 'the sail line'"


def model(project):
    """Stocks named by their places, scoped by workflow (delivery, hotfix, pr_watch); one between
    write and sail_check with no workflow; a machine bot.sail."""
    from orchestra.sd.model import Flow, Machine, Model, SeriesRef, Stock
    from orchestra.sd.store import ModelStore
    pile = "workflow_pile_items"

    def placed(sid, title, place, wf):
        return Stock(sid, title, series=SeriesRef(pile, {"place": place, "workflow": wf}),
                     evidence={"named": "places", "tags": [f"workflow={wf}"]})
    stocks = [placed("w1", "held at write", "write", "delivery"), placed("w2", "held at write", "write", "hotfix"),
              placed("d1", "done", "done", "delivery"), placed("h1", "done", "done", "hotfix"),
              placed("rv", "held at review", "review", "pr_watch"), placed("sl", "held at sail", "sail", "pr_watch"),
              Stock("btw", "between write and sail_check", series=SeriesRef("between_items", {}), evidence={"named": "places"})]
    flows = [Flow("f_wd", "write → done", "w1", "d1", counter=SeriesRef("moved_total", {"workflow": "delivery"})),
             Flow("f_wh", "write → done", "w2", "h1", counter=SeriesRef("moved_total", {"workflow": "hotfix"})),
             Flow("f_rs", "review → sail", "rv", "sl", counter=SeriesRef("moved_total", {"workflow": "pr_watch"})),
             Flow("f_wb", "write → sail_check", "w1", "btw", counter=SeriesRef("checked_total", {}))]
    m = Model("m", "m", stocks=stocks, flows=flows, machines=[Machine("bot.sail", "bot.sail", "machine")])
    ModelStore.for_state(project.orchestra.state_dir).save(m, "for the test")
    return m


def drawn(project) -> dict:
    """The boxes the maps draw with a name: Orchestra's own reading (``with_declared``), group value -> name."""
    from orchestra.sd.intended.subsystems import with_declared
    from orchestra.sd.store import ModelStore
    from orchestra.sd.subsystems import subsystems
    m = ModelStore.for_state(project.orchestra.state_dir).load()
    out = with_declared(subsystems(m), layer(project), m)
    return {(p["label"], g["value"]): g["name"] for p in out["partitions"] for g in p["groups"] if g.get("name")}


def declare(project, store, message, quote=None, **kw):
    say(store, message)
    return call(project, store, "declare_subsystem", quote=quote or message, reason=WHY, **kw)


def test_a_labels_value_is_named_from_the_partitions_quoted_and_drawn_on_both_maps(project, store):
    model(project)
    out = declare(project, store, NAME_IT, value="pr_watch", name="release watch")
    assert out["said"][0] == "a box 'release watch' over pr_watch on both maps, named by you"
    assert changes(project)[-1] == {**changes(project)[-1], "op": "declare_subsystem", "label": "workflow", "value": "pr_watch",
                                    "name": "release watch"}
    el = layer(project)["subsystems"][out["id"]]
    assert el["state"] == "declared"
    assert el["provenance"]["author"] == "owner" and el["provenance"]["source"] == "supervisor"
    assert el["provenance"]["quote"] == NAME_IT
    assert drawn(project) == {("workflow", "pr_watch"): "release watch"}
    with pytest.raises(StoreError, match="no partition of the model has a group 'ci'.*workflow=delivery"):
        declare(project, store, "call the ci workflow 'build line'", value="ci", name="build line")
    with pytest.raises(StoreError, match="not in the owner's words"):
        declare(project, store, NAME_IT, value="pr_watch", name="the watcher")


def test_parts_are_gathered_by_the_names_the_page_shows_and_machines_by_id(project, store):
    from orchestra.sd.intended.keys import Keys
    keys = Keys(model(project))
    out = declare(project, store, GATHER, name="the sail line", machines=["bot.sail"],
                  members=[{"shown": "between write and sail_check", "part": "stock"}, {"shown": "review -> sail"}])
    assert out["said"][0] == ("a box 'the sail line' on both maps, named by you, holding the stock between write and "
                              "sail_check, the flow review → sail and the machine bot.sail")
    op = changes(project)[-1]
    assert op["members"] == {"stocks": [keys.stock_of_id["btw"]], "flows": [keys.flow_of_id["f_rs"]], "machines": ["bot.sail"]}
    assert drawn(project) == {("declared", out["id"]): "the sail line"}
    out = declare(project, store, "drop the sail line", subsystem="the sail line", drop=True)
    assert changes(project)[-1]["op"] == "retract" and layer(project)["subsystems"] == {} and drawn(project) == {}
    assert out["said"][0] == "the box 'the sail line' is gone from both maps: retracted, kept only in the history"


def test_a_members_words_matching_two_parts_are_one_question_and_nothing_is_declared(project, store):
    model(project)
    msg = "put the stock held at write and bot.sail together as 'the writers'"
    out = declare(project, store, msg, name="the writers", members=[{"shown": "held at write"}], machines=["bot.sail"])
    assert out["changes"] == [] and out["asked"] == ("For 'held at write', which do you mean: the stock held at write · "
                                                     "workflow=delivery, or the stock held at write · workflow=hotfix?")
    assert changes(project) == [] and drawn(project) == {}
    out = declare(project, store, msg + ", the hotfix one", quote=msg, name="the writers",
                  members=[{"shown": "held at write · workflow=hotfix"}], machines=["bot.sail"])
    assert out["said"][0].startswith("a box 'the writers' on both maps, named by you, holding the stock held at write · "
                                     "workflow=hotfix")


def test_a_part_already_in_another_subsystem_is_refused_saying_which(project, store):
    model(project)
    declare(project, store, GATHER, name="the sail line", machines=["bot.sail"], members=[{"shown": "review → sail"}])
    with pytest.raises(StoreError, match=r"the machine bot.sail is already in 'the sail line' \(subsystem the_sail_line\)"):
        declare(project, store, "put bot.sail and the stock done in 'the finish'", name="the finish", machines=["bot.sail"])
    with pytest.raises(StoreError, match="the flow .* is already in 'the sail line'"):
        declare(project, store, "gather the review → sail flow as 'pr flow'", name="pr flow", members=[{"shown": "review → sail"}])
    with pytest.raises(StoreError, match="no machine 'bot.nope' is known"):
        declare(project, store, "put bot.nope alone in 'the nope box'", name="the nope box", machines=["bot.nope"])
    assert list(layer(project)["subsystems"]) == ["the_sail_line"]


def test_a_proposal_draws_no_box_until_the_owner_confirms_and_the_digest_says_it_once_a_day(project, store):
    model(project)
    say(store, "what changed since this morning?")
    assert subsystem_offer.line(project, store, now=1000.0).startswith(
        "Subsystems: the model separates best by workflow (3 boxes: delivery, hotfix, pr_watch), 3 flows inside a box, "
        "0 flows crossing; none is named")
    assert subsystem_offer.line(project, store, now=2000.0) is None            # once a day
    out = call(project, store, "declare_subsystem", value="hotfix", reason="the model separates best by workflow")
    assert out["proposed"] and "no box until the owner confirms it (confirm subsystem:hotfix" in out["said"][0]
    el = layer(project)["subsystems"]["hotfix"]
    assert el["state"] == "proposed" and el["provenance"]["author"] == "supervisor"
    assert drawn(project) == {}                                                # a proposal draws nothing
    assert subsystem_offer.line(project, store, now=1000.0 + 86_400) == (
        "Subsystems: proposed by me, no box until you confirm: 'hotfix' over workflow=hotfix.")
    with pytest.raises(StoreError, match="a name of its own is the owner's"):
        call(project, store, "declare_subsystem", value="delivery", name="the delivery line", reason=WHY)
    say(store, "yes, confirm the hotfix box")
    call(project, store, "confirm", element="subsystem:hotfix", quote="confirm the hotfix box", reason=WHY)
    assert drawn(project) == {("workflow", "hotfix"): "hotfix"}
    assert subsystem_offer.line(project, store, now=1000.0 + 3 * 86_400) is None   # named now: nothing to offer


def test_renaming_a_named_value_redeclares_it(project, store):
    model(project)
    first = declare(project, store, NAME_IT, value="pr_watch", name="release watch")
    out = declare(project, store, "rename release watch to 'the pr line'", subsystem="release watch", name="the pr line")
    assert out["id"] == first["id"] and out["said"][0] == ("a box 'the pr line' over pr_watch on both maps, named by you "
                                                           "(was 'release watch')")
    assert drawn(project) == {("workflow", "pr_watch"): "the pr line"}


def test_the_tool_is_listed_with_the_design_tools():
    tool = {t.name: t for t in DESIGN_TOOLS}["declare_subsystem"]
    assert set(tool.required) == {"reason"} and "never one you made up" in tool.description

