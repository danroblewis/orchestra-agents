"""The ``design`` tools' definitions (machine-design.md §2), part of the one catalogue in
``tools.py``, which appends them: kept here only so that file stays readable. What each does is
``orchestra_agents/design*.py``. The schemas are narrow and flat where they can be, every nested
object with its properties and enums, so a small model (Haiku) fills them without guessing."""

from __future__ import annotations

from .tools import A, B, S, ToolDef

REASON = S("Why, in a sentence: recorded with the change and shown with it")
QUOTE = S("The owner's own words from their message this turn, exactly, at least three words (or their whole message; … "
          "between parts you skip). With a quote the change is the owner's (\"added by you\"); without one it is your "
          "proposal. Never invent one")
REQUEST = S("When you answer the owner's click on a page (orchestra.intent.requested), its request id: the event names the "
            "control and the element it backs")
ELEMENT = S("A declared element's id (features, feature_count_improver), or part:id (stock:features, "
            "machine:feature_count_improver)")
NUM = {"type": "number"}


def E(values: list[str], d: str = "") -> dict:
    return {"type": "string", "enum": values, **({"description": d} if d else {})}


MODE = E(["ad hoc", "continuous", "scheduled"], "How it is engaged: ad hoc (when asked or woken), continuous (always up, woken "
                                                "by its receives), scheduled (once a day at `at`)")
AT = S("For scheduled: the time of day, HH:MM (00:00 is midnight)")
TZ = S("For scheduled: the owner's time zone (America/Los_Angeles), from their message; none configured, so leave it out "
      "and ask them when they have not said one. Never UTC or the host's zone as a guess")
RECEIVES_D = ("Event types (family.name) it watches, or reads at each run (a scheduled machine too): what the owner says it "
              "watches goes here, never only in steps or uses. Only types a machine speaks: heard on the bus or announced "
              "(read_events: by_type, announced_not_heard), or emitted by a declared machine")
EMITS_D = ("Event types it publishes: one heard or announced (read_events), or one you declare first (declare_event); "
           "never an invented name")
MISSED = E(["skip", "run once"], "For scheduled: a run missed while the host was down is skipped or run once")
OPTION = {"type": "object", "properties": {
    "label": S("A few words: acceptance tests passing on main"),
    "kind": E(["existing_series", "script", "scrape", "counters", "simulated", "source"],
              "existing_series: already recorded (nothing is added); script: one you write; scrape: a URL serving "
              "Prometheus text; counters: another plugin's counters; simulated: a placeholder, always last; source: another "
              "store or company system"),
    "how": S("Where it is read and what is counted"),
    "cost": S("How long a round takes and what it reads; for a store, its request rate against the store's limit"),
    "caveat": S("What it gets wrong, if anything"),
    "series": S("For existing_series: the series' name"),
    "system": S("The company system it reads, if any (jira, github, prometheus, jenkins): it then needs the owner's approval"),
    "bearer_env": S("The NAME of the environment variable holding its token (WORK_TOKEN), never a value"),
    "url": S("For scrape: the URL it reads; add_instrument runs only this"),
    "command": S("For a command already there (python -m orchestra.instruments.test_reports ...): add_instrument runs only this"),
}, "required": ["label", "kind", "how", "cost"]}
EXPECT = {"type": "object", "properties": {
    "kind": E(["factor", "below", "above", "rises", "falls", "appears"],
              "factor: a flow's rate times factor (needs flow, factor); below/above: a stock's level (needs stock, value); "
              "rises/falls: a stock's slope (needs stock); appears: a new flow into `to` (needs to; from, or outside)"),
    "flow": S("For factor: a flow's id from read_model (a rate: the flow into the stock), never a stock's key"), "factor": {"type": "number", "description": "For factor: 1.2 is 20% more"},
    "stock": S("For below, above, rises, falls: a stock's key or a declared stock's id (one with no series waits for one)"),
    "value": {"type": "number", "description": "For below and above: the level"},
    "to": S("For appears: the stock the new flow goes into"), "from": S("For appears: where it comes from (leave out: outside)"),
}, "required": ["kind"]}
# the kind's fields, flat on declare_machine (in the work's terms: a Universal one names no model, prompt or tool)
SPEC_FIELDS = {
    "invariants": A("Universal: lines a reviewer can check (never changes code itself; it proposes work)"),
    "steps": A("Universal: numbered, naming each event where it is used"),
    "uses": A("Universal: what it may act on (the ticket queue, read only)"),
    "command": S("Programmatic: what runs it"),
    "trigger": S("Programmatic, managed external: the remote call that starts it (a Jenkins job's URL)"),
    "arguments": S("Programmatic, ad hoc or scheduled: its arguments"),
    "environment": S("Programmatic, ad hoc or scheduled: the environment it needs"),
    "stdin": S("Programmatic, ad hoc or scheduled: what it reads on stdin"),
    "exit": S("Programmatic, ad hoc or scheduled: what its exit code means"),
    "protocol": E(["http", "graphql", "grpc", "other"], "Programmatic, continuous: how it is spoken to"),
    "send": S("Programmatic, continuous: what it sends"),
    "receive": S("Programmatic, continuous: what it receives (push, or a poll and its interval)"),
    "role": S("Programmatic, ad hoc, answering a workflow's offers: the role whose offers it answers"),
    "source": S("Managed external: the relayed actor whose events it is (bot.jira), never the bridge"),
}
SPEC = {"type": "object", "description": "Older form, kept for one release: the same fields nested. Give them at the top level",
        "properties": SPEC_FIELDS}

