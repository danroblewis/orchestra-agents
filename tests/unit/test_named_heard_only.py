"""T2c step 10: the owner's "the GitHub pull request events" put github.pr.approved and
github.pr.changes_requested into the nudger's receives. The Workflows plugin's proposals name both
(so they were known), but the stand-in reviewer only comments, so neither was ever heard on the bus,
and L3's check_nudger failed. A phrase now becomes only the types heard on the bus; the answer says
which plausible types were not on the bus. check_nudger's own rule (receives_problem) is the oracle."""

from design_fixtures import WHY, call, layer, say
from live.test_design_live_delivery import receives_problem

from orchestra_agents import design_confirm, intended_api

pytest_plugins = ["design_fixtures"]

WORDS = ("Create a machine called Review Nudger: when a pull request has waited too long for its review or for SAIL, "
         "it reminds the reviewer. It should listen for the GitHub pull request events and the SAIL check results "
         "on the bus. Make it a scheduled task for 9am every day. Then confirm it. I expect SAILED per day ×1.2.")
HEARD = [("github.pr.opened", "person.github.danroblewis"), ("github.pr.synchronize", "person.github.danroblewis"),
         ("github.pr.commented", "person.github.danroblewis"), ("github.pr.labeled", "person.github.danroblewis"),
         ("github.pr.unlabeled", "person.github.danroblewis"), ("github.checks.completed", "bot.github.github-actions"),
         ("sail.check.completed", "sail"), ("offer.made", "supervisor/dispatch")]
PROPOSED = {"author": "workflows", "source": "workflow delivery_live", "reason": "the definition of workflow delivery_live"}
# T2c's own call (raw/stream-s10.json), word for word
GIVEN_PR = ["github.pr.opened", "github.pr.synchronize", "github.pr.commented", "github.pr.labeled", "github.pr.unlabeled",
            "github.pr.approved", "github.pr.changes_requested"]


def t2c(project, store):
    intended_api.layer(project).apply(
        [{"op": "propose_machine", "id": "review_nudger", "title": "review_nudger", "kind": "programmatic",
          "description": "Named by workflow review_nudger: the program review_nudger.", "receives": ["offer.made"],
          "engagement": {"mode": "ad hoc"}},
         {"op": "propose_machine", "id": "github", "title": "github", "kind": "programmatic", "managed": "external",
          "description": "Named by workflow delivery_live: the outside system github.", "engagement": {"mode": "continuous"},
          "emits": ["github.checks.completed", "github.pr.approved", "github.pr.changes_requested", "github.pr.commented",
                    "github.pr.labeled"]}], PROPOSED)
    for ty, src in HEARD:
        store.events.publish(ty, {"number": 21}, source=src)
    say(store, WORDS, 1)


def heard(store) -> set[str]:
    return {e["type"] for e in store.events.read(0)[0]}


def test_the_t2c_call_puts_only_heard_types_in_receives(project, store):
    t2c(project, store)
    out = call(project, store, "refine_machine", name="review_nudger", mode="scheduled", at="09:00",
               add_receives=[*GIVEN_PR, "sail.check.completed"], quote="Create a machine called Review Nudger", reason=WHY,
               owner_named=[{"said": "the GitHub pull request events", "types": GIVEN_PR},
                            {"said": "the SAIL check results on the bus", "types": ["sail.check.completed"]}])
    receives = layer(project)["machines"]["review_nudger"]["receives"]
    assert receives_problem(receives, heard(store)) == ""
    assert "github.pr.approved" not in receives and "github.pr.changes_requested" not in receives
    said = " ".join(out["said"])
    assert ("'the GitHub pull request events' -> github.pr.opened, github.pr.synchronize, github.pr.commented, "
            "github.pr.labeled, github.pr.unlabeled") in said
    assert "'the SAIL check results on the bus' -> sail.check.completed" in said
    assert ("'the GitHub pull request events': github.pr.approved, github.pr.changes_requested never heard on the bus "
            "(plausible, but only announced, declared or guessed): left out of receives") in said


def test_a_family_names_its_unheard_types_and_its_heard_ones_left_out_but_adds_neither(project, store):
    t2c(project, store)
    out = call(project, store, "refine_machine", name="review_nudger", quote="Create a machine called Review Nudger",
               reason=WHY, owner_named=[{"said": "the GitHub pull request events", "types": ["github.pr.opened"]},
                                        {"said": "the SAIL check results on the bus"}])
    receives = layer(project)["machines"]["review_nudger"]["receives"]
    assert set(receives) == {"offer.made", "github.pr.opened"}                 # nothing from the family list
    said = " ".join(out["said"])
    assert "github.pr.approved, github.pr.changes_requested never heard on the bus" in said
    assert ("'the GitHub pull request events' names github.pr, heard as github.pr.commented, github.pr.labeled, "
            "github.pr.synchronize, github.pr.unlabeled: not in its types") in said
    assert "'the SAIL check results on the bus' names sail.check, heard as sail.check.completed: none is in its types" in said
    assert "github.checks.completed" not in said                              # checks are not pull request events
    assert receives_problem(receives, heard(store)).startswith("receives ")   # the oracle fails it: four heard types missing


