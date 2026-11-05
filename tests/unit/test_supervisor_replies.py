"""What the Supervisor tells the human: a chat reply answers the human's message, with Orchestra's
notices marked as the Supervisor's alone; a reply that claims an action the turn did not take
gets one more turn to take it or say so; and a ticket it created for the human that is done wakes
it to leave the human one short message."""

import subprocess

import pytest
from conftest import FakeHarness, FakeSession, agents_project, supervisor_knows

from orchestra_agents import prompting, protocol
from orchestra_agents.events import Events
from orchestra_agents.harness.base import TurnResult
from orchestra_agents.host import AgentHost
from orchestra_agents.promises import unkept
from orchestra_agents.store import AgentStore


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(protocol, "WAIT_S", 0.3)                 # no task system here: the snapshot gives up quickly
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo)


class Scripted(FakeSession):
    """Replies in order, one per turn; a reply of the form ("text", n) also says n tools ran."""

    def __init__(self, spec, replies):
        super().__init__(spec, None)
        self.replies = list(replies)

    def prompt(self, text, timeout, on_event=None, idle_timeout=None):
        self.prompts.append(text)
        reply = self.replies.pop(0) if self.replies else "ok"
        reply, tools = reply if isinstance(reply, tuple) else (reply, 0)
        for _ in range(tools):
            on_event and on_event({"type": "tool_start", "name": "update_ticket", "args": {}})
        on_event and on_event({"type": "assistant_end", "text": reply, "tool_calls": []})
        return TurnResult(text=reply)


class ScriptedHarness(FakeHarness):
    def __init__(self, replies):
        super().__init__()
        self.replies = replies

    def start(self, spec):
        s = Scripted(spec, self.replies)
        self.sessions.append(s)
        return s


def host_with(project, harness):
    store, events = AgentStore(project), Events.for_project(project)
    host = AgentHost(project, store, events)
    host.harness = harness
    host._refresh_org(store.runtime()["agent_state"])
    return host, store, events


def test_the_humans_message_is_marked_and_the_notices_are_the_supervisors_alone():
    assert prompting.chat_turn("", "yes, go ahead") == "yes, go ahead"            # no notices: the message as itself
    out = prompting.chat_turn("[Orchestra: 3 events since your last turn: gate.landed=3]\n\n", "what landed?")
    assert out.index("gate.landed") < out.index("the human has not seen them") < out.index("[The human's message]\nwhat landed?")
    assert "gate.landed" not in prompting.CONCERNS                                     # landings wake the Supervisor instead


def test_the_live_tests_unkept_promise_is_found_and_honest_replies_pass():
    live = ("The ticket is already IN_PROGRESS with worker/1, so the worker may have started from the New York default. "
            "I'll check with the coordinator to make sure they read the change.")
    assert unkept(live, ["project_status", "list_tickets"]) == "I'll check with the coordinator to make sure they read the change."
    assert unkept("Tell me your city and I'll update DEMO-6's description before a worker picks it up.", ["list_tickets"]) is None
    assert unkept("I'll tell you when DEMO-5 lands. Let me know if the interval is wrong.", ["list_tickets"]) is None
    assert unkept("I updated DEMO-6 with Boulder.", ["update_ticket"]) is None
    assert unkept("I updated DEMO-6 with Boulder.", []) == "I updated DEMO-6 with Boulder."   # a claim with no tool call


def test_a_reply_that_promises_a_later_action_gets_one_more_turn(project):
    harness = ScriptedHarness([("DEMO-6 now says Boulder. I'll check with the coordinator that the worker saw it.", 1),
                               ("I left the coordinator a note about the change.", 1)])
    host, store, _events = host_with(project, harness)
    seen = []
    out = host.prompt_supervisor("use Boulder for the weather", seen.append)
    sup = harness.sessions[0]
    assert len(sup.prompts) == 2 and "I'll check with the coordinator" in sup.prompts[1] and "Nothing runs after your reply" in sup.prompts[1]
    assert out["text"] == "DEMO-6 now says Boulder. I'll check with the coordinator that the worker saw it.\n\nI left the coordinator a note about the change."
    assert [e["text"] for e in seen if e["type"] == "assistant_end"][-1] == "I left the coordinator a note about the change."
    assert store.find_session("supervisor")["last_text"].endswith("I left the coordinator a note about the change.")


def test_an_honest_reply_is_one_turn(project):
    harness = ScriptedHarness([("I created DEMO-5 and DEMO-6. Tell me the city and I'll add it to DEMO-6.", 2)])
    host, _store, _events = host_with(project, harness)
    out = host.prompt_supervisor("measure the weather", lambda ev: None)
    assert len(harness.sessions[0].prompts) == 1 and out["text"].startswith("I created DEMO-5")
    assert harness.sessions[0].prompts[0].endswith("[The human's message]\nmeasure the weather")   # the context document came first


def test_a_chat_turn_is_the_supervisors_last_turn_though_nothing_woke_it(project):
    from orchestra_agents.org_views import agents_status
    harness = ScriptedHarness(["All quiet."])
    host, store, _events = host_with(project, harness)
    assert not store.agent_state("supervisor").get("last_turn")
    host.prompt_supervisor("how is it going?", lambda ev: None)
    st = store.agent_state("supervisor")
    assert st["last_turn"] > 0 and not st.get("last_woken")             # the owner's message is a turn, not a wake-up
    sup = next(a for a in agents_status(project, store)["agents"] if a["name"] == "supervisor")
    assert sup["last_turn"] == st["last_turn"]
    host.residents.close_all()


