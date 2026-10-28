# orchestra-agents

AI agents as Orchestra machines. A host (the process `agents.host`, which `orchestra run` starts) keeps the resident agents up
(the Supervisor the human talks to, the Coordinator that keeps the plan executable, any custom
agent), wakes them on events and schedules, and runs a worker or a reviewer on a ticket when a
task system (orchestra-tasking) assigns one over Orchestra's event bus. Which product runs a
session is a setting: Pi, Claude Code or Codex, or `dummy`, scripted agents with no model, no
network and no cost, for demos and tests. An Orchestra plugin (`agents`) adds the Agents
page, the Supervisor chat, and the agents' machine cards to Orchestra's web UI.

What the plugin is: a target repository normally defines its own machines, as code that
publishes and listens on the bus under its own `source`, or through a bridge. The Agents plugin
is a common resource for that: it provides an **implementation of a machine** (the AI agent, a
universal machine), the **means to configure one** (roles, harness, model, how it is engaged,
permissions), a **shared runtime** that runs the configured agents (the host, which is not a
machine: it never announces itself, never publishes as `agents` and writes no metrics; its
measurements reach Orchestra through the instrument `agents.turns`), and **UI additions**. The
machines on Orchestra's Machines page are the configured agents (`supervisor`, `coordinator`,
`worker/1`, ...), never the plugin.

How it works, with diagrams, the tools and the protocols: [ARCHITECTURE.md](ARCHITECTURE.md).

The agents change no ticket themselves and manage no branches: every ticket change is a request
on the bus that the task system applies, and the task system prepares the workspace. The two
packages import nothing from each other; the contract is `orchestra_agents/protocol.py`.
`SPEC.md` is the design; `AGENTS.md` the rules.

## Try it

    uv sync --extra dev                # Orchestra and the task system from GitHub, the commits uv.lock pins ("Developing against a local Orchestra")
    uv run demo/run.py                 # a fresh demo project at ~/orchestra-agents-demo, http://127.0.0.1:8072
    uv run demo/run.py --dummy         # the same demo for free: scripted agents, no model, no network

An AI reviewer (Claude Code, Haiku) and a program worker (a shell runner) work the same queue:
fifteen tickets over about four minutes, eight at once and then one every 30 s, five of them
rejected once and three blocked for a planner, about ten cents of Haiku; `--tickets 3` is a
one-minute look. `demo/README.md` says what you will see and where. Ctrl-C stops everything;
remove `~/orchestra-agents-demo` first to run it again. With `--dummy` the agents run on the
dummy harness, playing `demo/dummy.toml`: the four tickets land with real work (one rejected once
and reworked) and the Supervisor answers the chat, without a model and at no cost.

## Use it on a repository

Install Orchestra with the task system and the agents beside it, once, as one tool (no clone):

    uv tool install 'orchestra[sd] @ git+https://github.com/danroblewis/orchestra@main' --with 'orchestra-tasking @ git+https://github.com/danroblewis/orchestra-tasking' --with 'orchestra-agents @ git+https://github.com/danroblewis/orchestra-agents'

That puts one executable on your PATH, `orchestra`. The agents' command line comes with it as
`orchestra agents …` and the task system's as `orchestra tasks …`; neither has an executable of
its own. Installing the tool again replaces it, so name every plugin on each install.

In the repository (a git repository), set up the task system, then add the agents to its
`orchestra.toml`. Start with the dummy harness: scripted agents with no model, no network and no
cost, so you can see the whole loop work first:

    cd <repo>
    orchestra tasks init         # orchestra.toml (if there is none) with the task system's sections

    # then add to orchestra.toml:
    [agents]
    harness = "dummy"            # pi | claude_code | codex | dummy
    serves = ["worker", "reviewer"]   # the task system's roles these agents fill

Run Orchestra, open its UI, and give it a ticket:

    orchestra run                # Orchestra's host; it starts the task system's scheduler and the agents' host
    orchestra web                # Orchestra's UI with the Tickets and Agents pages; open the login link it prints
    orchestra tasks api --as planner create_milestone --args '{"name": "First"}'
    orchestra tasks api --as planner create_ticket --args '{"title": "Say hello", "milestone_id": "M1", "description": "Write hello.", "acceptance": "It says hello."}'
    orchestra tasks status       # the ticket moves to DONE: a dummy worker did it, a dummy reviewer approved it

Then switch to a real model. The `claude_code` harness runs Claude Code, so `claude` must be
installed, on the PATH `orchestra run` has, and logged in (`claude` once by hand). Change the
section to:

    [agents]
    harness = "claude_code"
    model = "haiku"
    serves = ["worker", "reviewer"]

Restart `orchestra run` (Ctrl-C, then start it again) so that every agent, the resident
Supervisor and Coordinator included, comes up on the new harness. Talk to the Supervisor on the Agents page of `orchestra web`, or in the terminal:

    orchestra agents chat        # the Supervisor; Ctrl-D leaves
    orchestra agents status      # the host, what it runs the agents on, what each agent is doing

If your tasks live seconds, set `[discovery] step = "5s"` (with a window of 30–60 m) in
`orchestra.toml` too: Orchestra's default grid is 30 s, and a level that changes inside one
step is not a stock.

