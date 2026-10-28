"""The tools an agent gets, defined once as data.

Every harness exposes these to its model in its own way: the Pi extension registers them
as Pi tools, the MCP server (``orchestra_agents.harness.mcp``) serves them to Claude Code and any
other MCP client. Each tool maps to an ``orchestra_agents.tooling`` command. Which tools an agent
gets is decided by tool *groups* (``RoleDef.tools``), never per tool."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..protocol import STATES as _STATES

STATES = ", ".join(_STATES)


@dataclass(frozen=True)
class ToolDef:
    name: str                                         # also the ``orchestra_agents.tooling`` command
    group: str
    description: str
    properties: dict = field(default_factory=dict)   # JSON schema properties
    required: tuple[str, ...] = ()

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": dict(self.properties), "required": list(self.required),
                "additionalProperties": False}

    def to_dict(self) -> dict:
        return {"name": self.name, "group": self.group, "description": self.description, "inputSchema": self.input_schema}


def S(d: str) -> dict:
    return {"type": "string", "description": d}


def I(d: str = "") -> dict:
    return {"type": "integer", "description": d} if d else {"type": "integer"}


TARGET = {"type": "string", "enum": ["repo", "overlay"],
          "description": "repo (default): orchestra.toml, the project's settings, committed; overlay: the owner's "
                         "private file, for their own or their company's settings (identities, channels, templates), "
                         "never committed"}


def A(d: str = "") -> dict:
    s = {"type": "array", "items": {"type": "string"}}
    return {**s, "description": d} if d else s


def B(d: str) -> dict:
    return {"type": "boolean", "description": d}


def O(d: str) -> dict:
    return {"type": "object", "description": d}


def OS(d: str) -> dict:
    return {"type": "array", "items": {"type": "object"}, "description": d}


DURATION = S("A duration such as 90s, 30m, 2h or 1d (plain digits are seconds)")
EXPERIMENT = S("The experiment's id (x-...)")
RAW = B("Also give Orchestra's whole answer as JSON (trajectories, every number); default: the summary only")
WINDOW = {"span": S("The past window back from now, such as 30m, 2h or 1d (default 1h)"),
          "start": {"type": "number", "description": "Or the window's start, unix seconds"},
          "end": {"type": "number", "description": "And its end, unix seconds (default now)"},
          "era": S("Or a whole era, by its id (e1, ...): read_eras lists them")}
CHANGE = S("The change id commit_prediction gave you, when this change is the one you predicted: Orchestra judges "
           "the prediction from this announcement")
EDIT_OPS = ("Each edit is an object with an op: add_flow {id, from, to, equation, params} (from/to a stock id, or null for "
            "outside); replace_flow {id, equation, params, from?, to?} (the whole equation); remove_flow {id}; set_params "
            "{flow, params}; add_stock {id, initial} (a hidden stock); remove_stock {id}; set_level {stock, level}; set_input "
            "{input, scenario} (scenario: {kind: hold|value|scale|step|ramp|series, ...}). Equations are in the language "
            "modeling_guide describes.")


TOOLS: list[ToolDef] = [
    ToolDef("report_ticket", "report_ticket",
            "Report the outcome of your work on this ticket. Call exactly once when you are finished. "
            "READY_FOR_REVIEW means your result is ready for a reviewer (committed on your branch, or in RESULT.md). "
            "BLOCKED means you cannot proceed; blocked_reason is required and must say what you need.",
            {"state": {"type": "string", "enum": ["READY_FOR_REVIEW", "BLOCKED"]},
             "summary": S("What you changed and why, in a few sentences"),
             "blocked_reason": S("Required when state is BLOCKED")}, ("state", "summary")),
    ToolDef("report_review", "report_review",
            "Report your review verdict. Call exactly once when you are finished reading the result. "
            "APPROVED means it is correct, complete against the acceptance criteria, and consistent with "
            "the repository. REJECTED must include specific, actionable feedback the worker can act on.",
            {"verdict": {"type": "string", "enum": ["APPROVED", "REJECTED"]},
             "summary": S("Short summary of the review"),
             "feedback": S("Required when REJECTED: concrete problems, with file and line where possible")},
            ("verdict", "summary")),
    # tickets
    ToolDef("project_status", "tickets", "Counts of tickets by state, milestones, capacity, pipeline state."),
    ToolDef("list_milestones", "tickets", "List all milestones with id, name, status, and ticket counts."),
    ToolDef("create_milestone", "planning", "Create a milestone to group tickets. Returns its id (M1, M2, ...).",
            {"name": S("Short name"), "description": S("What this milestone is for")}, ("name",)),
    ToolDef("update_milestone", "planning", "Rename or re-describe a milestone.",
            {"id": S("Milestone id"), "name": S("New name"), "description": S("New description"), "order": I()}, ("id",)),
    ToolDef("close_milestone", "planning",
            "Close a milestone. Only allowed when every ticket in it is DONE or CANCELLED. Irreversible.",
            {"id": S("Milestone id")}, ("id",)),
    ToolDef("list_tickets", "tickets", "List tickets, optionally filtered by state or milestone.",
            {"state": S(f"One of {STATES}"), "milestone_id": S("Milestone id")}),
    ToolDef("get_ticket", "tickets", "Full detail of one ticket including its notes.", {"id": S("Ticket id like ORC-12")}, ("id",)),
    ToolDef("create_ticket", "planning",
            "Create a ticket in an open milestone. objective says why (which project objective it serves); "
            "description is a self-contained brief a worker can do without asking; acceptance is what a "
            "reviewer checks in the result; verification is how it is proved: the merge gate's tests for code "
            "(including any test that must be added), what the reviewer checks for a task.",
            {"title": S("Short imperative title"),
             "objective": S("Why this ticket exists and which objective it serves"),
             "description": S("Self-contained implementation brief"),
             "acceptance": S("Acceptance criteria, one per line"),
             "verification": S("How it is proved: existing acceptance test(s) or the test to add; for a task, what the reviewer checks"),
             "milestone_id": S("Milestone id, e.g. M1"),
             "priority": I("1 (first) .. 9 (last); default 5"),
             "depends_on": A("Ticket ids that must be DONE first")},
            ("title", "objective", "description", "acceptance", "verification", "milestone_id")),
    ToolDef("update_ticket", "tickets",
            "Edit a ticket's title, description, acceptance, verification, objective, priority, milestone, or dependencies.",
            {"id": S("Ticket id"), "title": S("New title"), "description": S("New description"),
             "acceptance": S("New acceptance criteria"), "verification": S("New verification"),
             "objective": S("New objective (supervisor only)"), "priority": I(),
             "milestone_id": S("Move to this open milestone"), "depends_on": A()}, ("id",)),
    ToolDef("add_note", "tickets", "Append a note to a ticket.", {"id": S("Ticket id"), "text": S("Note text")}, ("id", "text")),
    ToolDef("cancel_ticket", "tickets", "Cancel a ticket that should not be done.", {"id": S("Ticket id"), "reason": S("Why")}, ("id", "reason")),
    ToolDef("unblock_ticket", "tickets",
            "Move a BLOCKED ticket back to OPEN after resolving its blocker. Explain the resolution in note.",
            {"id": S("Ticket id"), "note": S("How the blocker was resolved")}, ("id", "note")),
    # context
    ToolDef("get_project_context", "context",
            "Read the project context document (objectives, value propositions, non-goals, verification)."),
    ToolDef("update_project_context", "context", "Replace the project context document with the full new markdown text.",
            {"text": S("The complete document in markdown")}, ("text",)),
    # pipeline
    ToolDef("get_gate_run", "pipeline", "Detail and logs of one merge gate run.", {"id": S("Gate run id")}, ("id",)),
    ToolDef("list_gate_runs", "pipeline", "Recent merge gate runs."),
    ToolDef("get_staging_status", "pipeline", "Status and recent log of the staging environment."),
    ToolDef("resume_pipeline", "pipeline", "Resume the merge pipeline after a base-branch failure has been fixed.",
            {"note": S("What was fixed")}, ("note",)),
    # capacity
    ToolDef("get_capacity", "capacity", "Current worker/reviewer capacity: the runtime dial and the configured maximum."),
    ToolDef("set_capacity", "capacity",
            "Set how many workers and reviewers may run at once, between 0 and the configured maximum. "
            "Use 0 to pause pickup while re-planning; raise it again afterwards.",
            {"workers": I(), "reviewers": I(), "note": S("Why")}, ("note",)),
    # events
    ToolDef("emit_event", "events",
            "Raise an event that other agents may be subscribed to: a fact with no addressee, like flaky_test_suspected "
            "or lesson_proposed. The type must be one your team declares in `emits`. Use message_agent for something "
            "aimed at one agent, escalate_to_supervisor for a decision. Rate-limited; refused when the cause chain is deep.",
            {"type": S("Declared event type, lowercase_name"), "subject": S("What it is about, e.g. a ticket id or test name"),
             "note": S("One or two sentences")}, ("type",)),
    ToolDef("read_events", "events",
            "Recent events on the bus (tickets, assignments, gate runs, staging, agents). Use to look back over what happened.",
            {"minutes": I("How far back, default 60"), "types": A("Event types to include; empty for all")}),
    # team: reading the organisation, and talking down or up the tree (leads and the supervisor)
    ToolDef("list_teams", "team", "All teams: lead, agents, sub-teams, access, paused state."),
    ToolDef("get_team", "team", "One team in full: responsibilities (own and inherited), agents, access, space.",
            {"name": S("Team name, e.g. product or product/qa")}, ("name",)),
    ToolDef("list_agents", "team", "All agents: roles, team, kind, schedule or wake events, paused state."),
    ToolDef("get_agent", "team", "One agent in full: its roles, team chain, effective tools, and the prompt it is started with.",
            {"name": S("Agent name")}, ("name",)),
    ToolDef("list_roles", "team", "All roles (job descriptions), built-in and custom, and which agents use each."),
    ToolDef("get_role", "team", "One role's definition and prompt.", {"name": S("Role name")}, ("name",)),
    ToolDef("run_agent", "team", "Invoke one of your team's ad_hoc agents once with a brief. It wakes at the next tick, "
            "does the brief, and closes. Only a lead (or the supervisor) may do this, and only for its own team.",
            {"name": S("Agent name"), "brief": S("What to do, self-contained")}, ("name", "brief")),
    ToolDef("message_agent", "team", "Leave a note for an agent's next wake-up: a lead to its team or its sub-teams' leads, "
            "an agent to its own lead. Anything else is refused; escalate to the supervisor instead.",
            {"name": S("Agent name"), "note": S("The message")}, ("name", "note")),
    # roles: designing the organisation (supervisor only)
    ToolDef("org_guide", "roles", "The method for designing roles, agents, and teams. Call this first whenever the human "
            "asks for a new team, a change to a team, a new role, or a new scheduled agent; then follow it."),
    ToolDef("create_role", "roles",
            "Create a role: a job description in the second person (what this kind of agent is good at, how it works, "
            "what it never does, how it reports). No schedule, no team: those belong to agents and teams.",
            {"name": S("lowercase_with_underscores"), "description": S("One line: what this kind of agent is good at"),
             "prompt": S("The job description in markdown, under sixty lines"),
             "tools": A("Tool groups the role needs: tickets, planning, guides, context, pipeline, capacity, escalate, events, model, experiments, replay, predict")},
            ("name", "description", "prompt")),
    ToolDef("update_role", "roles", "Change a custom role's description, tools, or prompt.",
            {"name": S("Role name"), "description": S(""), "prompt": S("New full prompt"), "tools": A()}, ("name",)),
    ToolDef("delete_role", "roles", "Delete a custom role no agent uses.", {"name": S("Role name")}, ("name",)),
    ToolDef("create_agent", "roles",
            "Create an agent: an instance of one or more roles in a team. kind is continuous (a process that is always "
            "up, session resident, woken by wake_on events and messages), scheduled (every '6h' or at 'HH:MM'; session "
            "resident or ephemeral), or ad_hoc (a fresh session each time it is invoked, by its lead with run_agent or by "
            "a wake_on event). The team may be one you create next.",
            {"name": S("lowercase_with_underscores"), "roles": A("Role names, at least one"), "team": S("Team name, or empty for none"),
             "kind": S("continuous | scheduled | ad_hoc"), "session": S("resident | ephemeral"),
             "wake_on": A("Event types, optionally filtered: 'gate.landed', 'task.reported:BLOCKED', 'task.attention_needed:coordinator'"), "every": S("e.g. 30m, 6h, 1d"), "at": S("HH:MM"),
             "description": S("One line: what this agent is for")},
            ("name", "roles", "kind")),
    ToolDef("update_agent", "roles", "Change a custom agent's roles, team, kind, schedule, or session policy.",
            {"name": S("Agent name"), "roles": A(), "team": S(""), "kind": S(""), "session": S(""), "wake_on": A(),
             "every": S(""), "at": S(""), "description": S("")}, ("name",)),
    ToolDef("delete_agent", "roles", "Delete a custom agent; its sessions are archived.", {"name": S("Agent name")}, ("name",)),
    ToolDef("pause_agent", "roles", "Pause an agent: wake-ups queue until resumed.", {"name": S("Agent name")}, ("name",)),
    ToolDef("resume_agent", "roles", "Resume a paused agent.", {"name": S("Agent name")}, ("name",)),
    ToolDef("restart_agent", "roles", "Restart an agent's session (fresh process; transcript kept).", {"name": S("Agent name")}, ("name",)),
    ToolDef("reset_agent", "roles", "Start a resident agent over with an empty transcript (the old one is archived as a run). Use when "
            "a session has grown so long that every turn is slow; the agent loses its memory of the conversation but "
            "not the project context or the team's space.", {"name": S("Agent name")}, ("name",)),
    ToolDef("create_team", "roles",
            "Create a team: one lead agent, members, responsibilities (standing conditions as a numbered markdown list), "
            "access (tool groups, which decide the tools its agents get, and repository paths, which are written into "
            "their prompts as instructions and are not enforced). Nest with 'parent/child'. Prefer update_team on an "
            "existing team; a project rarely needs more than four or five.",
            {"name": S("Team name, e.g. product or product/qa"), "description": S("One line"), "lead": S("Lead agent name"),
             "members": A("Member agent names"), "responsibilities": S("Markdown: what this team keeps true"),
             "access": {"type": "array", "items": {"type": "object"},
                        "description": "Entries like {\"tools\": [\"tickets\", \"events\"]} or {\"path\": \"src\", \"access\": \"read\"}; "
                                       "a path entry is an instruction in the agents' prompts, not a permission check"},
             "tracked": {"type": "boolean", "description": "Commit the team's space with the code (default false: ignored by git)"},
             "emits": A("Event types this team's agents may raise with emit_event, e.g. lesson_proposed"),
             "probes": {"type": "array", "items": {"type": "object"},
                        "description": "Conditions Orchestra checks on an interval, run in the team's space: "
                                       "[{\"name\": \"docs_budget\", \"command\": \"tools/doc-budget\", \"every\": \"30m\"}]. "
                                       "Exit 0 = holds; probe.failed:<name> / probe.recovered:<name> fire on the transition."}},
            ("name", "lead", "responsibilities")),
    ToolDef("update_team", "roles", "Change a team: lead, members, responsibilities, access, tracking.",
            {"name": S("Team name"), "description": S(""), "lead": S(""), "members": A(), "responsibilities": S("Full new markdown"),
             "access": {"type": "array", "items": {"type": "object"}}, "tracked": {"type": "boolean"},
             "emits": A(), "probes": {"type": "array", "items": {"type": "object"}}}, ("name",)),
    ToolDef("delete_team", "roles", "Delete a custom team that has no agents and no sub-teams.", {"name": S("Team name")}, ("name",)),
    # observe: the whole system, read only (the Supervisor looks before it concludes)
    ToolDef("check_setup", "observe",
            "Whether the system can do what is asked: which machines fill the worker and reviewer roles (AI agents, programs), "
            "the kind of work (tasks or code), the gate's tests, the capacity limit, what Orchestra runs and how each "
            "collector's last round went. Call it first when a ticket blocks or keeps being rejected, or a request is not met."),
    ToolDef("read_config", "observe", "The project's orchestra.toml as it is, whether it can be changed now, and its recent commits; "
            "whether the owner has a private overlay, and each key it sets (secret values left out)."),
    ToolDef("list_processes", "observe",
            "The programs Orchestra's launcher keeps running (its runtime record: state, pid, restarts, last exit code, "
            "log) and the collectors orchestra.toml declares, with the last line of each one's log."),
    ToolDef("read_machine_log", "observe",
            "The end of a machine's log: a program's or a collector's (by its name in orchestra.toml or the process "
            "list, e.g. shell_worker, tasks.scheduler), orchestra-host for Orchestra's own, or the log a machine announced.",
            {"name": S("Machine or process name"), "lines": I("How many lines from the end, default 80")}, ("name",)),
    ToolDef("list_metrics", "observe",
            "The metrics catalogue: every series name in the project's own store, its labels, when it was last written "
            "and how many points in the last hour. A measurement that is not here is not being stored.",
            {"prefix": S("Only names starting with this")}),
    ToolDef("get_model", "observe",
            "The system dynamics model Orchestra discovered from the metrics, for a diagnosis: the series each stock is, flows "
            "with their equations and the machines attributed to them, and the series the last discovery left out, with why. "
            "To experiment on the model, read_model."),
    ToolDef("read_instrument", "observe",
            "A declared instrument ([[collect]], [[scrape]] or [[sources]]) as Orchestra sees it: when it last wrote, how "
            "many points, failures and its last error, the end of its log, and the latest values of each series it stored "
            "in the last minutes. Use it after set_collector to see that the measurement is being stored.",
            {"name": S("The instrument's name"), "minutes": I("How far back to look for points, default 30"),
             "wait_s": I("Wait up to this many seconds (at most 120) for its next round to end first: right after set_collector")},
            ("name",)),
    ToolDef("check_sources", "observe",
            "Another time-series store ([[sources]], or a url before declaring one): where it is, whether its credentials "
            "are present (never their values), whether it answers and which API endpoints work.",
            {"source": S("A [[sources]] name"), "url": S("Or a store's URL, to look before declaring it"),
             "auth": O("With url: as [[sources]] writes it: {\"bearer_file\": \"~/path/to/token\"} for a token in a file, "
                       "{\"bearer_env\": \"WORK_TOKEN\"} for one in the environment; never the token itself")}),
    ToolDef("source_schema", "observe",
            "What a store has: without match, every metric name on one line (its type, or a guess from its name; how many "
            "series; each label with how many values); with match, those metrics in detail (help, label values).",
            {"source": S("A [[sources]] name"), "url": S("Or a store's URL"), "auth": O("With url: as [[sources]] writes it"),
             "match": A("PromQL selectors, e.g. {__name__=~\"http_.*\"}"), "minutes": I("The window, default 60"),
             "limit": I("Most series read, default 20000")}),
    ToolDef("preview_query", "observe",
            "What a PromQL query returns over a recent window: each series' labels, points, min, max, last and a sparkline. "
            "From a source, or with neither source nor url from the project's own store.",
            {"query": S("PromQL"), "source": S("A [[sources]] name"), "url": S("Or a store's URL"),
             "auth": O("With url: as [[sources]] writes it"), "minutes": I("The window, default the discovery window"),
             "step_s": I("Seconds per step, default the discovery step")}, ("query",)),
    ToolDef("infer_source", "observe",
            "What Orchestra infers to read from a store for discovery: its metric families (kind, stats, interval, labels "
            "and their roles), the definitions it keeps (query, view, kind, score) and those it leaves out with why.",
            {"source": S("A [[sources]] name"), "url": S("Or a store's URL"), "auth": O("With url: as [[sources]] writes it"),
             "match": A("PromQL selectors to narrow it")}),
    ToolDef("snapshot", "observe",
            "The task system's whole picture in one answer: tickets with why each waits, milestones, capacity, the "
            "pipeline, and which machine instance holds each assignment."),
    # setup: the configuration, changed by the Supervisor only, through Orchestra: in orchestra.toml
    # (target repo, each change a commit with its reason) or the owner's private overlay (target overlay)
    ToolDef("set_setting", "setup",
            "Set one key of the configuration (section.name, as desk.channels or slack.me), or remove it with value "
            "null. Say where: target overlay for the owner's own or their company's settings (identities, channels, "
            "templates, anything personal; never committed), repo for the project's settings (committed).",
            {"key": S("section.name"),
             "value": S('The value as JSON: "#ops", 3, true, ["#a", "@me"], {"k": "v"}; null removes the key'),
             "target": TARGET, "change": CHANGE, "reason": S("Why, in a sentence")}, ("key", "target", "reason")),
    ToolDef("set_agent_roles", "setup",
            "Set which of the task system's roles the AI agents fill ([agents] serves). A role left out is left to other "
            "machines (a program worker): leave a role out only when one fills it.",
            {"serves": A("worker, reviewer, both, or none"), "target": TARGET, "change": CHANGE, "reason": S("Why, in a sentence: it goes in the commit")},
            ("serves", "reason")),
    ToolDef("set_work_mode", "setup",
            "Set the kind of work ([project] work): code (a branch per ticket, reviewed, landed on the base branch by the "
            "merge gate; needed for anything that must end up in the repository, like a collector) or tasks (a scratch "
            "folder per ticket, nothing merged).",
            {"work": {"type": "string", "enum": ["tasks", "code"]}, "target": TARGET, "change": CHANGE, "reason": S("Why, in a sentence")}, ("work", "reason")),
    ToolDef("set_gate_unit", "setup",
            "Add or change a test the merge gate runs on every branch before it lands ([[gate.unit]]): a shell command, "
            "run in the branch's checkout, that must exit 0.",
            {"name": S("The test's name, default unit"), "command": S("The shell command"),
             "timeout": S("How long it may take, e.g. 300s"), "target": TARGET, "change": CHANGE, "reason": S("Why, in a sentence")}, ("command", "reason")),
    ToolDef("set_capacity_limit", "setup",
            "Set the most tickets offered at once to workers and to reviewers ([tasks] workers, reviewers): the ceiling "
            "under which the Coordinator's set_capacity dial moves.",
            {"workers": I(), "reviewers": I(), "target": TARGET, "change": CHANGE, "reason": S("Why, in a sentence")}, ("reason",)),
    ToolDef("set_machine", "setup",
            "Add or change a program Orchestra keeps running ([[machines]]): a command in the repository. Orchestra starts "
            "a new one within a second and restarts one whose command changed. The program itself is a worker's to write.",
            {"name": S("letters, digits, . _ -"), "command": S("The command, e.g. python machines/feed.py"),
             "restart": S("always (default), on-failure or never"), "stop_grace": I("Seconds from SIGTERM to SIGKILL"),
             "target": TARGET, "change": CHANGE, "reason": S("Why, in a sentence")}, ("name", "command", "reason")),
    ToolDef("set_collector", "setup",
            "Add or change an instrument: a command that prints Prometheus text ([[collect]]), or a url that serves it "
            "([[scrape]]). Orchestra runs it on its interval and stores what it prints, labelled instrument=<name>. The "
            "command is a script in the repository (machines/disk.sh, a worker's to write), an existing tool's command "
            "(python -m orchestra.instruments.test_reports ...), or with shell: true a one-liner run by sh -c. Try it "
            "first with try_collector; after, check it with read_instrument.",
            {"name": S("The instrument's name: letters, digits, . _ -"), "command": S("The command, e.g. machines/disk.sh"),
             "shell": B("Run command with sh -c: a one-liner with pipes, $(...) or &&, at most 300 characters"),
             "url": S("Or a URL"), "every": S("How often, e.g. 60s"), "timeout": S("For a round that may take more than 10 s"),
             "target": TARGET, "change": CHANGE, "reason": S("Why, in a sentence")}, ("name", "reason")),
    ToolDef("try_collector", "setup",
            "Run a collector's command once (or read a url once) exactly as Orchestra would, without declaring or storing "
            "anything: the series it would store, the lines Orchestra cannot parse, names it refuses, how long it took, "
            "its stderr. Use it before set_collector, until it works.",
            {"command": S("The command, as set_collector would take it"), "shell": B("Run it with sh -c (a one-liner)"),
             "url": S("Or a URL serving Prometheus text"), "timeout": S("How long it may take, default 10s")}),
    ToolDef("remove_entry", "setup",
            "Remove a [[machines]], [[collect]], [[scrape]] or [[gate.unit]] entry by name. A removed machine is stopped.",
            {"section": {"type": "string", "enum": ["machines", "collect", "scrape", "gate.unit"]}, "name": S("Entry name"),
             "target": TARGET, "change": CHANGE, "reason": S("Why, in a sentence")}, ("section", "name", "reason")),
    ToolDef("restart_machine", "setup",
            "Restart one of the project's programs ([[machines]]), including one Orchestra gave up on: its entry is taken "
            "out until Orchestra has stopped it, then put back as it was, which starts it again. Nothing is committed.",
            {"name": S("The [[machines]] entry's name"), "reason": S("Why, in a sentence")}, ("name", "reason")),
    # model: read the system dynamics model and what was done with it (modeling.py)
    ToolDef("modeling_guide", "model",
            "How to read the system dynamics model and experiment on it: what stocks, flows, equations, eras, trust and "
            "verdicts mean, the equation language, and how to test a hypothesis before you believe it. Read it before your "
            "first experiment."),
    ToolDef("read_model", "model",
            "The system dynamics model Orchestra discovered from the metrics, summarised: each stock with its level now, each "
            "flow with its ends, its equation in the equation language, its origin and error, and whether to trust it.",
            {"version": I("A saved version (default: the current era's latest discovery)"), "raw": RAW}),
    ToolDef("read_series", "model",
            "Measured history of stocks (levels), flows (rates) or measured inputs, summarised: first and last, range, mean, "
            "trend an hour, the latest readings. points: true gives the points themselves, paged.",
            {"ids": A("Stock, flow or input ids from read_model (at most 64)"), **{k: WINDOW[k] for k in ("span", "start", "end")},
             "points": B("Also give the points, [unix seconds, value], at most limit a series"), "limit": I("Points a series, default 500"),
             "after": {"type": "number", "description": "Continue a series cut short: its next"}, "version": I("A saved version")},
            ("ids",)),
    ToolDef("read_eras", "model",
            "The eras (each announced change to the system begins one) and what changed at the last boundary: each flow's "
            "numbers before and after, with how sure that it changed.", {"raw": RAW}),
    ToolDef("read_observability", "model",
            "Which stocks what is measured determines, which are hidden, and the model's modes (how fast it settles).",
            {"version": I("A saved version"), "raw": RAW}),
    ToolDef("check_equation", "model",
            "Whether an equation parses in the equation language against the model (or an experiment's draft), and what it reads.",
            {"equation": S("e.g. k * min(queue, 2 * workers)"), "params": O("Its parameters, e.g. {\"k\": 0.5}"),
             "experiment": S("Check against this experiment's draft")}, ("equation",)),
    ToolDef("read_predictions", "model",
            "Predictions and their verdicts (passed, no effect, side effect, model wrong elsewhere, interrupted, undecided), "
            "with the numbers each was judged by.",
            {"id": S("One prediction"), "experiment": S("The prediction this experiment became"), "change": S("The change id"),
             "mine": B("Only yours"), "raw": RAW}),
    ToolDef("list_experiments", "model", "Experiments, newest last: id, title, status, owner, runs, prediction.",
            {"mine": B("Only yours"), "owner": S("Only this machine's"), "status": S("draft, committed or discarded"),
             "era": S("Only those made on this era's model or predicting the change that began it")}),
    ToolDef("read_experiment", "model",
            "One experiment: what its draft changed from the base model, its runs, its prediction.",
            {"id": EXPERIMENT, "draft": B("Also the whole draft model, summarised"), "raw": RAW}, ("id",)),
    ToolDef("diff_experiment", "model", "What an experiment's draft changed from its base model: flows, stocks, the scenario.",
            {"id": EXPERIMENT, "raw": RAW}, ("id",)),
    ToolDef("read_run", "model", "An earlier run of an experiment again, summarised, or whole with raw.",
            {"experiment": EXPERIMENT, "run": S("The run's id (r-...)"), "raw": RAW}, ("experiment", "run")),
    # experiments: a draft copy of the model, edited and run; it changes nothing in the system
    ToolDef("create_experiment", "experiments",
            "Start an experiment: a draft copy of the model (the current era's latest, or a saved version) that you edit and run. "
            "It changes nothing in the system.",
            {"title": S("What you are trying, in a few words"), "purpose": S("The question it answers"),
             "version": I("Copy a saved version instead")}, ("title",)),
    ToolDef("edit_experiment", "experiments",
            "Edit your experiment's draft: one edit (edit) or a batch (edits), applied together or not at all. " + EDIT_OPS,
            {"id": EXPERIMENT, "edits": OS("The batch, in order"), "edit": O("One edit")}, ("id",)),
    ToolDef("load_model", "experiments",
            "Replace your experiment's whole draft: {stocks: [{id, initial?}], flows: [{id, from, to, equation, params}]}. A stock "
            "the base measures keeps its series; a new one is hidden.",
            {"id": EXPERIMENT, "model": O("The whole model")}, ("id", "model")),
    ToolDef("run_forward", "experiments",
            "Run the base model and your draft forward from the levels now: each stock's base, experiment and difference with "
            "a band, the limits they hit, and what not to trust.",
            {"id": EXPERIMENT, "horizon": S("How far ahead, such as 30m, 2h or 1d (default 1h)"),
             "stocks": A("Only these stocks"), "bounds": O("Limits to watch: {stock: [lo, hi]}"),
             "members": I("Runs in the band, default 24"), "raw": RAW}, ("id",)),
    ToolDef("run_inverse", "experiments",
            "Ask what would have had to be different for a past window to come out otherwise: the fewest changes to the "
            "draft's numbers (and, with new_flows, a new flow) under which a replay meets the targets. The answer is a new "
            "draft of yours. Hold what must not move; treat an ambiguous answer as a warning.",
            {"id": EXPERIMENT, "targets": OS("{stock, scale} (its mean ×), {stock, level}, {stock, below|above}, {flow, scale}, "
                                             "{flow, total}"),
             "hold": OS("{flow} (its equation stays) or {series} (that history stays)"), **WINDOW,
             "new_flows": B("Also consider a new inflow or outflow of a target stock"), "max_changes": I("1 to 8, default 3"),
             "deadline_s": I("Seconds to search, up to 300, default 60"), "alternatives": I("Other answers to report, default 3"),
             "check": B("Check the answer's uniqueness by rediscovery (default true)"), "raw": RAW}, ("id", "targets")),
    ToolDef("discard_experiment", "experiments", "End an experiment of yours you will not commit; it stays readable.",
            {"id": EXPERIMENT}, ("id",)),
    # replay: a past window run again on an experiment's equations
    ToolDef("run_replay", "replay",
            "Replay a past window with the base model and an experiment's draft, from the levels measured at its start, against "
            "what was measured: whether the draft's equations explain what happened (each stock's error, each counted "
            "flow's fit), not only the outcome wanted. Any experiment may be replayed.",
            {"id": EXPERIMENT, **WINDOW, "raw": RAW}, ("id",)),
    # predict: an experiment as the prediction of the caller's own change
    ToolDef("commit_prediction", "predict",
            "Commit your experiment as the prediction of the change you are about to make: its edits become what the change "
            "is expected to do, its forward run what the stocks will do. Returns the change id; make the change, then announce "
            "it with that id. Orchestra judges it after the horizon and the verdict wakes you.",
            {"id": EXPERIMENT, "horizon": S("How long after the change to judge it, such as 30m or 2h: long enough for the "
                                            "flows you changed to count a few dozen events"),
             "subject": S("What the change is about (default: your ticket, or the experiment)")}, ("id", "horizon")),
    ToolDef("announce_change", "predict",
            "Announce that the change you predicted is made (system.changed, carrying its change id): a new era begins and the "
            "prediction is judged from it. when: now (it is in effect) or landed (code that takes effect when your ticket lands).",
            {"change": S("The change id commit_prediction gave you"), "note": S("What changed, in a sentence"),
             "when": {"type": "string", "enum": ["now", "landed"], "description": "now (default) or landed"}}, ("change",)),
    # guides: how to configure Orchestra and each installed plugin (orchestra.guides)
    ToolDef("list_guides", "guides",
            "The operator's guides to configuring Orchestra and each installed plugin: each one's name, what it is about, "
            "its size and its sections. Read the one you need with read_guide before you change a setting it covers."),
    ToolDef("read_guide", "guides", "One guide in full, or one of its sections.",
            {"name": S("The guide's name, from list_guides (orchestra, tasks, agents, ...)"),
             "section": S("A section's heading (or part of it), to read only that part")}, ("name",)),
    # handoff: the Supervisor passes the human's requirements to the Coordinator, who plans them
    ToolDef("hand_to_coordinator", "handoff",
            "Hand the human's requirements to the Coordinator, who plans them into a milestone and tickets and reports "
            "the plan back to you (you are woken with it). Write the requirements so a planner who never met the human can "
            "plan them: the outcome wanted, what will be observably true, how it is verified, constraints, the human's "
            "choices already made, and the priority.",
            {"requirements": S("What is wanted: outcome, acceptance, verification, constraints, priority"),
             "context": S("What the Coordinator should know: the objective it serves, what you found in the repository, "
                          "related tickets")}, ("requirements",)),
    ToolDef("report_plan", "planning",
            "Tell the Supervisor how you planned its request (work.requested): the milestone, the tickets you created, and "
            "any question only the human can answer. Call it once per request, after creating the tickets.",
            {"request": S("The request's id (r-...), from the wake-up"), "summary": S("The plan in a few sentences"),
             "milestone": S("The milestone id the tickets are in"), "tickets": A("The ids of the tickets you created"),
             "questions": S("What the human must decide before (some of) the work can go ahead; empty when nothing")},
            ("request", "summary")),
    # escalate
    ToolDef("escalate_to_supervisor", "escalate",
            "Wake the Supervisor with a note when a decision about intent, scope, or priorities is needed.",
            {"note": S("What needs deciding and why")}, ("note",)),
]

BY_NAME = {t.name: t for t in TOOLS}


def tools_for(groups: list[str] | tuple[str, ...] | set[str]) -> list[ToolDef]:
    g = set(groups)
    return [t for t in TOOLS if t.group in g]