def test_the_humans_ticket_that_is_done_wakes_the_supervisor_to_say_so(project):
    harness = ScriptedHarness([("DEMO-5 is done: the disk and git collectors are running; their series are on the Core page.", 0)])
    host, store, events = host_with(project, harness)
    events.publish("ticket.created", {"ticket": "DEMO-4", "title": "Measure the repository", "by": "planner"}, source="planner", subject="DEMO-4")
    events.publish("ticket.created", {"ticket": "DEMO-5", "title": "Measure the disk", "by": "supervisor"}, source="supervisor", subject="DEMO-5")
    events.publish("ticket.created", {"ticket": "DEMO-7", "title": "Dropped", "by": "supervisor"}, source="supervisor", subject="DEMO-7")
    # how a ticket's end is heard, the task system publishing no state change (protocol.ended): the
    # gate's landing, a cancel request done; a step on the way (a review requested) is no end
    events.publish("gate.landed", {"ticket": "DEMO-4", "gate_run": "g1"}, source="merge_gate", subject="DEMO-4")
    events.publish("review.requested", {"ticket": "DEMO-5"}, source="worker/1", subject="DEMO-5")
    events.publish("request.done", {"request": "r1", "result": {"ok": True, "ticket": {"id": "DEMO-7", "state": "CANCELLED"}}},
                   source="supervisor", subject="DEMO-7")
    events.publish("gate.landed", {"ticket": "DEMO-5", "gate_run": "g2"}, source="merge_gate", subject="DEMO-5")
    host._wake_on_events()
    [w] = [w for w in store.runtime()["wakeups"] if w["target"] == "supervisor"]
    assert w["reason"].startswith("ticket DEMO-5 (Measure the disk), which was made for the human's request, is DONE")
    assert store.agent_state("supervisor")["asked_tickets"] == {}                 # followed until done or cancelled, then forgotten
    host._wake("supervisor", [w["reason"]])
    prompt = harness.sessions[0].prompts[-1]
    assert prompt.startswith(prompting.SUPERVISOR_WAKE) and "Leave the human one short message" in prompt
    assert store.find_session("supervisor")["last_text"].startswith("DEMO-5 is done")   # the message the human sees waiting


def test_a_hand_off_s_promise_to_tell_the_plan_is_kept():
    from orchestra_agents.harness.dummy_script import Turn, load
    rule = load(None).pick(Turn(role="supervisor", prompt="[The human's message]\nAdd a page"))
    reply = rule.reply.replace("{kept.h.request}", "r-0a1b2c3d")              # the built-in hand-off's reply
    assert "I will tell you the plan" in reply
    assert unkept(reply, ["hand_to_coordinator"]) is None                       # the plan wakes it (work.planned)
    assert unkept(reply, ["project_status"]) is not None                         # no hand-off: nothing will wake it


def test_an_items_state_comes_from_this_turns_tools(project):
    """T2 s14: the merged SCRUM-17 and SCRUM-18 were called waiting from memory of earlier turns."""
    text = " ".join(supervisor_knows(project).split())
    assert "an item the tools list as closed is closed, never \"waiting\"" in text
    assert "never from memory of an earlier turn" in text


def test_a_reminder_is_requested_with_the_owners_words_and_no_question_back(project):
    """T2 s11: "Remind the reviewer on B's pull request." was answered with "which one?" and nothing
    was requested, so the owner's one page act never happened."""
    text = " ".join(supervisor_knows(project).split())
    assert "A reminder (\"remind the reviewer\", \"nudge them\") is a piece of work too: `request_work`" in text
    assert "with the owner's words as the brief and the quote" in text and "never ask them which one first" in text


def test_a_heard_type_needs_no_declaration(project):
    """T2 s10: sail.check.completed was dropped because "no machine declares it"; sail publishes it."""
    text = " ".join(supervisor_knows(project).split())
    assert "A type in `by_type` is heard on the bus; it needs no declaration, and goes in receives as it is" in text


def test_times_are_given_in_the_pages_zone_named(monkeypatch):
    """T2 (b O10, d O6): replies gave UTC times while the pages showed PST."""
    import time as _time
    from datetime import UTC, datetime
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    _time.tzset()
    try:
        now = datetime(2026, 11, 1, 22, 47, tzinfo=UTC).timestamp()
        line = prompting.clock_line(now)
        assert "now 2026-11-01 14:47 PST (UTC-08:00)" in line and '"14:47 PST"' in line and "never in UTC" in line
        ctx = prompting.supervisor_context({"counts": {}, "absent": True}, [{"type": "item.requested"}])
        assert ctx.startswith("[Orchestra: now ") and "PST" in ctx.split("\n")[0]
        assert prompting.supervisor_context({"counts": {}, "absent": True}, []) == ""        # "yes, go ahead" as itself
    finally:
        monkeypatch.delenv("TZ")
        _time.tzset()


def test_recording_a_factor_says_what_judging_it_needs(project):
    text = " ".join(supervisor_knows(project).split())
    assert "the flow fitted in both eras" in text and "about 200 a side for ×1.2" in text


def test_a_promise_right_after_a_question_waits_on_the_answer():
    """R3 e (O10): "Which do you want? I'll set it up." cost a follow-up turn at T2i s01, s14, s15."""
    from orchestra_agents.promises import unkept
    assert unkept("Two ways: a poller or a source. Which do you want? I'll set it up.", []) is None
    assert unkept("Shall I write it to the repository?\nI'll keep it in the state directory otherwise.", []) is None
    assert unkept("Which do you want? Fine. I'll set it up.", []) == "I'll set it up."     # only right after
    assert unkept("I'll set it up. Which do you want?", []) == "I'll set it up."           # a question after: no
