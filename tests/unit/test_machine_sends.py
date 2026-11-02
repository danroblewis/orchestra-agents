"""T2i step 10: the answer no longer said that each reminder is a comment drafted to the Outbox that
waits for the owner's approval (step 11's did). It is in the data now: a machine whose workflow has
an outbox step carries ``sends`` on declare_machine, refine_machine and confirm, from the kind's
``approval`` in ``[outbox.kinds]``."""

from types import SimpleNamespace

from design_fixtures import WHY, call, say
from test_named_heard_only import t2d

from orchestra_agents import machine_sends, workflow_tools

pytest_plugins = ["design_fixtures"]

WAITS = "each comment on a pull request (live.nudge) is drafted to the Outbox and waits for the owner's approval"


def fake_plugin(monkeypatch, steps):
    """workflow -> [(actor kind, actor name, draft kind or None)]."""
    wfs = [SimpleNamespace(name=w, steps=[SimpleNamespace(actor=SimpleNamespace(kind=k, name=n),
                                                          draft=SimpleNamespace(kind=d) if d else None) for k, n, d in ss])
           for w, ss in steps.items()]
    define = SimpleNamespace(load=lambda o: SimpleNamespace(workflows=wfs, problems=[], notes=[]))
    monkeypatch.setattr(workflow_tools, "plugin", lambda: SimpleNamespace(define=define))


def nudger(monkeypatch):
    fake_plugin(monkeypatch, {"review_nudger": [("program", "review_nudger", None), ("outbox", "owner", "live.nudge")],
                              "delivery_live": [("program", "has_ticket", None), ("outbox", "owner", "live.sail")]})


def kinds(project, text: str) -> None:
    toml = project.orchestra.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text() + "\n" + text)
    project.reload_config()


def test_an_always_kind_waits_for_the_owner_and_an_auto_kind_is_sent_by_configuration(project, monkeypatch):
    nudger(monkeypatch)
    kinds(project, '[outbox.kinds."live.nudge"]\nsender = "github.pr_comment"\napproval = "always"\n'
                   '[outbox.kinds."live.sail"]\nsender = "github.label"\napproval = "auto"\n')
    assert machine_sends.sends(project, "review_nudger") == WAITS
    assert machine_sends.sends(project, "has_ticket") == ("each label on a pull request (live.sail) is sent by "
                                                          "configuration, without waiting for the owner's approval")
    assert machine_sends.sends(project, "worker") is None


def test_a_kind_with_no_approval_set_waits_as_the_outbox_does(project, monkeypatch):
    nudger(monkeypatch)
    assert machine_sends.sends(project, "review_nudger") == ("each draft (live.nudge) is drafted to the Outbox and waits "
                                                             "for the owner's approval")
    monkeypatch.setattr(workflow_tools, "plugin", lambda: None)
    assert machine_sends.sends(project, "review_nudger") is None


def test_refine_and_confirm_answers_carry_sends(project, store, monkeypatch):
    nudger(monkeypatch)
    kinds(project, '[outbox.kinds."live.nudge"]\nsender = "github.pr_comment"\napproval = "always"\n')
    t2d(project, store)
    out = call(project, store, "refine_machine", name="review_nudger", mode="scheduled", at="09:00",
               quote="Make it a scheduled task for 9am every day.", reason=WHY)
    assert out["sends"] == WAITS
    say(store, "Then confirm it", 2)
    out = call(project, store, "confirm", element="review_nudger", quote="Then confirm it", reason=WHY)
    assert out["sends"] == WAITS


def test_a_machine_no_workflow_runs_sends_nothing(project, store, monkeypatch):
    nudger(monkeypatch)
    say(store, "Create a machine called Greeter: it says hello on each new pull request.", 1)
    out = call(project, store, "declare_machine", name="greeter", kind="universal", title="Greeter",
               description="Says hello on each new pull request.", reason=WHY,
               quote="Create a machine called Greeter: it says hello on each new pull request.", mode="ad hoc")
    assert out["sends"] is None
