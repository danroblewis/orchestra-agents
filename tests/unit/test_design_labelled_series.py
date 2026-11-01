"""What the live delivery example (L3) needs of the design tools: its series are labelled (one
`workflow_pile_items` per pile), so an existing series is named with its labels and never matched
to the first stock of its name; one the store records but no discovery has as a stock yet is still
the measurement, chosen and waiting for a link, with nothing new run; and the owner's expectation
written as a factor ("SAILED per day ×1.2") is heard."""

import time

from design_fixtures import WHY, call, layer, model_with, say
from orchestra.metrics import Point, for_project

from orchestra_agents.design_asked import expectations_in
from orchestra_agents.design_dynamics import discovered_stock_of

pytest_plugins = ["design_fixtures"]

ASK = "Create a stock called PRs Waiting for SAIL."
PILE = "workflow_pile_items"
SAIL_CHECK = {"workflow": "delivery_live", "step": "sail_check", "place": "waiting", "pile": "sail_check.waiting"}
WRITE = {"workflow": "delivery_live", "step": "write", "place": "held", "pile": "write.held"}


def existing(series: str) -> dict:
    return {"label": "pull requests at the sail_check pile", "kind": "existing_series", "series": series,
            "how": "the Workflows plugin's piles instrument", "cost": "nothing new"}


def waiting_stock(project, store):
    say(store, ASK)
    call(project, store, "declare_stock", name="PRs Waiting for SAIL", quote=ASK, reason=WHY)
    return "prs_waiting_for_sail"


def test_a_labelled_series_names_its_one_stock_and_a_bare_shared_name_none(project, store):
    model_with(project, {"w": (PILE, WRITE), "s": (PILE, SAIL_CHECK)})
    assert discovered_stock_of(project, f'{PILE}{{pile="sail_check.waiting"}}').startswith(PILE + "{")
    assert "sail_check.waiting" in discovered_stock_of(project, f"{PILE}{{step=sail_check, place=waiting}}")
    assert discovered_stock_of(project, PILE) is None                       # two piles: not the first one
    assert discovered_stock_of(project, f'{PILE}{{pile="review.waiting"}}') is None


def test_the_labelled_existing_series_is_linked_and_a_bare_one_is_offered_with_the_candidates(project, store):
    model_with(project, {"w": (PILE, WRITE), "s": (PILE, SAIL_CHECK)})
    eid = waiting_stock(project, store)
    out = call(project, store, "propose_measurements", element=eid, reason=WHY, options=[existing(PILE)])
    assert not layer(project)["stocks"][eid].get("found_as")
    assert any(x.startswith(f"{PILE} is 2 discovered stocks") and "name the one" in x for x in out["said"])
    call(project, store, "propose_measurements", element=eid, reason=WHY,
         options=[existing(f'{PILE}{{pile="sail_check.waiting"}}')])
    assert "sail_check.waiting" in layer(project)["stocks"][eid]["found_as"]


def test_a_recorded_series_no_discovery_has_yet_is_chosen_and_waits_for_a_link(project, store):
    lab = {**SAIL_CHECK, "instrument": "workflows.piles", "project": project.key}
    for_project(project.orchestra).write([Point(PILE, 1, time.time() - 10, lab)])
    eid = waiting_stock(project, store)
    out = call(project, store, "propose_measurements", element=eid, reason=WHY,
               options=[existing(f'{PILE}{{pile="sail_check.waiting"}}')])
    stock = layer(project)["stocks"][eid]
    assert stock["measure"]["chosen"] == "a" and not stock.get("found_as")
    assert any("waits for a link" in x and "Nothing new is recorded" in x for x in out["said"])
    say(store, "Track deploys too.", 2)
    call(project, store, "declare_stock", name="Deploys", reason=WHY)
    call(project, store, "propose_measurements", element="deploys", reason=WHY, options=[existing("deploys_total")])
    assert layer(project)["stocks"]["deploys"]["measure"]["chosen"] is None    # a series the store does not hold


def test_a_factor_written_with_a_times_sign_is_an_expectation():
    assert expectations_in("Confirm it. I expect SAILED per day ×1.2.") == ["expect SAILED per day ×1.2"]
    assert expectations_in("SAILED per day x1.2, please") == ["SAILED per day x1.2"]
    assert expectations_in("Lay it out on a 1x2 grid, an x 2 b.") == []
