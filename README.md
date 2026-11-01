# orchestra-agents

AI agents as Orchestra machines. A host (the process `agents.host`, which `orchestra run` starts) keeps the resident agents up
(the Supervisor the human talks to, the Coordinator that keeps the plan executable, any custom
agent), wakes them on events and schedules, and runs a worker or a reviewer on a piece of work
when a task system (orchestra-tasking) or the Workflows floor (orchestra-workflows) assigns one
over Orchestra's event bus. Which product runs a
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

How it works, the tools and the protocols: [SPEC.md](SPEC.md).

The agents change no ticket themselves and manage no branches: every ticket change is a request
on the bus that the task system applies, and the task system prepares the workspace. The two
packages import nothing from each other; the contract is `orchestra_agents/protocol.py`.
`SPEC.md` is the design; `AGENTS.md` the rules.

## Try it

    uv sync --extra dev                # Orchestra and the task system from GitHub, the commits uv.lock pins ("Developing against a local Orchestra")
    uv run demo/run.py                 # a fresh demo project at ~/orchestra-agents-demo, http://127.0.0.1:8072
    uv run demo/run.py --dummy         # the same demo for free: scripted agents, no model, no network

One milestone of four tickets, worked by AI agents (Claude Code, Haiku) and a program worker (a
shell runner) on one queue: two shell tickets (DEMO-1 and DEMO-2, the toolchain and the start
date, which the program worker or an AI worker takes, whichever accepts first) and two code
tickets only an AI worker can write (DEMO-3, a line-counting tool and its test; DEMO-4, a
collector), each reviewed by an AI reviewer and landed on main by the merge gate. With `--dummy`
the agents run on the dummy harness, playing `demo/dummy.toml`: the four tickets land with real
work, DEMO-3's first review rejects it and the rework lands, and the Supervisor answers the chat
with the project's status, in about a minute, without a model and at no cost. `demo/README.md`
says what you will see and where. Ctrl-C stops everything; remove `~/orchestra-agents-demo`
first to run it again.

## Use it on a repository

Install Orchestra with the agents beside it, once, as one tool (no clone):

    uv tool install 'orchestra[sd,agents,bridges,dev] @ git+https://github.com/danroblewis/orchestra@main'

`bridges` brings the integration kits the Supervisor builds a connection to another system from
(`integrate_system`), and `dev` brings pytest, which runs each scaffolded poller's own test in
Orchestra's Python (as Orchestra's `docs/INTEGRATING.md` §4 installs them); add `tasking` for the
task system and `workflows` for the Workflows floor. That puts one executable on your PATH,
`orchestra`. The agents' command line comes with it as `orchestra agents …` and the task system's
as `orchestra tasks …`; neither has an executable of its own. Installing the tool again replaces it, so name every plugin, as an extra, on each install.

In the repository (a git repository), start Orchestra and its UI. You configure nothing: the
Supervisor does.

    cd <repo>
    orchestra init               # orchestra.toml with Orchestra's own sections, if there is none
    orchestra run                # Orchestra's host; it starts the agents' host (and the task system's scheduler once set up)
    orchestra web                # Orchestra's UI with the Agents page; open the login link it prints

With no `[agents]` section the agents run on Claude Code with Haiku when the `claude` command is on
the PATH `orchestra run` has (installed, and logged in once with `claude` by hand), and otherwise on
the dummy harness (scripted, no model, no cost); the Agents page says which, and how to change it.
The Supervisor runs on its machine spec's model, **Sonnet by default** (decision D20), where the
harness takes the name; every other agent on `[agents] model` (Haiku by default). To change the
Supervisor's, tell it, or edit `model` in `.orchestra/machines/supervisor/machine.toml`.

