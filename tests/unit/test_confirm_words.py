"""``confirm`` of a machine on the owner's explicit words only (confirm_words.py; bootstrap run 7,
finding 3): "Start tracking." confirmed Jira, GitHub and Slack as machines in run 7's step 7, words
that never spoke of a machine. A yes that names the machine, or all of them, confirms; a bare "it"
confirms one machine a turn."""

import pytest
from design_fixtures import WHY, call, layer, say

from orchestra_agents import intended_api
from orchestra_agents.store import StoreError

pytest_plugins = ["design_fixtures"]

SYSTEMS = (("jira", "Jira"), ("github", "GitHub"), ("slack", "Slack"))
PROPOSED = {"author": "supervisor", "source": "supervisor", "reason": "the systems the owner connected"}


@pytest.fixture
def three(project):
    intended_api.layer(project).apply(
        [{"op": "propose_machine", "id": mid, "title": title, "kind": "programmatic", "managed": "external",
          "description": f"{title} itself", "tags": ["integration"], "engagement": {"mode": "continuous"}}
         for mid, title in SYSTEMS], PROPOSED)


def state(project, mid):
    return layer(project)["machines"][mid]["state"]


def test_run_sevens_start_tracking_confirms_no_machine(project, store, three):
    say(store, "Start tracking.", 7)
    for mid, _ in SYSTEMS:
        with pytest.raises(StoreError, match=r"do not confirm machine .*nothing confirmed|nothing confirmed.*do not confirm"):
            call(project, store, "confirm", element=mid, quote="Start tracking.", reason=WHY)
        assert state(project, mid) == "proposed"


@pytest.mark.parametrize("message", ["Yes, confirm all three.", "Confirm Jira, GitHub and Slack.", "OK, confirm them."])
def test_a_yes_naming_them_or_all_of_them_confirms_each(project, store, three, message):
    say(store, message, 8)
    for mid, _ in SYSTEMS:
        call(project, store, "confirm", element=mid, quote=message, reason=WHY)
        assert state(project, mid) == "declared"


def test_a_yes_naming_one_confirms_that_one_only(project, store, three):
    say(store, "Yes, confirm GitHub.", 8)
    call(project, store, "confirm", element="github", quote="Yes, confirm GitHub.", reason=WHY)
    with pytest.raises(StoreError, match="do not confirm machine jira"):
        call(project, store, "confirm", element="jira", quote="Yes, confirm GitHub.", reason=WHY)
    assert state(project, "github") == "declared" and state(project, "jira") == "proposed"


def test_a_bare_it_confirms_one_machine_a_turn(project, store, three):
    say(store, "Confirm it.", 9)
    call(project, store, "confirm", element="slack", quote="Confirm it.", reason=WHY)
    with pytest.raises(StoreError, match="confirms one machine"):
        call(project, store, "confirm", element="jira", quote="Confirm it.", reason=WHY)
    say(store, "Confirm it.", 10)
    call(project, store, "confirm", element="jira", quote="Confirm it.", reason=WHY)    # the next turn's answer
    assert state(project, "jira") == "declared"
