# The agents' demo

A project with nothing of its own: Orchestra, the task system (orchestra-tasking), the agents
(orchestra-agents) and one plain program. The reviewer is an AI agent on Claude Code with Haiku;
the worker is a program that runs shell commands (`machines/shell_worker.py`, a copy of the task
system's). Both work the same queue. From the orchestra-agents repository:

    uv run demo/run.py                 # copies this folder to ~/orchestra-agents-demo and starts everything
    uv run demo/run.py /tmp/d --port 8072

Ctrl-C stops everything. To start over, remove the target directory and run it again: `run.py`
removes the state a previous run left for that path (under `~/.orchestrator/<name>-<hash>/`)
before it starts. The reviews cost a few cents; `CLAUDE_BINARY=tests/e2e/fake_claude.py` runs
the demo with a fake that approves everything, as the e2e tests do.

## What runs

| Process | What it is |
|---|---|
| `orchestra run` | Orchestra's host: the Core strip shows it up, and it rediscovers the system dynamics model from the metrics |
| `orchestra web --port 8072` | Orchestra's web UI with both plugins: the task system's Tickets page, Tasks panel and scheduler card; the agents' Agents page, Supervisor button and agent cards |
| `tasking run` | the task system's scheduler: offers ready tickets to the roles over the bus, applies what comes back |
| `orchestra-agents run` | the agents' host (`agents`): the Supervisor and the Coordinator resident, a reviewer per ticket (`reviewer/1`), the Supervisor chat |
| `machines/shell_worker.py` | the machine `worker` (`worker/1`): runs the `sh` code blocks of a ticket's description in its workspace, writes RESULT.md |

`orchestra.toml` has Orchestra's sections (the file bus), the task system's (`[project]` with
`work = "tasks"`, `[tasks]`) and `[agents]` with `serves = ["reviewer"]`: the worker role is the
program's.

## What you will see

`run.py` creates one milestone and three tickets as a planner would (requests over the bus).
The worker does each in a few seconds; the reviewer takes ten to thirty seconds with a real
model, so the Tasks panel shows `reviewer/1 · since Ns · <what it is doing>` on the ticket's
card while it holds it. With Haiku a review may be rejected, and the worker, which cannot act on
prose, reports the ticket BLOCKED; a banner then says a ticket waits for a decision.

Where to look, at http://127.0.0.1:8072:

- **Dashboard**: the Machines panel lists the Supervisor and the Coordinator (AI agents, resident),
  the host `agents`, the reviewer with the ticket it is on and what it is saying, and the
  worker (a program) beside the task system's scheduler; the Tasks panel shows each ticket in
  flight with the instance that holds it, since when, and its last words. The Supervisor button
  in the navigation opens the chat.
- **Agents**: the event map (the task system's scheduler offers `task.assigned`, the reviewer
  answers `task.accepted` and `task.reported`, attention events wake the planners), the
  scheduled wake-ups, the `engineering` team with the Coordinator and the reviewer, the
  Supervisor outside a team, the roles. Click an agent for its definition, tools, prompt and
  runs; a transcript for what it said.
- **Supervisor** (the button on every page): the chat with the resident Supervisor. Ask it what is
  going on; it reads the task system's snapshot and the events since its last turn. A message
  it leaves while you are away is a banner and a count on the button until you read it.
- **Machines**: `agents` (package agents, with its log), `supervisor` and `coordinator`
  (AI agents, continuous and scheduled), `reviewer` with its instance `1` (ad hoc, a session per
  ticket), `worker` as observed from the bus (a program, no package), `task_scheduler` (package
  tasks), `planner` (run.py's one-shot requests), `orchestra-host`; and the event map between
  them.
- **Tickets**: the task system's page; click a ticket for its description, acceptance, notes
  (the worker's summary, the reviewer's verdict with its reasons).
- **Dynamics**: a thin model (three tickets in a minute give discovery little); the agents'
  series `agents_working{role="reviewer"}` and the turn counters are there for it.

Logs: `run/host.log`, `run/web.log`, `run/scheduler.log`, `run/agents.log`, `run/worker.log` in
the target; the agents' own under `~/.orchestrator/<name>-<hash>/agents/logs/` (the host's
`agents-host.log`, which the Machines page opens, and each session's stderr and starting prompt).