On the first `orchestra run` the agents' host writes the Supervisor's spec (its duties, its role and
a ledger of why each duty exists) and commits it to `.orchestra/machines/supervisor/`: on the base
branch when the checkout is on it and clean and the work is not code; otherwise on the side branch
`orchestra/supervisor`, through git's plumbing, without touching your working tree. Workers and
reviewers run with a push guard (no `git push`, no pull-request writes from their sessions); the
Supervisor's shell has none, and its own git writes are its tools' local commits.
On a project that is not set up yet (no task system `[project]`, no instruments), the Supervisor is
woken at once to set it up: it reads the operator's guides Orchestra and each plugin serve, explores
the repository read-only (how its tests run, how it builds and is served), sets what is plainly
right (the task system's `[project]`, the test-report instrument, the gate's stages), each a commit
of `orchestra.toml` with its reason, and leaves you one message: what it set and why, and the few
choices that are yours (whether the agents may change code, pushing, the model and its cost), each
with its recommendation. Answer it on the Agents page, or in the terminal:

    orchestra agents chat        # the Supervisor; Ctrl-D leaves
    orchestra agents status      # the host, what it runs the agents on, what each agent is doing

Ask it for what you want: "measure the open orders every minute" (it sets up and checks the
collector itself), "re-check the setup", or work ("add a page that lists the orders"), which it
hands to the Coordinator as requirements; the Coordinator plans the milestone and tickets, the
workers and reviewers do them, and the Supervisor tells you the plan and when it is done. You make
no tickets yourself. To choose the harness and model yourself, tell the Supervisor, or set
`[agents]` (the agents' guide, `orchestra_agents/guide.md`, says what each setting does):

    [agents]
    harness = "claude_code"      # pi | claude_code | codex | dummy
    model = "haiku"
    serves = ["worker", "reviewer"]   # the task system's roles these agents fill
    tools_via = "auto"           # mcp | bash | auto: how agents call Orchestra's tools

If your organisation's Claude Code policy blocks MCP servers it has not listed, the agents call
Orchestra's tools from their shell instead (`orchestra agents tool <name> '<json>'`): `auto`, the
default, switches when Claude Code reports the server blocked; `bash` never starts it. Tell the
Supervisor "use bash for tools", or set `tools_via = "bash"`.

The host follows a change of harness or model once no turn runs. `enabled = false` turns the
agents off.

If your tasks live seconds, set `[discovery] step = "5s"` (with a window of 30–60 m) in
`orchestra.toml` too: Orchestra's default grid is 30 s, and a level that changes inside one
step is not a stock.

`orchestra run` starts the agents' host (`orchestra agents run --wait`, the process
`agents.host`, run with Orchestra's own Python) because the plugin declares it for Orchestra's
launcher (with or without an `[agents]` section), keeps it running and stops it with itself, giving
it 30 s to shut down; its output is in `~/.orchestrator/<key>/logs/machines/agents.host.log`. To
run the host by hand instead, leave `orchestra run` out and run `orchestra agents run`; two hosts
of one project refuse to run side by side (with `--wait`, the second waits for the first to stop).
The agents' tools call back into Orchestra's Python by its full path, which the host gives each
session, so a session's own PATH never matters.

A measurement the project wants taken over time is an instrument the Supervisor adds when you ask:
a one-liner over tools already there (`git grep`, `sqlite3`, `curl`) it tries once as Orchestra
would, declares under `[[collect]]` itself and reads back once Orchestra stored its first points;
a `[[scrape]]` of a URL that already serves Prometheus metrics; a `[[sources]]` read of another
metrics store; or, when it takes a script, a measurement script of at most 100 lines that the
Supervisor writes itself, tries, and keeps in the state directory
(`~/.orchestrator/<key>/agents/instruments/<name>.sh`), with its entry in your private overlay and
nothing committed. It records at once as a draft and is removed after a day (`[agents]
draft_expiry`) unless you keep it. A longer program, or one you want in the repository, is a
worker's, through the Coordinator (Orchestra's `docs/machines.md`).

