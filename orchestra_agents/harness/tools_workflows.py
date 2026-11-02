"""The ``workflows`` tools' definitions (orchestra-workflows' SPEC §7.1, §10), and ``request_work``
(the ``work`` group), part of the one catalogue in ``tools.py``, which appends them. What each does
is ``orchestra_agents/workflow_tools.py`` and ``request_work.py``.
The definition's schema spells out the vocabulary's every key, with what it means, so a small model
(Haiku) writes it without guessing; the plugin's own checks are still the judge."""

from __future__ import annotations

from .tools import A, B, I, S, ToolDef
from .tools_design import QUOTE, REASON, E

DURATION = S("A duration: 30m, 2h, 1d")
EVENT_PROPS = {
    "event": S("The event type about the item (offer.finished, github.pr.opened)"),
    "of": E(["children"], "children: the event is about one of the item's children, not the item"),
    "data": {"type": "object", "description": "Fields of the event's data that must equal a value, an item field "
             "({\"item\": \"commit\"}) or a setting ({\"setting\": \"label\"}): {\"head_sha\": {\"item\": \"commit\"}}"},
    "count": I("At least this many such events; it counts events, so one person approving twice is 2 (add "
               "distinct: source to count people)"),
    "distinct": E(["source"], "source: count one event per source, so count is the number of distinct people or "
                              "machines (two different reviewers: count 2, distinct source)"),
}
METRIC_PROPS = {
    "metric": S("A gauge's name (never a counter: no _total, _count, _sum, _bucket)"),
    "labels": {"type": "object", "description": "The gauge's labels, {name: value or {\"item\": field}}"},
    "above": {"type": "number", "description": "Met while the gauge is above this"},
    "below": {"type": "number", "description": "Met while the gauge is below this"},
    "for": S("For this long without a gap: 30m"),
    "exit": {"type": "number", "description": "For an on: the threshold that ends it (hysteresis)"},
}
DONE_WHEN = {"type": "object", "description": (
    "When the step is done. Leave out for a role's or a program's step (done when its turn finishes, with the outcome "
    "it reports) and for an outbox step (done on the outbox's result). Otherwise one of: an event {event, of, data, "
    "count, distinct} (its outcome is the event's data.outcome, else done); a metric condition {metric, labels, "
    "above|below, for}; a fan-in {children: {all: [outcome], else: outcome}} (every child ended with one of all, else "
    "the else outcome; the parent following its slowest child is this); or a wait {after: \"5m\"} (done that long "
    "after the step was entered, outcome done: a pause before a retry, never a sleep in a machine). A fan-in or an "
    "after step has no who. An outcome the step's outcomes do not name leaves the item at the step and tells the "
    "planner (attention)"), "properties": {**EVENT_PROPS, **METRIC_PROPS, "after": DURATION, "children": {
        "type": "object", "description": "A fan-in over the item's children",
        "properties": {"all": A("The outcomes every child must end with; the first is the step's outcome"),
                       "else": S("The outcome otherwise")}}}}
ON = {"type": "object", "description": "An event or a metric condition that moves the item whenever it happens",
      "properties": {**EVENT_PROPS, **METRIC_PROPS, "to": {"type": ["string", "null"], "description": "The step it "
                     "moves to, or null for the end"}}}
WHO = {"type": "object", "description": (
    "Who does the step: exactly one key; leave who out for a fan-in or an after step (nobody acts there). role: any "
    "machine announcing that role (an AI agent or a program: worker, reviewer), offered to it; program: a program this "
    "plugin runs per item (machines/<id>/machine.json), told its workflow, step and the item's fields in its "
    "environment (ORCHESTRA_WORKFLOW, ORCHESTRA_STEP, ORCHESTRA_FIELDS); outside: a system outside (github, jira), a "
    "wait, nothing is offered, and only that system's events count; person: a person (owner), a wait only their own "
    "act moves (a machine may prepare it, never do it), never offered; outbox: a person (owner) as whom an act in "
    "another system is done: the step's draft, which the floor hands to the Outbox plugin and never performs itself. "
    "The item waits awaiting the owner's approval, the outbox performs it, and the step ends sent, rejected or failed "
    "on the outbox's result. Never offered, no done_when"),
    "properties": {"role": S("The role's name"), "program": S("The program's id"),
                   "outside": S("The system's name (github), or a relayed actor's source (bot.triage)"),
                   "person": S("The person's name: owner"),
                   "outbox": S("The person the act is done as: owner")}}