`orchestra run` starts the agents' host (`orchestra agents run --wait`, the process
`agents.host`, run with Orchestra's own Python) because the plugin declares it for Orchestra's
launcher when `orchestra.toml` has `[agents]`, keeps it running and stops it with itself, giving
it 30 s to shut down; its output is in `~/.orchestrator/<key>/logs/machines/agents.host.log`. To
run the host by hand instead, leave `orchestra run` out and run `orchestra agents run`; two hosts
of one project refuse to run side by side (with `--wait`, the second waits for the first to stop).
The agents' tools call back into Orchestra's Python by its full path, which the host gives each
session, so a session's own PATH never matters.

A measurement the project wants taken over time is a collector of the project's own: the
Supervisor has a worker write a small script under `machines/` (shell or any language, importing
nothing of Orchestra's) that prints Prometheus text, and declare it in `orchestra.toml` under
`[[collect]]` with an interval (or a `[[scrape]]` of a URL that already serves Prometheus
metrics); once the branch lands, `orchestra run` runs it and stores what it prints in the
project's metrics store (Orchestra's `docs/machines.md`). That needs `work = "code"` in
`[project]` and an AI agent among the workers; when the project is not set up that way, the
Supervisor changes it.

The Supervisor is also the project's operator. It reads the whole system (orchestra.toml, the
processes Orchestra runs and their logs, the metrics catalogue, the model, the task system's
snapshot) and, when the setup cannot do what is asked, changes `orchestra.toml` with its setup
tools: which roles the AI agents fill, the kind of work, the gate's tests, the capacity limit,
`[[machines]]`, `[[collect]]` and `[[scrape]]` entries, and restarting a program. Each change is a
commit of `orchestra.toml` alone on the base branch with the reason, and a `config.changed` event
on the bus; it tells you in the chat what it changed and why. It never changes code: that is a
worker's, through a ticket, a review and the merge gate.

Without the task plugin in the same `orchestra web`, the agents still run, but a ticket link on
the Agents page opens no ticket drawer and no banner says a ticket is blocked.

State is in `~/.orchestrator/<key>/agents/`; custom roles, agents and teams in the repository
under `.orchestra/`. The Supervisor designs the organisation in conversation; the Agents page
shows it.

## Dummy agents

`harness = "dummy"` runs every agent from a script instead of a model: each turn plays the first
matching rule of the file `[agents] dummy_script` names (TOML), then the built-in rules, so the
setting alone is a working team (a worker commits a token file and reports ready, a reviewer
approves, the Supervisor answers that it is scripted). A rule matches on the role, the ticket, a
pattern in the prompt and the turn's number, and its steps write files, run commands, commit,
call the agents' tools, wait or fail:

    [[rule]]
    role = "reviewer"
    turn = 1                     # the first review of each ticket is a rejection
    steps = [{ tool = "report_review", args = { verdict = "REJECTED", summary = "Not yet.", feedback = "1. Add a test." } }]

The tools go through the same calls as a model's, so a test on dummy agents tests everything but
the model. The format: SPEC.md section 6, ARCHITECTURE.md "The dummy harness and its scripts",
and `demo/dummy.toml` as a worked example.

## Tests

    uv run python -m pytest -q         # about four minutes: unit, in-process e2e per harness, the demo through a real Orchestra
    uv run ruff check orchestra_agents tests demo

Every e2e test uses the fakes in `tests/e2e/` (one per harness, speaking the product's real
protocol) or the dummy harness; nothing in the tests calls a model. `tests/e2e/test_dummy_demo_e2e.py`
runs the whole demo on dummy agents with no API key and no network. Its
`test_what_the_owner_sees` lands twelve tickets, five of them rejected once (a rule appended to
the target's `dummy.toml`), and checks what the owner would see: `task_scheduler` is in no
event's source and no machine, edge or hand-off of `/api/machines`; a reworked ticket's hand-offs
are planner → coordinator → worker → reviewer → worker → reviewer → merge_gate (the first offer is
the task system's dispatch, an instance of the coordinator); discovery hypothesises no flow
on `task_scheduler`; and the ticket metrics arrive with `instrument=tasks.tickets` (the
instrument the task system's plugin declares, `orchestra tasks metrics`). Its `test_the_agents_host_is_no_machine` has the
Supervisor file a ticket and checks that no machine, edge or event is `agents`, that the
Supervisor announces what the host watches for it (merge_gate's `gate.landed` reaches it on the
map), that it is woken when its ticket lands, and that the agents' metrics arrive with
`instrument=agents.turns`; `tests/unit/test_host_is_no_machine.py` is the same rule in process.

## Developing against a local Orchestra

`pyproject.toml` takes Orchestra from GitHub (`orchestra @ git+https://github.com/danroblewis/orchestra@main`),
and the dev extra takes the task system the same way, over SSH (it is private). `uv.lock` pins
both commits, so nothing needs cloning beside this repository. `uv lock --upgrade-package orchestra`
(or `orchestra-tasking`) moves a pin to the newest `main`, then `uv sync --extra dev`.

To run against local checkouts instead, with their uncommitted changes, lay them over this
environment for one command:

    uv run --with-editable ~/orchestra_2 --with-editable ~/orchestra-tasking python -m pytest -q

Nothing is written: `.venv`, `pyproject.toml` and `uv.lock` keep the pinned commits, and a plain
`uv run` goes back to them.
