"""The ``design`` tools' definitions (machine-design.md §2), part of the one catalogue in
``tools.py``, which appends them: kept here only so that file stays readable. What each does is
``orchestra_agents/design*.py``."""

from __future__ import annotations

from .tools import OS, A, B, O, S, ToolDef

REASON = S("Why, in a sentence: recorded with the change and shown on the page")
QUOTE = S("The owner's own words from their message this turn, exactly (… between parts you skip). With a quote the "
          "change is the owner's (\"added by you\"); without one it is your proposal. Never invent one")
REQUEST = S("When you answer the owner's click on a page (orchestra.intent.requested), its request id")
ELEMENT = S("A declared stock or effect's id (features), or part:id (stock:features, machine:feature_count_improver)")
EXPECT = OS("Each in SPEC's expect kinds, rates where you can: {flow, factor}, {from, to, appears: true} (from null: from "
            "outside), {stock, below|above: x}, {stock, rises|falls: true}. A declared stock with no series waits for one")
OPTIONS = OS("The ways to measure it, best first, the simulated one last (three or four honest ones): {label, kind "
             "(existing_series | script | scrape | source | counters | simulated), how (where it is read and what is counted), "
             "cost (how long a round takes, what it reads; for a store its request rate against the store's limit), caveat?, "
             "series? (an existing one's name), system? (jira, github, prometheus, jenkins, ...: a company system needs the "
             "owner's approval before anything reads it), bearer_env? (the NAME of the environment variable holding its "
             "token, never a value)}")
ENGAGEMENT = O("{mode: ad hoc | continuous | scheduled, at?: \"00:00\", tz?: \"America/Los_Angeles\", missed?: skip | run once} "
               "(at, tz, missed only when scheduled)")
SPEC = O("The kind's fields. universal: {invariants: [lines a reviewer can check], steps: [numbered, naming each event "
         "where it is used], uses: [what it may act on]}, in the work's terms: no model, prompt or tool names. programmatic, "
         "ad hoc or scheduled: {command | trigger (external: a remote call that starts it), arguments, environment, stdin, "
         "exit}; continuous: {command, protocol (http, graphql, grpc, other), send, receive}; managed external adds {source: "
         "the relayed actor whose events it is, never the bridge}")
EDITS = OS("Applied together or not at all, each recorded with the value it replaces: {op: add_receive|remove_receive|"
           "add_emit|remove_emit, type}, {op: set_engagement, mode, at?, tz?, missed?}, {op: set_field, field (title, "
           "description, tags, managed, spec.<field>), value}, {op: rewrite_steps, steps}, {op: add_invariant|"
           "remove_invariant, text}, {op: add_read|remove_read, element}, {op: set_kind, kind}")