INT = {"type": "integer"}
INTEGRATION_SETTINGS = {"type": "object", "description": "The program's own settings, from the owner's words (only what they "
                        "said; the kit's defaults stand otherwise). Never a token", "properties": {
    "jql": S("Jira: the issues in scope (project = OPS), no ORDER BY"), "channels": A("Slack: channel ids (C0123), not names"),
    "open_statuses": A("PagerDuty: the statuses counted open"), "every": S("How often it polls: 5m (the default)"),
    "per_minute": INT, "ttl": S("How long a cached answer is served: under every (4m)"),
    "lookback": S("How far back the first poll reads (1d)"), "thing": S("http kit: what one item is (build)"),
    "list_path": S("http kit: the list endpoint (/api/builds)"),
    "match": A("source: the PromQL selectors to read ({__name__=~\"http_requests_total\"})"),
    "prefix": S("source: the series' prefix (chrono)"), "machine": S("source: the label naming machines (service)"),
    "max_series": INT, "rate": NUM, "max_requests_per_day": INT}}

DESIGN_TOOLS: list[ToolDef] = [
    ToolDef("declare_stock", "design",
            "Declare a stock the owner expects to accumulate, measured or not yet (\"I'm going to create a Features stock\"). "
            "Then find how to measure it (propose_measurements) and start recording (add_instrument) in the same turn.",
            {"name": S("In the owner's words: Features"), "description": S("What the owner says it means, in a sentence"),
             "unit": S("features, tickets, tests, ..."),
             "near": S("A stock it is drawn beside (DONE, or a key); \"where you said it belongs\" only when your quote names it"),
             "quote": QUOTE, "reason": REASON}, ("name", "reason")),
    ToolDef("declare_effect", "design",
            "Declare an effect: a measurable consequence of the system nobody owns (latency, error budget, review backlog "
            "age, on-call load). Kept like a declared stock.",
            {"name": S("In the owner's words"), "description": S("What the owner says it means, in a sentence"),
             "unit": S("What it is counted in: seconds, percent, tickets"),
             "near": S("A stock it is drawn beside"), "quote": QUOTE, "reason": REASON}, ("name", "reason")),
    ToolDef("set_target", "design",
            "A target on a stock or effect (a level) or a flow (a rate), judged on its mean over a window: met, missed or "
            "unknown (\"land five a day\" is a flow target).",
            {"element": ELEMENT, "compare": E(["below", "above", "within"]), "value": NUM,
             "hi": {"type": "number", "description": "The upper bound, for within"},
             "over": S("The window its mean is judged over: 1h, 1d, 7d"), "quote": QUOTE, "reason": REASON},
            ("element", "compare", "value", "over", "reason")),
    ToolDef("propose_measurements", "design",
            "Record the ways to measure a declared stock or effect, in order, after you looked. Refused until you called "
            "list_metrics in this session: a series already in the store (existing_series, its series named) always beats a "
            "script, and is option a; when that series is a discovered stock (read_model), link the declared one to it. Then "
            "the repository's own reports (a CI JUnit report through orchestra.instruments.test_reports; "
            "never run a suite in the working tree), other plugins' counters, another store or company system (named and "
            "costed only: never previewed until the owner clears it), and a simulated placeholder last. It only records. "
            "Whether one needs the owner's approval is decided from what it reads, not from what you say.",
            {"element": ELEMENT, "options": {"type": "array", "items": OPTION, "description": "Best first, the simulated one last "
                                             "(three or four honest ones)"}, "quote": QUOTE, "reason": REASON},
            ("element", "options", "reason")),
    ToolDef("add_instrument", "design",
            "Start recording one option at once (never an existing_series one: link that instead), its option chosen: a script of at most 100 lines you write (to the state directory, "
            "instruments/<name>.sh), or the command or URL the option proposed; tried first exactly as Orchestra runs it, then "
            "its [[collect]] or [[scrape]] entry goes in the owner's private overlay, one act. Refused over 100 lines, with "
            "what looks like a secret, for an option that needs approval or reaches another host, or when the trial fails "
            "(with its output); nothing is written then. It records as a draft that expires in 24 h unless the owner keeps it "
            "(keep_instrument). where: repo tries it, then hands it to the Coordinator for .orchestra/instruments/<name>.sh.",
            {"element": ELEMENT, "option": S("The option's key from propose_measurements (a, b, ...)"),
             "name": S("The instrument's name: letters, digits, . _ - (features.acceptance)"),
             "script": S("The script's text (sh unless it starts with #!), printing Prometheus text: name value per line"),
             "command": S("Only the command the option proposed, if it proposed one"),
             "url": S("Only the URL the option proposed, if it proposed one"), "every": S("How often: 15m, 60s"),
             "simulated": B("A simulated placeholder (its entry carries simulated = true)"),
             "where": E(["state", "repo"], "state (default): written here; repo: through the Coordinator, on the owner's say-so"),
             "wait_s": {"type": "integer", "description": "Wait up to this long (at most 120) for its first round"},
             "quote": QUOTE, "reason": REASON}, ("element", "option", "name", "reason")),
    ToolDef("keep_instrument", "design",
            "Keep a draft instrument the owner chose: quote their words, or call it after their expect click on its series. "
            "It stops expiring, its draft flag comes off (discovery reads it from then) and the option is chosen.",
            {"name": S("The instrument's name"), "quote": QUOTE, "reason": REASON}, ("name", "reason")),
    ToolDef("mark_expected", "design",
            "Mark a discovered stock or flow expected (\"yes, track that one\"): always drawn, with the pin; a ghost when a "
            "version leaves it out.",
            {"part": E(["stock", "flow"]), "key": S("Its key, as read_design or read_model gives it"),
             "quote": QUOTE, "reason": REASON}, ("key", "reason")),
    ToolDef("ignore", "design", "Ignore a discovered suggestion: it moves to the collapsed \"ignored\" group; nothing is hidden.",
            {"part": E(["stock", "flow"]), "key": S("Its key"), "quote": QUOTE, "reason": REASON}, ("key", "reason")),
    ToolDef("unexpect", "design",
            "Take a discovered stock's or flow's expected mark off (the owner no longer expects it): drawn as discovery found it.",
            {"part": E(["stock", "flow"]), "key": S("Its key, as read_design gives it"), "quote": QUOTE, "reason": REASON},
            ("key", "reason")),
    ToolDef("unignore", "design",
            "List an ignored suggestion again (the page's \"to list it again, ask …\"): it leaves the ignored group.",
            {"part": E(["stock", "flow"]), "key": S("Its key, as read_design gives it"), "quote": QUOTE, "reason": REASON},
            ("key", "reason")),
    ToolDef("alias", "design",
            "Join a declared machine to the name it is heard under on the bus (the Machines panel's \"heard under another "
            "name? … join them\"): one machine, heard as that source.",
            {"machine": S("The declared machine's id"), "observed": S("The source it is heard as on the bus (bot.pagerduty)"),
             "quote": QUOTE, "reason": REASON}, ("machine", "observed", "reason")),
    ToolDef("propose_attachment", "design",
            "Propose that an instrument's series measure a declared machine (instruments on machines): declared provenance, "
            "never a sign it runs. Read the store first (list_metrics) and give the match explicitly; the owner confirms it "
            "on the Machines page.",
            {"machine": S("The declared machine's id"), "instrument": S("The instrument whose series measure it (company_prom)"),
             "prefix": S("Which of its series: a series-name prefix (pg_)"),
             "labels": A("Which of its series: label=value (job=postgres); a value starting =~ is a regex"),
             "instance_label": S("The label whose values are its replicas (instance), if any"),
             "quote": QUOTE, "reason": REASON}, ("machine", "instrument", "reason")),
    ToolDef("revise_attachment", "design",
            "Change a proposed or confirmed attachment's instrument, match or instance label (a confirmed one's change waits "
            "for the owner).",
            {"attachment": S("The attachment's id, as read_design gives it"), "instrument": S("Another instrument"),
             "prefix": S("A new series-name prefix"), "labels": A("New label=value matches"),
             "instance_label": S("The label whose values are its replicas"), "quote": QUOTE, "reason": REASON},
            ("attachment", "reason")),
    ToolDef("link", "design",
            "A declared stock is a discovered one: the owner says so (\"yes, that's Features\"), or the series you measured it "
            "with is that stock (read_model); yours to call, quoted or as your proposal. A manual binding, origin "
            "declared, an assumption until fitted; it is how a declared stock's expected effects can be judged. It never renames the discovered stock. A confirmed machine's expected "
            "edits that waited for it are published then.",
            {"declared": ELEMENT, "discovered": S("The discovered stock's (or series') key"), "quote": QUOTE, "reason": REASON},
            ("declared", "discovered", "reason")),
    ToolDef("declare_machine", "design",
            "Declare a machine by name (\"Create a new Universal Machine called Feature Count Improver\"): its envelope and "
            "its kind's fields (invariants, steps, uses for a Universal one; command and the rest for a Programmatic one), "
            "every guess named in guesses. It stays unconfirmed and never runs until the owner confirms. "
            "Read the map first (read_design, read_events, read_model); use existing event types where one has the purpose. "
            "A Universal one is drafted under drafts/<name>/; say its cost.",
            {"name": S("Its bus source, or the title (\"Feature Count Improver\" becomes feature_count_improver)"),
             "title": S("Feature Count Improver"), "kind": E(["universal", "programmatic"]),
             "description": S("What it achieves, in the work's terms"), "tags": A(),
             "managed": E(["orchestra", "external"], "orchestra (default): started and kept here; external: runs elsewhere, "
                                                     "known by its events"),
             "mode": MODE, "at": AT, "tz": TZ, "missed": MISSED,
             "receives": A(RECEIVES_D), "emits": A(EMITS_D),
             "reads": A("Stocks and effects it may read (never one it produces)"), **SPEC_FIELDS, "spec": SPEC,
             "guesses": A("The fields you guessed, whole or one item: engagement (mode, at, tz), receives[0], ..."), "quote": QUOTE, "reason": REASON},
            ("name", "kind", "description", "reason")),
    ToolDef("refine_machine", "design",
            "Change a declared machine while the owner talks (\"watch DONE and REJECTED tickets\", \"make it 12am every day\"): "
            "give only what changes; each change is recorded with the value it replaced, all or none. With the owner's words "
            "quoted it applies at once on an unconfirmed machine; otherwise, or on a confirmed one, it is a proposed change "
            "the owner confirms. The Universal | Programmatic switch is switch_kind.",
            {"name": S("The machine"),
             "add_receives": A("Event types to add to what it watches: " + RECEIVES_D),
             "remove_receives": A("Event types to take out of its receives"),
             "add_emits": A(EMITS_D), "remove_emits": A("Event types it will no longer publish"),
             "add_reads": A("Stocks or effects it may read"), "remove_reads": A("Stocks or effects it no longer reads"),
             "add_invariants": A("Lines a reviewer can check"), "remove_invariants": A("Invariants to drop, word for word"),
             "add_uses": A("What it may act on, in the work's terms (the ticket queue, read only)"),
             "remove_uses": A("Uses to drop, word for word"), "add_tags": A("Tags to add"), "remove_tags": A("Tags to drop"),
             "steps": A("The whole new numbered list of steps"),
             "mode": MODE, "at": AT, "tz": TZ, "missed": MISSED,
             "set_field": E(["title", "description", "spec.command", "spec.trigger", "spec.arguments", "spec.environment",
                             "spec.stdin", "spec.exit", "spec.protocol", "spec.send", "spec.receive", "spec.source", "spec.role"],
                            "A text field to set to value (the lists have their add_ and remove_ edits)"),
             "value": S("The new text of set_field"), "quote": QUOTE, "reason": REASON}, ("name", "reason")),
    ToolDef("switch_kind", "design",
            "The Universal | Programmatic switch, on the owner's click (request) or their words (quote): the kind changes "
            "and the spec is kept (to Programmatic, the Universal description is the program's specification). On a "
            "confirmed machine switched to Programmatic, its agent stops and the program goes to the Coordinator.",
            {"name": S("The machine"), "request": REQUEST, "quote": QUOTE, "reason": REASON}, ("name", "reason")),
    ToolDef("declare_event", "design",
            "Declare an event type's purpose (a new type in an existing family, when no observed one has the purpose). "
            "Never one nothing would publish.",
            {"type": S("family.name: work.improvement_proposed"), "purpose": S("What it means, in a sentence"),
             "quote": QUOTE, "reason": REASON}, ("type", "purpose", "reason")),
    ToolDef("declare_expected_effect", "design",
            "A machine's expected effect, as a draft prediction: what it should change, rates where you can, judged after a "
            "horizon of at least three of its runs. Read the model first (read_model): a rate (\"the commit rate up 1.2 times\") "
            "is factor on a flow's id; a level is a stock (rises, falls, above, below). A stock named as a flow is refused. Published as your prediction only when the owner confirms the machine.",
            {"machine": S("The machine"), "expect": {"type": "array", "items": EXPECT, "description": "One object per edit, "
                                                     "each with its kind and the fields that kind needs"},
             "horizon": S("3d, 7d"), "quote": QUOTE, "reason": REASON},
            ("machine", "expect", "horizon", "reason")),
    ToolDef("confirm", "design",
            "The owner confirmed (\"confirm it\", quoted, or their click: request). A stock, effect or a pending change is "
            "declared; a Universal Machine is declared and set running: its spec, role and agent records written to the "
            "state directory's machines/, its expected effect published.",
            {"element": ELEMENT, "change": S("A pending change's id, to confirm only that"), "request": REQUEST, "quote": QUOTE,
             "reason": REASON}, ("element", "reason")),
    ToolDef("discard", "design",
            "The owner discarded it (quoted, or their click: request): kept only in the history; a draft, and a discarded "
            "stock's instruments, leave no file.",
            {"element": ELEMENT, "change": S("A pending change's id, to discard only that"), "request": REQUEST, "quote": QUOTE,
             "reason": REASON}, ("element", "reason")),
    ToolDef("integrate_system", "design",
            "The first call when the owner asks to connect an outside system (\"connect our Jira so I can see issue "
            "transitions and open counts\"), before reading any guide. It follows Orchestra's INTEGRATING.md: a poller built from the bridges' kit (events and series), a measurement alone "
            "(collect), a [[sources]] entry for a Prometheus-API store (source), or the bridges' own reader (bridge). A poller "
            "is scaffolded into the state directory and tested against the kit's fake, and the system is declared on the "
            "Machines page as a machine managed elsewhere. Nothing reads the real system until the owner approves: put the "
            "answer's ask_owner to them, then approve_integration.",
            {"system": S("The system's name: jira, slack, pagerduty, chronosphere, jenkins"),
             "wants": S("What the owner wants from it, in a sentence (issue transitions and open counts)"),
             "strategy": E(["poller", "collect", "source", "bridge"],
                           "Leave out for INTEGRATING.md's default: source for a Prometheus-API store, poller otherwise"),
             "name": S("The poller's name (default <system>_poller); never the system's own name"),
             "kit": E(["jira", "slack", "pagerduty", "promql", "http"], "Leave out: the system's own kit, else http"),
             "base_url": S("The system's address (its API's root URL), when the owner gave it"),
             "settings": INTEGRATION_SETTINGS,
             "stock": S("The stock the owner expects it to fill, in their words (Open issues): declared, with the system's "
                        "expected effect"),
             "unit": S("The stock's unit (issues)"), "quote": QUOTE, "reason": REASON},
            ("system", "wants", "reason")),
    ToolDef("approve_integration", "design",
            "The owner approved reading the system (their yes, quoted) and named the environment variable holding its token: "
            "the clearance and the variable's name are recorded, and what runs it is written to the owner's overlay, so the "
            "program reads the real system from then on. Never pass or ask for the token itself.",
            {"name": S("The integration's name, from integrate_system's answer"),
             "token_env": S("The NAME of the environment variable holding the token (JIRA_TOKEN), never its value"),
             "data_class": S("What the owner cleared it for (default: metadata only)"),
             "base_url": S("The system's address, when it was not given before"),
             "settings": INTEGRATION_SETTINGS, "quote": QUOTE, "reason": REASON},
            ("name", "token_env", "quote", "reason")),
    ToolDef("read_design", "observe",
            "The declared layer in one read: every declared and proposed element with its state, link and guessed fields; "
            "each machine's fields; each event type's declared publishers and listeners and who was heard publishing it or "
            "announced listening; the open items (unconfirmed proposals, pending changes, unanswered requests, unpublishable "
            "drafts); the last changes; the draft instruments; and the refinement pass's latest proposal."),
]
