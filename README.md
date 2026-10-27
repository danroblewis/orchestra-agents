# orchestra-agents

AI agents as Orchestra machines. A host (`orchestra-agents run`) keeps the resident agents up
(the Supervisor the human talks to, the Coordinator that keeps the plan executable, any custom
agent), wakes them on events and schedules, and runs a worker or a reviewer on a ticket when a
task system (orchestra-tasking) assigns one over Orchestra's event bus. Which product runs a
session is a setting: Pi, Claude Code or Codex, or `dummy`, scripted agents with no model, no
network and no cost, for demos and tests. An Orchestra plugin (`agents`) adds the Agents
page, the Supervisor chat, and the agents' machine cards to Orchestra's web UI.

How it works, with diagrams, the tools and the protocols: [ARCHITECTURE.md](ARCHITECTURE.md).

The agents change no ticket themselves and manage no branches: every ticket change is a request
on the bus that the task system applies, and the task system prepares the workspace. The two
packages import nothing from each other; the contract is `orchestra_agents/protocol.py`.
`SPEC.md` is the design; `AGENTS.md` the rules.

## Try it

    uv sync --extra dev                # first clone github.com/danroblewis/orchestra beside this repository as `orchestra_2`, and orchestra-tasking as `orchestra-tasking` (the paths in [tool.uv.sources])
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

The task system must be set up first (`tasking init`, `tasking run`; see its README). Add to the
repository's `orchestra.toml`:

    [agents]
    harness = "claude_code"      # pi | claude_code | codex | dummy
    model = "haiku"
    serves = ["worker", "reviewer"]   # the task system's roles these agents fill

If your tasks live seconds, set `[discovery] step = "5s"` (with a window of 30–60 m) in
`orchestra.toml` too: Orchestra's default grid is 30 s, and a level that changes inside one
step is not a stock.

The commands run from this repository's environment (`--project <this repo>`, as the task
system's README does it), since the target repository need not have anything installed; the
`dev` extra brings the task system (`uv sync --extra dev` here first):

    cd <repo> && uv run --project <this repo> orchestra run          # Orchestra's host; its launcher starts the agents' host
    cd <repo> && uv run --project <this repo> orchestra web          # Orchestra's UI with both plugins' pages
    uv run --project <this repo> orchestra-agents chat               # the Supervisor, in the terminal

`orchestra run` starts the agents' host (`orchestra-agents run --wait`, the process `agents.host`)
because the plugin declares it for Orchestra's launcher when `orchestra.toml` has `[agents]`,
keeps it running and stops it with itself, giving it 30 s to shut down; its output is in
`~/.orchestrator/<key>/logs/machines/agents.host.log`. To run the host by hand instead, leave
`orchestra run` out and run `orchestra-agents run`; two hosts of one project refuse to run side
by side (with `--wait`, the second waits for the first to stop).

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
are planner → worker → reviewer → worker → reviewer → merge_gate; discovery hypothesises no flow
on `task_scheduler`; and the ticket metrics arrive with `instrument=tasks.tickets` (the
instrument the task system's plugin declares, `tasking metrics`).