DESIGN_TOOLS: list[ToolDef] = [
    ToolDef("declare_stock", "design",
            "Declare a stock the owner expects to accumulate, measured or not yet (\"I'm going to create a Features stock\"). "
            "The Dynamics page draws it at once as a ghost in the Expected group until a series is found for it. Then find "
            "how to measure it (propose_measurements) and start recording (add_instrument) in the same turn.",
            {"name": S("In the owner's words: Features"), "unit": S("features, tickets, tests, ..."), "near": S("A stock it is drawn beside (DONE, or a key); \"where you "
                                                                  "said it belongs\" only when your quote names it"),
             "quote": QUOTE, "reason": REASON}, ("name", "reason")),
    ToolDef("declare_effect", "design",
            "Declare an effect: a measurable consequence of the system nobody owns (latency, error budget, review backlog "
            "age, on-call load). Drawn like a declared stock.",
            {"name": S("In the owner's words"), "unit": S(""),
             "near": S("A stock it is drawn beside"), "quote": QUOTE, "reason": REASON}, ("name", "reason")),
    ToolDef("set_target", "design",
            "A target on a stock or effect (a level) or a flow (a rate), judged on its mean over a window: met, missed or "
            "unknown on the page (\"land five a day\" is a flow target).",
            {"element": ELEMENT, "op": {"type": "string", "enum": ["below", "above", "within"]},
             "value": {"type": "number"}, "hi": {"type": "number", "description": "The upper bound, for within"},
             "over": S("The window its mean is judged over: 1h, 1d, 7d"), "quote": QUOTE, "reason": REASON},
            ("element", "op", "value", "over", "reason")),
    ToolDef("propose_measurements", "design",
            "Record the ways to measure a declared stock or effect, in order, after you looked: the store (list_metrics, "
            "read_series), the repository's own reports (a CI JUnit report through orchestra.instruments.test_reports; "
            "never run a suite in the working tree), other plugins' counters, another store or company system "
            "(check_sources), and a simulated placeholder last. It only records; the page shows them as \"How to measure "
            "it\". An existing series needs nothing added: discovery relates it on its own.",
            {"element": ELEMENT, "options": OPTIONS, "quote": QUOTE, "reason": REASON}, ("element", "options", "reason")),
    ToolDef("add_instrument", "design",
            "Start recording one option at once: a script of at most 100 lines you write (to the state directory, "
            "instruments/<name>.sh), or a tool's command, or a URL; tried first exactly as Orchestra runs it, then its "
            "[[collect]] or [[scrape]] entry goes in the owner's private overlay, one act. Refused over 100 lines, with a "
            "line that looks like a secret, or when the trial fails (with its output); nothing is written then. It records "
            "as a draft that expires in 24 h unless the owner keeps it (keep_instrument). where: repo hands it to the "
            "Coordinator (a ticket and a pull request) instead.",
            {"element": ELEMENT, "option": S("The option's key from propose_measurements (a, b, ...)"),
             "name": S("The instrument's name: letters, digits, . _ - (features.acceptance)"),
             "kind": {"type": "string", "enum": ["collect", "scrape", "source"]},
             "script": S("The script's text (sh unless it starts with #!), printing Prometheus text: name value per line"),
             "command": S("Or the command of a tool already there (python -m orchestra.instruments.test_reports ...)"),
             "url": S("For scrape: the URL serving Prometheus text"), "every": S("How often: 15m, 60s"),
             "simulated": B("A simulated placeholder (its entry carries simulated = true)"),
             "where": {"type": "string", "enum": ["state", "repo"], "description": "state (default): written here; repo: through "
                       "the Coordinator, on the owner's say-so"},
             "wait_s": {"type": "integer", "description": "Wait up to this long (at most 120) for its first round"},
             "quote": QUOTE, "reason": REASON}, ("element", "option", "name", "reason")),
    ToolDef("keep_instrument", "design",
            "Keep a draft instrument (on the owner's word, or after their expect click): it stops expiring and the option "
            "shows chosen.", {"name": S("The instrument's name"), "quote": QUOTE, "reason": REASON}, ("name", "reason")),
    ToolDef("mark_expected", "design",
            "Mark a discovered stock or flow expected (\"yes, track that one\"): always drawn, with the pin; a ghost when a "
            "version leaves it out.",
            {"part": {"type": "string", "enum": ["stock", "flow"]}, "key": S("Its key, as read_design or read_model gives it"),
             "quote": QUOTE, "reason": REASON}, ("key", "reason")),
    ToolDef("ignore", "design", "Ignore a discovered suggestion: it moves to the collapsed \"ignored\" group; nothing is hidden.",
            {"part": {"type": "string", "enum": ["stock", "flow"]}, "key": S("Its key"), "quote": QUOTE,
             "reason": REASON}, ("key", "reason")),
    ToolDef("link", "design",
            "The owner says a declared stock is a discovered one (\"yes, that's Features\"): a manual binding, origin "
            "declared, drawn as an assumption until fitted. It never renames the discovered stock.",
            {"declared": ELEMENT, "discovered": S("The discovered stock's (or series') key"), "quote": QUOTE, "reason": REASON},
            ("declared", "discovered", "reason")),
    ToolDef("declare_machine", "design",
            "Declare a machine by name (\"Create a new Universal Machine called Feature Count Improver\"): its envelope and "
            "its kind's fields, every guess named in guesses. It stays unconfirmed (dashed on the Machines page) and runs "
            "never until the owner confirms. Read the map first (read_design, read_events, read_model); use existing event "
            "types where one has the purpose. A Universal one is drafted under drafts/<name>/; say its cost.",
            {"name": S("Its bus source, or the title (\"Feature Count Improver\" becomes feature_count_improver)"),
             "title": S("Feature Count Improver"), "kind": {"type": "string", "enum": ["universal", "programmatic"]},
             "description": S("What it achieves, in the work's terms"), "tags": A(),
             "managed": {"type": "string", "enum": ["orchestra", "external"], "description": "orchestra (default): started and "
                         "kept here; external: runs elsewhere, known by its events"},
             "engagement": ENGAGEMENT, "receives": A("Event types it wakes on or reads"), "emits": A("Event types it publishes"),
             "reads": A("Stocks and effects it may read (never one it produces)"), "spec": SPEC,
             "guesses": A("The fields you guessed, whole or one item: engagement, receives[0], ..."), "quote": QUOTE, "reason": REASON},
            ("name", "kind", "description", "reason")),
    ToolDef("refine_machine", "design",
            "Change a declared machine while the owner talks (\"watch DONE and REJECTED tickets\", \"make it 12am every day\"): "
            "each edit recorded with the value it replaced, all or none. With the owner's words quoted it applies at once on "
            "an unconfirmed machine; otherwise, or on a confirmed one, it is a proposed change the owner confirms.",
            {"name": S("The machine"), "edits": EDITS, "quote": QUOTE, "reason": REASON}, ("name", "edits", "reason")),
    ToolDef("declare_event", "design",
            "Declare an event type's purpose (a new type in an existing family, when no observed one has the purpose). "
            "Never one nothing would publish.",
            {"type": S("family.name: work.improvement_proposed"), "purpose": S("What it means, in a sentence"),
             "quote": QUOTE, "reason": REASON}, ("type", "purpose", "reason")),
    ToolDef("declare_expected_effect", "design",
            "A machine's expected effect, as a draft prediction: what it should change, judged after a horizon of at least "
            "three of its runs. Published as your prediction only when the owner confirms the machine.",
            {"machine": S("The machine"), "expect": EXPECT, "horizon": S("3d, 7d"), "quote": QUOTE, "reason": REASON},
            ("machine", "expect", "horizon", "reason")),
    ToolDef("confirm", "design",
            "The owner confirmed (\"confirm it\", quoted, or their click: request). A machine is declared: a Universal one's "
            "spec moves to the state directory's machines/, its expected effect is published; then create_role and "
            "create_agent. With change, a pending change is applied.",
            {"element": ELEMENT, "change": S("A pending change's id, to confirm only that"), "request": REQUEST, "quote": QUOTE,
             "reason": REASON}, ("element", "reason")),
    ToolDef("discard", "design",
            "The owner discarded it: gone from the page, kept in history; a draft leaves no file.",
            {"element": ELEMENT, "change": S("A pending change's id, to discard only that"), "request": REQUEST, "quote": QUOTE,
             "reason": REASON}, ("element", "reason")),
    ToolDef("read_design", "observe",
            "The declared layer in one read: every declared and proposed element with its state, link and guessed fields, "
            "each machine's fields, the measurement options and draft instruments, the open items (unconfirmed proposals, "
            "pending changes, unanswered requests), the last changes, and the refinement pass's latest proposal."),
]