def test_declare_machine_leaves_out_an_unheard_named_type_given_in_receives(project, store):
    say(store, "Create a machine called Approval Watcher that listens for the GitHub pull request events", 1)
    t2c(project, store)
    call(project, store, "declare_machine", name="Approval Watcher", kind="universal", description="Watches approvals",
         receives=["github.pr.approved", "github.pr.opened"], quote="Create a machine called Approval Watcher", reason=WHY,
         owner_named=[{"said": "the GitHub pull request events", "types": ["github.pr.approved", "github.pr.opened"]}])
    assert layer(project)["machines"]["approval_watcher"]["receives"] == ["github.pr.opened"]


# T2d s10: the Workflows plugin's proposal of review_nudger carried the floor's protocol types, and
# offer.withdrawn was on no bus that run; the owner's types matched exactly the heard ones.
T2D_HEARD = [("github.pr.opened", "person.github.danroblewis"), ("github.pr.synchronize", "person.github.danroblewis"),
             ("github.pr.commented", "person.github.danroblewis"), ("github.pr.labeled", "person.github.danroblewis"),
             ("github.pr.unlabeled", "person.github.danroblewis"), ("github.checks.completed", "bot.github.github-actions"),
             ("sail.check.completed", "sail"), ("offer.made", "supervisor/dispatch"), ("offer.claimed", "worker/1"),
             ("offer.finished", "worker/1")]
T2D_NAMED = [{"said": "the GitHub pull request events", "types": ["github.pr.opened", "github.pr.synchronize",
                                                                   "github.pr.labeled", "github.pr.unlabeled",
                                                                   "github.pr.commented"]},
             {"said": "the SAIL check results", "types": ["sail.check.completed"]}]


def t2d(project, store):
    intended_api.layer(project).apply(
        [{"op": "propose_machine", "id": "review_nudger", "title": "review_nudger", "kind": "programmatic",
          "description": "Named by workflow review_nudger: the program review_nudger.",
          "receives": ["offer.made", "offer.withdrawn"], "emits": ["offer.finished"], "engagement": {"mode": "ad hoc"}}],
        {**PROPOSED, "source": "workflow review_nudger"})
    for ty, src in T2D_HEARD:
        store.events.publish(ty, {"number": 24}, source=src)
    say(store, WORDS, 1)


def test_the_t2d_call_keeps_the_floors_protocol_and_passes_the_oracle(project, store):
    t2d(project, store)
    out = call(project, store, "refine_machine", name="review_nudger", owner_named=T2D_NAMED, mode="scheduled", at="09:00",
               add_invariants=["Reminds the reviewer only about a pull request that has waited longer than its limit"],
               quote="Make it a scheduled task for 9am every day.", reason=WHY)
    receives = layer(project)["machines"]["review_nudger"]["receives"]
    assert receives == ["offer.made", "offer.withdrawn", "github.pr.opened", "github.pr.synchronize", "github.pr.labeled",
                        "github.pr.unlabeled", "github.pr.commented", "sail.check.completed"]     # T2d's, as it was stored
    assert "offer.withdrawn" not in heard(store)
    assert receives_problem(receives, heard(store)) == ""
    said = " ".join(out["said"])
    assert "the floor's protocol (the plugin's), not yours: offer.made, offer.withdrawn" in said
    assert "offer.withdrawn never heard" not in said


def test_an_unheard_type_outside_the_protocol_still_fails_the_oracle():
    assert receives_problem(["offer.withdrawn", "github.pr.opened", "sail.check.completed", "github.pr.approved"],
                            {"github.pr.opened", "sail.check.completed"}).endswith("['github.pr.approved'] not heard on the bus")


def test_the_owners_words_neither_add_nor_drop_a_protocol_type(project, store):
    t2d(project, store)
    call(project, store, "refine_machine", name="review_nudger", quote="Make it a scheduled task for 9am every day.",
         reason=WHY, owner_named=[{"said": "the GitHub pull request events", "types": ["github.pr.opened", "offer.claimed"]},
                                  {"said": "the offers withdrawn", "types": ["offer.withdrawn"]}])
    receives = layer(project)["machines"]["review_nudger"]["receives"]
    assert receives == ["offer.made", "offer.withdrawn", "github.pr.opened"]     # offer.claimed not added, offer.withdrawn kept


def test_confirm_lists_the_protocol_apart(project, store, monkeypatch):
    t2d(project, store)
    monkeypatch.setattr(design_confirm.machines_running, "workflow_programs", lambda p: {"review_nudger": ["review_nudger"]})
    say(store, "Then confirm it", 2)
    out = call(project, store, "confirm", element="review_nudger", quote="Then confirm it", reason=WHY)
    assert "the floor's protocol (the plugin's), not yours: offer.made, offer.withdrawn" in " ".join(out["said"])