VALUE = ("a plain value, an item field ({\"item\": \"pull\"}), the item itself ({\"item\": \"id\"}) or a setting "
         "({\"setting\": \"label\"})")
DRAFT = {"type": "object", "description": (
    "An outbox step's act (required there, refused on any other step): what the Outbox plugin performs as the owner "
    "after their approval. Each value is " + VALUE + ". The text comes from an item field an earlier step wrote "
    "(a role writes the ping's text into the nudge field; the outbox step drafts from it): there is no text template"),
    "properties": {"kind": S("A kind in the owner's [outbox.kinds], by its name there (review.request); one not "
                             "configured yet is a kind for the owner to add to their overlay, with its sender and "
                             "template"),
                   "target": {"type": "object", "description": "Where the act lands, {name: value}: the repository "
                              "and pull request, the ticket, the channel"},
                   "content": {"description": "The text or the label: " + VALUE},
                   "fields": {"type": "object", "description": "The act's other fields, {name: value}"},
                   "recipients": {"type": "array", "description": "Who it goes to, 1 to 50 ids, each " + VALUE}},
    "required": ["kind"]}
STEP = {"type": "object", "description": "One stage the item goes through", "properties": {
    "name": S("The step's name: lowercase letters, digits, _ and - (write, review)"),
    "who": WHO,
    "needs": A("Resource kinds claimed before the step and held across hand-offs (worktree)"),
    "lease": S("How long an offer is held before it is offered again: 10m (longer than the heartbeat plus a tick)"),
    "done_when": DONE_WHEN,
    "outcomes": {"type": "object", "additionalProperties": {"type": ["string", "null"]}, "description": (
        "Each result of the step -> the step it leads to, or null for the end: {\"approved\": \"gate\", \"rejected\": "
        "\"write\"} (a move back is rework: offered to whoever did that step). Leave out: any outcome goes to the next "
        "step")},
    "guards": {"type": "object", "description": "Limits: past one, attention for the planner, or a move to a step",
               "properties": {"within": DURATION, "rounds": I("At most this many entries into the step (review rounds)"),
                              "then": S("attention (default), or the step to move the item to")}},
    "on": {"type": "array", "items": ON, "description": "Moves on an event or a metric condition, whenever it happens"},
    "depends": {"type": "object", "description": "Not offered until each item this one depends on (item.related "
                "depends_on, from the machine that knows it) is at a step", "properties": {"at": S("That step's name, or end")}},
    "draft": DRAFT,
}, "required": ["name"]}
DEFINITION = {"type": "object", "description": "The workflow, in the vocabulary's words and nothing else (no "
              "expressions or scripts: anything that needs logic is a machine)", "properties": {
    "name": S("The workflow's name: lowercase letters, digits, _ and - (task_loop); its folder's name"),
    "about": S("One sentence: what the workflow is for and what a request's brief selects (at most 400 characters)"),
    "planner": S("The role that is told when an item needs attention (default: the settings' planner, coordinator)"),
    "starts": {"type": "array", "description": "What starts an item: [{\"request\": true}] (an item.requested naming "
               "it), [{\"event\": \"jira.issue.assigned\", \"data\": {\"assignee\": {\"setting\": \"owner\"}}}] (an "
               "event, only when its data fields match), or [{\"every\": \"1h\"}] (one item per interval)",
               "items": {"type": "object", "properties": {
                   "request": B("true: a request naming the workflow starts it"),
                   "event": S("An event type that starts an item"),
                   "data": {"type": "object", "description": "With event: fields of its data that must equal a value or "
                            "a setting, or the event makes no item (only tickets assigned to the owner: {\"assignee\": "
                            "{\"setting\": \"owner\"}})"},
                   "every": DURATION}}},
    "steps": {"type": "array", "items": STEP, "description": "The steps in order; the first is where an item starts"},
    "piles": {"type": "array", "description": "Declared piles beyond each step's own (<step>.waiting, <step>.held)",
              "items": {"type": "object", "properties": {
                  "name": S("The pile's name"),
                  "where": {"type": "object", "description": "Predicates, all to hold: step, held, set, unset, equals, "
                            "contains, seen, unseen, open_child, unmet_dependency, closed"}}}},
    "settings": {"type": "object", "description": "Parameters a project fills in [workflows.settings.<name>]: "
                 "{\"label\": {\"type\": \"string\", \"default\": \"SAIL\"}}"},
}, "required": ["name", "steps"]}

