"""The ``workflows`` tools' definitions (orchestra-workflows' SPEC §7.1, §10), and ``request_work``
(the ``work`` group), part of the one catalogue in ``tools.py``, which appends them. What each does
is ``orchestra_agents/workflow_tools.py`` and ``request_work.py``.
The definition's schema spells out the vocabulary's every key, with what it means, so a small model
(Haiku) writes it without guessing; the plugin's own checks are still the judge."""

from __future__ import annotations

from .tools import A, B, I, S, ToolDef
from .tools_design import QUOTE, REASON, E

DURATION = S("A duration: 30m, 2h, 1d")
WITHIN = {"description": (
    "How long the item may stay at the step: a duration (\"2h\"); a setting holding one ({\"setting\": "
    "\"check_within\"}); or the setting named by a field of the event that entered the step ({\"setting\": "
    "{\"data\": \"channel_name\"}}: each channel its own reply policy, one setting per channel)"),
    "anyOf": [DURATION, {"type": "object", "properties": {"setting": {"anyOf": [
        S("The setting's name"), {"type": "object", "properties": {"data": S("The entering event's data field that "
                                                                             "names the setting")},
                                  "required": ["data"]}]}}, "required": ["setting"]}]}
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
               "properties": {"within": WITHIN, "rounds": I("At most this many entries into the step (review rounds)"),
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
    "items": {"description": "What the workflow's items are, one and many: a word (\"delivery\") or {\"one\": \"release candidate\", \"many\": \"release candidates\"}; the piles' unit and the pages' counts; default item/items",
              "anyOf": [{"type": "string"}, {"type": "object", "properties": {"one": S("The singular"), "many": S("The plural")}}]},
    "planner": S("The role that is told when an item needs attention (default: the settings' planner[[tasking]], coordinator[[/tasking]])"),
    "starts": {"type": "array", "description": "What starts an item: [{\"request\": true}] (an item.requested naming "
               "it), [{\"event\": \"jira.issue.assigned\", \"data\": {\"assignee\": {\"setting\": \"owner\"}}}] (an "
               "event, only when its data fields match), [{\"every\": \"1h\"}] (one item per interval, counted from "
               "00:00 UTC), or [{\"every\": \"24h\", \"at\": \"09:00\", \"tz\": \"America/Los_Angeles\"}] (each day at "
               "a time of day in a zone: the time the owner named, in the zone they gave or answer)",
               "items": {"type": "object", "properties": {
                   "request": B("true: a request naming the workflow starts it"),
                   "event": S("An event type that starts an item"),
                   "data": {"type": "object", "description": "With event: fields of its data that must equal a value or "
                            "a setting, or the event makes no item (only tickets assigned to the owner: {\"assignee\": "
                            "{\"setting\": \"owner\"}})"},
                   "every": DURATION,
                   "at": S("With every: the time of day its slots are counted from, HH:MM (09:00), the time the owner "
                           "named; left out, the slots count from 00:00 UTC"),
                   "tz": S("With every and at: the zone of at (America/Los_Angeles, UTC), the one the owner gave or "
                           "answered (their \"this machine's clock\", \"local\" or \"here\" as they said it: set to the host's "
                           "zone by name); left out, the project's local zone")}}},
    "steps": {"type": "array", "items": STEP, "description": "The steps in order; the first is where an item starts"},
    "piles": {"type": "array", "description": "Declared piles beyond each step's own (<step>.waiting, <step>.held)",
              "items": {"type": "object", "properties": {
                  "name": S("The pile's name"),
                  "where": {"type": "object", "description": "Predicates, all to hold: step, held, set, unset, equals, "
                            "contains, seen, unseen, open_child, unmet_dependency, closed"}}}},
    "settings": {"type": "object", "description": "Parameters a project fills in [workflows.settings.<name>]: "
                 "{\"label\": {\"type\": \"string\", \"default\": \"SAIL\"}}"},
}, "required": ["name", "steps"]}

