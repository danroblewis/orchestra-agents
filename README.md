# orchestra-agents

AI agents as Orchestra machines. A host (`orchestra-agents run`) keeps the resident agents up
(the Supervisor the human talks to, the Coordinator that keeps the plan executable, any custom
agent), wakes them on events and schedules, and runs a worker or a reviewer on a ticket when a
task system (orchestra-tasking) assigns one over Orchestra's event bus. Which product runs a
session is a setting: Pi, Claude Code or Codex. An Orchestra plugin (`agents`) adds the Agents
page, the Supervisor chat, and the agents' machine cards to Orchestra's web UI.

The agents change no ticket themselves and manage no branches: every ticket change is a request
on the bus that the task system applies, and the task system prepares the workspace. The two
packages import nothing from each other; the contract is `orchestra_agents/protocol.py`.
`SPEC.md` is the design; `AGENTS.md` the rules.

## Try it

    uv sync --extra dev
    uv run demo/run.py                 # a fresh demo project at ~/orchestra-agents-demo, http://127.0.0.1:8072

An AI reviewer (Claude Code, Haiku: a few cents) and a program worker (a shell runner) work the
same queue of three tickets. `demo/README.md` says what you will see and where. Ctrl-C stops
everything; remove `~/orchestra-agents-demo` first to run it again. With `CLAUDE_BINARY` set to
`tests/e2e/fake_claude.py` it runs without a model.

## Use it on a repository

The task system must be set up first (`tasking init`, `tasking run`; see its README). Then, from
this repository's environment:

    cd <repo> && uv run --project <this repo> orchestra-agents --help
    # add to orchestra.toml:
    #   [agents]
    #   harness = "claude_code"      # pi | claude_code | codex
    #   model = "haiku"
    #   serves = ["worker", "reviewer"]   # the task system's roles these agents fill
    uv run --project <this repo> orchestra web                 # Orchestra's UI with the Agents page
    uv run --project <this repo> orchestra-agents run          # the host
    uv run --project <this repo> orchestra-agents chat         # the Supervisor, in the terminal

State is in `~/.orchestrator/<key>/agents/`; custom roles, agents and teams in the repository
under `.orchestra/`. The Supervisor designs the organisation in conversation; the Agents page
shows it.

## Tests

    uv run python -m pytest -q         # about four minutes: unit, in-process e2e per harness, the demo through a real Orchestra
    uv run ruff check orchestra_agents tests demo

Every e2e test uses the fakes in `tests/e2e/` (one per harness, speaking the product's real
protocol); nothing in the tests calls a model.