The Supervisor's `design` tools are how you design the system by talking: it declares the stocks,
effects, targets and machines you name, measures them at once, drafts and refines machines as you
talk, and starts one only when you confirm it (Orchestra's `docs/proposals/machine-design.md`).
The declarations live only in Orchestra's declared layer (its intended model), written through the
running host's `/intended` endpoint; while `orchestra run` is down every design call is refused and
nothing is written anywhere else. The Dynamics page draws the stocks, effects and flows and the
Machines page the machines, and the Supervisor's answers name the page that draws each.

The Supervisor is also the project's operator. It reads the whole system (orchestra.toml, the
processes Orchestra runs and their logs, the metrics catalogue, the model, the task system's
snapshot) and, when the setup cannot do what is asked, changes `orchestra.toml` with its setup
tools: which roles the AI agents fill, the kind of work, the gate's tests, the capacity limit,
`[[machines]]`, `[[collect]]` and `[[scrape]]` entries, and restarting a program. A change for the
team is a commit of `orchestra.toml` alone on the base branch with the reason; one for you alone
(and every instrument it adds while designing) goes in your private overlay and is committed
nowhere. A change of an instrument (one added, kept, removed or expired) is announced on the bus as
`instruments.changed`, any other as `config.changed`, and it tells you in the chat what it changed
and why. It never changes code: that is a
worker's, through a ticket, a review and the merge gate.

With the task plugin in the same `orchestra web`, a ticket's drawer lists every agent turn that
worked on it (the worker's turns, each review round, the Coordinator's and the Supervisor's turns
about it), each with its outcome and its transcript a click away, while it runs and after it is
done; a ticket link on a transcript leads back. Without it, the agents still run, but a ticket
link on the Agents page opens no ticket drawer and no banner says a ticket is blocked.

State is in `~/.orchestrator/<key>/agents/`; custom roles, agents and teams in the repository
under `.orchestra/`, except a machine you designed with the Supervisor and confirmed, whose spec,
role and agent records stay in the state directory (`machines/<name>/`). The Supervisor designs the organisation in conversation; the Agents page
shows it.

## The Workflows floor

With the Workflows plugin installed (the `workflows` extra; a private repository), the agents also
answer the floor's offers: for each `offer.made` to a role served here, the host claims it as an
instance of the agent, runs the agent's turn on the offer's brief and finishes it with the outcome
the agent reported (SPEC.md §3.1). A floor role is served by the built-in worker or reviewer when
`[agents] serves` lists it, or by a confirmed Universal Machine whose spec names it (`role =
"<role>"`, which you design with the Supervisor). The floor's `attention.needed` wakes the
Coordinator or the Supervisor. `tests/e2e/test_floor_e2e.py` runs it end to end against a
scripted floor on the bus (a host process on the dummy harness), and the workflows repository's
`examples/task_loop` is a loop to try it on.

## Dummy agents

`harness = "dummy"` runs every agent from a script instead of a model: each turn plays the first
matching rule of the file `[agents] dummy_script` names (TOML), then the built-in rules, so the
setting alone is a working team (a worker commits a token file and reports ready, a reviewer
approves, the Supervisor hands a chat message to the Coordinator, which plans it into a milestone and
a ticket). Without the tasking plugin there are no tickets: the Supervisor hands nothing on and
says it changed nothing and why. A rule matches on the role, the ticket, a
pattern in the prompt, the turn's number and whether tasking is installed, and its steps write files, run commands, commit,
call the agents' tools, wait or fail:

    [[rule]]
    role = "reviewer"
    turn = 1                     # the first review of each ticket is a rejection
    steps = [{ tool = "report_review", args = { verdict = "REJECTED", summary = "Not yet.", feedback = "1. Add a test." } }]

The tools go through the same calls as a model's, so a test on dummy agents tests everything but
the model. The format: SPEC.md §6 (the dummy harness, its **Dummy** entry), and `demo/dummy.toml`
as a worked example.

## Tests

    uv run python -m pytest -q         # about twenty minutes: unit, in-process e2e per harness, the demo through a real Orchestra
    uv run ruff check orchestra_agents tests demo

