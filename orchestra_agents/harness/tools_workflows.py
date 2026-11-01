"""The ``workflows`` tools' definitions (orchestra-workflows' SPEC §7.1, §10), part of the one
catalogue in ``tools.py``, which appends them. What each does is ``orchestra_agents/workflow_tools.py``.
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
    "count": I("At least this many such events (two approvals: 2)"),
}
METRIC_PROPS = {
    "metric": S("A gauge's name (never a counter: no _total, _count, _sum, _bucket)"),
    "labels": {"type": "object", "description": "The gauge's labels, {name: value}"},
    "above": {"type": "number", "description": "Met while the gauge is above this"},
    "below": {"type": "number", "description": "Met while the gauge is below this"},
    "for": S("For this long without a gap: 30m"),
    "exit": {"type": "number", "description": "For an on: the threshold that ends it (hysteresis)"},
}
DONE_WHEN = {"type": "object", "description": (
    "When the step is done. Leave out for a role's or a program's step: done when its turn finishes, with the outcome "
    "it reports. Otherwise one of: an event {event, of, data, count}; a metric condition {metric, labels, above|below, "
    "for}; a fan-in {children: {all: [outcome], else: outcome}} (every child ended with one of all, else the else "
    "outcome)"), "properties": {**EVENT_PROPS, **METRIC_PROPS, "children": {
        "type": "object", "description": "A fan-in over the item's children",
        "properties": {"all": A("The outcomes every child must end with; the first is the step's outcome"),
                       "else": S("The outcome otherwise")}}}}
ON = {"type": "object", "description": "An event or a metric condition that moves the item whenever it happens",
      "properties": {**EVENT_PROPS, **METRIC_PROPS, "to": {"type": ["string", "null"], "description": "The step it "
                     "moves to, or null for the end"}}}
WHO = {"type": "object", "description": (
    "Who does the step: exactly one key. role: any machine announcing that role (an AI agent or a program: worker, "
    "reviewer); program: a program this plugin runs per item (machines/<id>/machine.json); outside: a system outside "
    "(github, jira), a wait, nothing is offered; person: a person (later); outbox: a write as the owner, after their "
    "approval"), "properties": {k: S(f"The {k}'s name") for k in ("role", "program", "outside", "person", "outbox")}}
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
    "depends": {"type": "object", "description": "Not offered until each item this one depends on is at a step",
                "properties": {"at": S("That step's name, or end")}},
}, "required": ["name", "who"]}
DEFINITION = {"type": "object", "description": "The workflow, in the vocabulary's words and nothing else (no "
              "expressions or scripts: anything that needs logic is a machine)", "properties": {
    "name": S("The workflow's name: lowercase letters, digits, _ and - (task_loop); its folder's name"),
    "planner": S("The role that is told when an item needs attention (default: the settings' planner, coordinator)"),
    "starts": {"type": "array", "description": "What starts an item: [{\"request\": true}] (an item.requested naming "
               "it), [{\"event\": \"jira.issue.assigned\"}], or [{\"every\": \"1h\"}] (one item per interval)",
               "items": {"type": "object", "properties": {"request": B("true: a request naming the workflow starts it"),
                                                          "event": S("An event type that starts an item"),
                                                          "every": DURATION}}},
    "steps": {"type": "array", "items": STEP, "description": "The steps in order; the first is where an item starts"},
    "piles": {"type": "array", "description": "Declared piles beyond each step's own (<step>.waiting, <step>.held)",
              "items": {"type": "object", "properties": {
                  "name": S("The pile's name"),
                  "where": {"type": "object", "description": "Predicates, all to hold: step, held, set, unset, equals, "
                            "contains, seen, unseen, open_child, unmet_dependency, closed"}}}},
    "lifecycle": {"type": "object", "properties": {"keep_final": B("Keep the final snapshot once closed (default true)")}},
    "settings": {"type": "object", "description": "Parameters a project fills in [workflows.settings.<name>]: "
                 "{\"label\": {\"type\": \"string\", \"default\": \"SAIL\"}}"},
}, "required": ["name", "steps"]}

WORKFLOW_TOOLS: list[ToolDef] = [
    ToolDef("declare_workflow", "workflows",
            "Write a workflow the owner describes (the Workflows plugin's definition): the plugin's checks first; with any "
            "problem nothing is written and every problem is said; otherwise it is written to the state directory (or the "
            "repository's .orchestra/ when the owner says so) and the answer says what the project still lacks for it. "
            "The Items page shows its items, and the Dynamics page its piles. Never write a definition file any other way.",
            {"definition": DEFINITION,
             "root": E(["state", "repository"], "state (default): the state directory, never committed; repository: "
                                                "the repository's .orchestra/, only when the owner says so (quote them)"),
             "quote": QUOTE, "reason": REASON}, ("definition", "reason")),
    ToolDef("list_workflows", "workflows",
            "The project's workflows as the plugin loads them: each with where it is defined, its planner, starts and "
            "steps (who, needs, outcomes, guards), and the loader's problems (definitions left out) and notes (shadowed)."),
    ToolDef("explain_item", "workflows",
            "Why an item is where it is: its summary, each field with the fact that set it, its position in each workflow "
            "(step, pile, wait reason), the facts not counted toward a step and why, and its last facts.",
            {"item": S("The item's id (its subject: ORC-12, task_loop@2026-11-01T00:00Z)"),
             "last": I("How many of its latest facts to show (default 15)")}, ("item",)),
]