ADOPT = {"type": ["object", "boolean", "null"], "description": (
    "For a workflow whose items are already under way when tracking begins (tickets assigned before the poller "
    "began are never relayed as assigned): which of the poller's events report a ticket as it is now, which fields "
    "say it is under way, and which step each status implies. Every such ticket the bus reports with no item yet "
    "is adopted (the Supervisor's item.requested, adopted: true, never a poller's event made up) at that step, now "
    "and when the poller first reports one later; a ticket a start refused (assigned to someone else) is not. "
    "A ticket board's: {\"event\": [\"jira.issue.transitioned\", \"jira.issue.created\", \"jira.comment.added\"], "
    "\"data\": {\"category\": {\"not\": [\"new\", \"done\"]}}, \"at\": {\"status\": {\"In Review\": \"review\"}}}. "
    "Left out on a re-declaration, the rule in place is kept; false drops it"),
    "properties": {"event": A("The poller's event types that carry the ticket as it is now (jira.issue.transitioned)"),
                   "data": {"type": "object", "description": "Fields of the report that say it is under way: a value "
                            "or {\"not\": [values]} ({\"category\": {\"not\": [\"new\", \"done\"]}})"},
                   "at": {"type": "object", "description": "One field and the step each value implies "
                          "({\"status\": {\"In Review\": \"review\"}}); any other value: the first step"}}}

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
            "pause before a retry; no who on a fan-in or an after step; depends for an item waiting on another. "
            "A ticket workflow takes adopt, so the tickets already under way are tracked too. A chat queue leaves out "
            "bot threads only on the owner's words about bots; in a channel the owner named for incidents or alerts "
            "the alert bot's threads start alerts (their own workflow, items alert, the owner's policy for that "
            "channel), and the answer's bot_threads says which channels count them.",
            {"definition": DEFINITION,
             "root": E(["state", "repository"], "state (default): the state directory, never committed; repository: "
                                                "the repository's .orchestra/, only when the owner says so (quote them)"),
             "adopt": ADOPT, "quote": QUOTE, "reason": REASON}, ("definition", "reason")),
    ToolDef("declare_program", "workflows",
            "Declare a program the Workflows plugin ships (relate_prs: relates each pull request to its ticket by the key in "
            "its branch or title) for a workflow's program step, on the owner's quoted yes: writes machines/<id>/machine.json "
            "in the state directory, which the plugin's program host runs each time the step is offered. The plugin's reader "
            "checks it; with a problem nothing stays written. A token only by its file's path (--token-file) or variable's "
            "name (--token-env), never its value. A workflow step whose program has no machine.json waits there: its goal is "
            "not running until this is done.",
            {"program": S("The shipped program: relate_prs"),
             "id": S("The program's id as the step names it (who: {program: <id>}); default: the program's name"),
             "args": A("The program's arguments, as words. relate_prs: --projects HK,PLAT (the Jira projects whose keys "
                       "count), --github <GitHub's API URL> and --token-file <the path the owner gave> to read titles"),
             "exit": {"type": "object", "description": "Exit code -> outcome; relate_prs's default: {\"0\": \"related\", "
                      "\"3\": \"unrelated\"}"},
             "timeout": S("A duration bounding one run (relate_prs: 1m)"),
             "role": S("The role it runs as (default: its id)"),
             "quote": QUOTE, "reason": REASON}, ("program", "quote", "reason")),
    ToolDef("list_workflows", "workflows",
            "The project's workflows as the plugin loads them: each with where it is defined, its planner, starts and "
            "steps (who, needs, outcomes, guards), and the loader's problems (definitions left out) and notes (shadowed)."),
    ToolDef("list_items", "workflows",
            "The open items of every workflow, as the Items page lists them, oldest first: each with its step and pile, "
            "why it waits and on whom (its wait: reason, why (an attention's own cause, to report as it says), by, until, "
            "and ends_on: every event that ends it), its holder, how "
            "long it has been there (age_s), how often it was reworked, and its fields (a ticket's key, a pull request's "
            "number); then the recently closed, with their last step and outcome. An item's end is item.closed (the floor "
            "publishes it when a workflow's item reaches the end of every workflow it entered): a closed item is never "
            "waiting. waiting_outside lists the open items at each step that waits on an outside system (CI, a review, "
            "SAIL): the ones an incident on that system's service holds. What to read first when the owner asks what is "
            "stuck or what needs them, and after an alert.",
            {"workflow": S("Only this workflow's items (leave out: every workflow)")}),
    ToolDef("explain_item", "workflows",
            "Why an item is where it is: its summary, each field with the fact that set it, its position in each workflow "
            "(step, pile, wait reason), the facts not counted toward a step and why, and its last facts.",
            {"item": S("The item's id (its subject: ORC-12, task_loop@2026-11-01T00:00Z)"),
             "last": I("How many of its latest facts to show (default 15)")}, ("item",)),
    ToolDef("retry_step", "workflows",
            "Ask the Workflows floor to offer an item's step again now, after you fixed what failed it: a step whose turn "
            "finished blocked or error (the floor retries it after 1, 5, 15 minutes, then hourly) or the step an item was "
            "moved to stalled from once its tries were spent. Only after the cause is fixed, never before: retried "
            "unfixed it fails again. Publishes item.retry_requested as you, the item's planner, with the reason; the "
            "floor enters the step again (tries counted from nothing) and offers it on its next tick. Refused, nothing "
            "published, for an item not blocked or stalled at that step. Tell the owner what you fixed.",
            {"item": S("The item's id (SCRUM-56), as list_items shows it"),
             "step": S("The step to offer again: the one it is blocked at, or the one it was moved to stalled from (write)"),
             "reason": S("What failed the step and what you fixed, in a sentence (git push refused over ssh: the ssh "
                         "agent's key is loaded again)")}, ("item", "step", "reason")),
    ToolDef("request_work", "work",
            "Start a piece of work on a workflow: publishes the Workflows plugin's item.requested (and an item.fact "
            "with the fields, if any). With role in place of workflow, a one-off item that role does once: code work is "
            "role worker with needs [\"worktree\"], made on the item's branch in a worktree, pushed only if allow_push "
            "names the role. The Supervisor asks only for the owner: quote their words, and the request goes "
            "out as theirs (source owner)[[tasking]]; the Coordinator's is its own, as the planner[[/tasking]]. Refused, nothing published: "
            "an unknown workflow or one a request does not start (list_workflows), an id already used, or the floor "
            "not running. Answers the item's id, the Items page link and how to follow it (explain_item).",
            {"workflow": S("The workflow's name, as list_workflows shows it (task_loop); or leave it out and give role"),
             "role": S("A one-off item's role, in place of workflow: worker for code work"),
             "needs": A("A one-off item's resource kinds: [\"worktree\"] for code work"),
             "brief": S("The work asked for, in the owner's terms: what the first step's machine is given"),
             "parent": S("The parent item's id, when this is part of a larger item"),
             "fields": {"type": "object", "description": "The item's fields to set at once, {name: value}: "
                        "{\"priority\": 2}"},
             "item": S("The new item's id (leave out: <workflow>-<6 hex>)"),
             "quote": S("The Supervisor's: the owner's own words asking for this work, from their message this turn, "
                        "exactly, at least three words (or their whole message). Required of the Supervisor; never "
                        "invent one.[[tasking]] The Coordinator leaves it out[[/tasking]]"),
             "reason": REASON}, ("brief", "reason")),
]
