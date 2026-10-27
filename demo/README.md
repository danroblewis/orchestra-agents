# The agents' demo

A project with nothing of its own: Orchestra, the task system (orchestra-tasking), the agents
(orchestra-agents) and one plain program, working one queue of code tickets. Every ticket is a
branch, reviewed by an AI reviewer and landed on main through the merge gate. The worker role has
two kinds of machine: the task system's own program worker (`machines/shell_worker.py`, copied
into the target by `run.py` from the orchestra-tasking checkout the `dev` extra installs), which
takes only tickets written as shell commands, and AI workers on Claude Code with Haiku, which take
any. From the orchestra-agents repository:

    uv run demo/run.py                 # copies this folder to ~/orchestra-agents-demo and starts everything
    uv run demo/run.py /tmp/d --port 8072

Needs: Claude Code installed and signed in, with `claude` on the PATH or named by
`CLAUDE_BINARY`; or the fake, `CLAUDE_BINARY=$PWD/tests/e2e/fake_claude.py uv run demo/run.py`,
as the e2e tests do: its worker runs a ticket's `sh` blocks and makes the files the acceptance
names, its reviewer checks that they exist, its Coordinator and Supervisor only acknowledge.
`run.py` refuses to start without one or the other.

Ctrl-C stops everything. To start over, remove the target directory and run it again: `run.py`
removes the state a previous run left for that path (under `~/.orchestrator/<name>-<hash>/`)
before it starts. The AI agents cost money in Haiku calls: a worker's turns on each ticket it
takes, a review per ticket, and, while the demo is up, the Supervisor's turn per chat message or
escalation and the Coordinator's per attention event (it has no schedule here:
`coordinator_every` is not set). Ctrl-C when done.

## What runs

`run.py` starts two processes; Orchestra's host starts the rest and keeps them running.

| Process | What it is |
|---|---|
| `orchestra run` | Orchestra's host: starts what the plugins declare and `[[machines]]` names, and rediscovers the system dynamics model from the metrics |
| `orchestra web --port 8072` | Orchestra's web UI with both plugins: the task system's Tickets page, Tasks panel and scheduler card; the agents' Agents page, Supervisor button and agent cards |
| `tasking run --wait` | the task system's scheduler (the process `tasks.scheduler`, declared by its plugin): offers ready tickets to the roles over the bus, applies what comes back, lands reviewed branches through the merge gate |
| `orchestra-agents run --wait` | the agents' host (the machine `agents`, the process `agents.host`, declared by its plugin): the Supervisor and the Coordinator resident, an AI worker per ticket it takes (`worker/1`, `worker/2`), a reviewer per ticket (`reviewer/1`), the Supervisor chat |
| `machines/shell_worker.py shell --only-commands` | the program worker (`worker/shell`, from `[[machines]]`): answers only offers whose description gives `sh` blocks, runs them in the ticket's worktree and commits what they changed |

The program worker has its own instance, `shell`: the agents' host names its AI workers
`worker/1`, `worker/2`, and the scheduler knows a holder by its source. Both kinds answer a
command ticket's offer; the first to accept keeps it and the other is released.

`orchestra.toml` has Orchestra's sections (the file bus; `[discovery]` on a 5 s grid;
`[[machines]]`), the task system's (`[project]` with `work = "code"`, `[tasks]` with two worker
slots, one reviewer and a 1 s tick, the merge gate's `[[gate.unit]]`: every Python file compiles
and the tests pass) and `[agents]` with `serves = ["worker", "reviewer"]`. The Coordinator and
the Supervisor are told which machines, program or AI agent, have been seen filling each role.

`run.py` also writes a project context (`.orchestra/PROJECT.md`: the demo's objectives, value
propositions and non-goals), which the Supervisor starts from, so a request in the chat is acted on
at once rather than met with questions about what the project is for.

`python` in `[[machines]]` and in the gate's command is the first one on the PATH: `run.py` puts
the interpreter Orchestra and the plugins are installed in first. To start `orchestra run` by
hand, start it from that environment.

## What you will see

`run.py` creates one milestone and four tickets as a planner would (requests over the bus):

- **DEMO-1 Record the toolchain** and **DEMO-2 Record the day the project started** are shell
  commands (`notes/toolchain.txt`, `notes/started.txt`). The program worker or an AI worker
  takes each, whichever accepts first.
- **DEMO-3 Count the lines of the Python files** is a small tool and its test: only an AI worker
  can write it. The merge gate runs the test before it lands.
- **DEMO-4 Measure the repository** is a machine: a program that writes the repository's file
  and commit counts into the project's metrics store every 30 seconds, declared in
  `[[machines]]`, so Orchestra starts it once the branch lands on main (with the real Claude
  Code; the fake writes only placeholder files).

Where to look, at http://127.0.0.1:8072:

- **Dashboard**: the Machines panel lists the Supervisor and the Coordinator (AI agents,
  resident), the host `agents`, the worker, the reviewer and the task system's scheduler.
  Orchestra lists an ad hoc machine while it is active and for two minutes after its last
  event. The Tasks panel shows each ticket in flight with the instance that holds it, since when,
  and its last words. The Supervisor button in the navigation opens the chat.
- **Agents**: the `engineering` team with the Coordinator, the worker and the reviewer, each
  with what wakes it and what it emits, the Supervisor outside a team. Click an agent for its
  definition, tools, prompt and runs; a transcript for what it said.
- **Supervisor** (the button on every page): ask it what is going on; it reads the task system's
  snapshot and the events since its last turn. A message it leaves while you are away is a
  banner and a count on the button until you read it.
- **Machines**: `worker` with its instances (`shell`, the program; `1`, `2`, the AI workers),
  `reviewer`, `supervisor`, `coordinator`, `agents`, `task_scheduler`, `planner` (`run.py`'s
  requests), `orchestra-host`, and the event map between them.
- **Tickets**: click a ticket for its description, acceptance and notes (the worker's summary,
  the reviewer's verdict). `git log main` in the target shows each ticket landed.
- **Dynamics**: four tickets are too few events for discovery to find stocks (a counter needs
  five events to carry a balance), so the page says what it left out and why rather than show a
  model.

Logs: `run/host.log` and `run/web.log` in the target; what the host starts logs to
`~/.orchestrator/<name>-<hash>/logs/machines/`; the agents' own under
`~/.orchestrator/<name>-<hash>/agents/logs/` (the host's `agents-host.log`, which the Machines
page opens, and each session's stderr and starting prompt).