WORKFLOW_TOOLS: list[ToolDef] = [
    ToolDef("declare_workflow", "workflows",
            "Write a workflow the owner describes (the Workflows plugin's definition): the plugin's checks first; with any "
            "problem nothing is written and every problem is said; otherwise it is written to the state directory (or the "
            "repository's .orchestra/ when the owner says so) and the answer says what the project still lacks for it. "
            "The Items page shows its items, and the Dynamics page its piles. Never write a definition file any other way. "
            "Use the vocabulary's words: a start filtered on its event's data (only tickets assigned to the owner); "
            "count with distinct: source for people (two distinct reviewers), count alone counts events; who: person: "
            "owner for the owner's own act (only theirs counts, never offered); who: outbox: owner with a draft for "
            "any write to another system as the owner (the Outbox performs it after the owner's approval, never the "
            "floor or a machine; its text from an item field an earlier role step wrote); done_when after for a "
            "pause before a retry; no who on a fan-in or an after step; depends for an item waiting on another.",
            {"definition": DEFINITION,
             "root": E(["state", "repository"], "state (default): the state directory, never committed; repository: "
                                                "the repository's .orchestra/, only when the owner says so (quote them)"),
             "quote": QUOTE, "reason": REASON}, ("definition", "reason")),
    ToolDef("list_workflows", "workflows",
            "The project's workflows as the plugin loads them: each with where it is defined, its planner, starts and "
            "steps (who, needs, outcomes, guards), and the loader's problems (definitions left out) and notes (shadowed)."),
    ToolDef("list_items", "workflows",
            "The open items of every workflow, as the Items page lists them, oldest first: each with its step and pile, "
            "why it waits and on whom (its wait: reason, by, until, and ends_on: every event that ends it), its holder, how "
            "long it has been there (age_s), how often it was reworked, and its fields (a ticket's key, a pull request's "
            "number); then the recently closed, with their last step and outcome. An item's end is item.closed (the floor "
            "publishes it when a workflow's item reaches the end of every workflow it entered): a closed item is never "
            "waiting. What to read first when the owner asks what is stuck or what needs them.",
            {"workflow": S("Only this workflow's items (leave out: every workflow)")}),
    ToolDef("explain_item", "workflows",
            "Why an item is where it is: its summary, each field with the fact that set it, its position in each workflow "
            "(step, pile, wait reason), the facts not counted toward a step and why, and its last facts.",
            {"item": S("The item's id (its subject: ORC-12, task_loop@2026-11-01T00:00Z)"),
             "last": I("How many of its latest facts to show (default 15)")}, ("item",)),
    ToolDef("request_work", "work",
            "Start a piece of work on a workflow: publishes the Workflows plugin's item.requested (and an item.fact "
            "with the fields, if any). The Supervisor asks only for the owner: quote their words, and the request goes "
            "out as theirs (source owner); the Coordinator's is its own, as the planner. Refused, nothing published: "
            "an unknown workflow or one a request does not start (list_workflows), an id already used, or the floor "
            "not running. Answers the item's id, the Items page link and how to follow it (explain_item).",
            {"workflow": S("The workflow's name, as list_workflows shows it (task_loop)"),
             "brief": S("The work asked for, in the owner's terms: what the first step's machine is given"),
             "parent": S("The parent item's id, when this is part of a larger item"),
             "fields": {"type": "object", "description": "The item's fields to set at once, {name: value}: "
                        "{\"priority\": 2}"},
             "item": S("The new item's id (leave out: <workflow>-<6 hex>)"),
             "quote": S("The Supervisor's: the owner's own words asking for this work, from their message this turn, "
                        "exactly, at least three words (or their whole message). Required of the Supervisor; never "
                        "invent one. The Coordinator leaves it out"),
             "reason": REASON}, ("workflow", "brief", "reason")),
]