The Supervisor's workflow tools (`declare_workflow`, `list_workflows`, `explain_item`, and
`request_work`, which the Coordinator has too) and the floor need the Workflows plugin,
`orchestra-workflows`, which is optional at runtime: it is imported lazily and named in no runtime
extra of this package (Orchestra's `workflows` extra installs it). Installed beside the agents, the
tools use it; without it they say it is not installed. For the tests, the `dev` extra installs it
and `uv.lock` pins it: the unit tests of the tools use a fake plugin, and
`tests/e2e/test_workflows_e2e.py` and `tests/e2e/test_request_work_e2e.py` (the task loop
example's target, the owner's request through the Supervisor's chat on the dummy harness) run on
the real one (`explain_item` on an item log its recorder wrote).

The design tools' tests run on Orchestra's own declared layer (`orchestra.sd.intended`), served
in process through its host's events endpoint; `uv.lock` names the Orchestra commit they ran on.

Every e2e test uses the fakes in `tests/e2e/` (one per harness, speaking the product's real
protocol) or the dummy harness; nothing in the default test paths calls a model. Three opt-in live
tests under `tests/live/` do, on Haiku only, each skipped unless its variable is set:
`ORCHESTRA_LIVE_DESIGN=1 uv run python -m pytest -q -s tests/live/test_design_live.py` (its free
rehearsal, `ORCHESTRA_LIVE_DESIGN=rehearse`, plays the same scenario on a fake Claude, and runs in
every suite as `tests/e2e/test_design_rehearsal_e2e.py`), and
`ORCHESTRA_LIVE_HAIKU=1` for `tests/live/test_integrate_live.py` and
`tests/live/test_modeling_live.py`. `tests/e2e/test_dummy_demo_e2e.py`
runs the whole demo on dummy agents with no API key and no network. Its
`test_what_the_owner_sees` lands twelve tickets, five of them rejected once (a rule appended to
the target's `dummy.toml`), and checks what the owner would see: `task_scheduler` is in no
event's source and no machine, edge or hand-off of `/api/machines`; a reworked ticket's hand-offs
are planner → coordinator → worker → reviewer → worker → reviewer → merge_gate (the first offer is
the task system's dispatch, an instance of the coordinator); discovery hypothesises no flow
on `task_scheduler`; and the ticket metrics arrive with `instrument=tasks.tickets` (the
instrument the task system's plugin declares, `orchestra tasks metrics`). Its `test_the_agents_host_is_no_machine` has the
Supervisor hand a request to the Coordinator, which files the ticket, and checks that no machine, edge or event is `agents`, that the
Supervisor announces what the host watches for it (merge_gate's `gate.landed` reaches it on the
map), that it is woken when its ticket lands, and that the agents' metrics arrive with
`instrument=agents.turns`; `tests/unit/test_host_is_no_machine.py` is the same rule in process.

## Developing against a local Orchestra

`pyproject.toml` requires `orchestra>=1.0.0a1,<1.0.0rc0` and takes it from GitHub's `main`
(`[tool.uv] override-dependencies`; installed as a plugin, it gets the Orchestra it is installed
with), and the
dev extra takes the task system, the bridges and the Workflows plugin from GitHub over SSH (they
are private). `uv.lock` pins all four commits, so nothing needs cloning beside this repository.
`uv lock --upgrade-package orchestra` (or `orchestra-tasking`, `orchestra-bridges`,
`orchestra-workflows`) moves a pin to the newest `main`, then `uv sync --extra dev`.

To run against local checkouts instead, with their uncommitted changes, lay them over this
environment for one command:

    uv run --with-editable ~/orchestra_2 --with-editable ~/orchestra-tasking python -m pytest -q

Nothing is written: `.venv`, `pyproject.toml` and `uv.lock` keep the pinned commits, and a plain
`uv run` goes back to them.

To keep a checkout as this environment's Orchestra, point `pyproject.toml` at it,
then `uv sync --extra dev`; leave that change, and the `uv.lock` it rewrites, out of commits
(`git checkout pyproject.toml uv.lock`, then `uv sync --extra dev`, goes back):

    [tool.uv]
    override-dependencies = ["orchestra[sd]"]
    sources = { orchestra = { path = "../orchestra_2", editable = true } }   # the path to your Orchestra checkout
