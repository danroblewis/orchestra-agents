# The agents' demo

A project with nothing of its own: Orchestra, the task system (orchestra-tasking), the agents
(orchestra-agents) and one plain program. The reviewer is an AI agent on Claude Code with Haiku;
the worker is the task system's own program worker (`machines/shell_worker.py`, copied into the
target by `run.py` from the orchestra-tasking checkout the `dev` extra installs). Both work the
same queue. From the orchestra-agents repository:

    uv run demo/run.py                 # copies this folder to ~/orchestra-agents-demo and starts everything
    uv run demo/run.py /tmp/d --port 8072 --tickets 3     # a two-minute look instead of the quarter-hour

Ctrl-C stops everything. To start over, remove the target directory and run it again: `run.py`
removes the state a previous run left for that path (under `~/.orchestrator/<name>-<hash>/`)
before it starts. The default run (twelve tickets, one every 90 s: `--tickets`, `--every`) costs
about ten cents in Haiku calls: a review per ticket, and the Coordinator's and Supervisor's turns
on the two tickets that need them (a run in October 2026: 1.7 M tokens, nearly all cache reads). While the demo is up the Supervisor costs
a call per chat message or escalation and the Coordinator one per attention event (it has no
schedule here: `coordinator_every` is not set); Ctrl-C when done.
`CLAUDE_BINARY=$PWD/tests/e2e/fake_claude.py uv run demo/run.py` runs the demo with a fake
reviewer that checks the acceptance criteria against the workspace, as the e2e tests do.

## What runs

| Process | What it is |
|---|---|
| `orchestra run` | Orchestra's host: the Core strip shows it up, and it rediscovers the system dynamics model from the metrics |
| `orchestra web --port 8072` | Orchestra's web UI with both plugins: the task system's Tickets page, Tasks panel and scheduler card; the agents' Agents page, Supervisor button and agent cards |
| `tasking run` | the task system's scheduler: offers ready tickets to the roles over the bus, applies what comes back |
| `orchestra-agents run` | the agents' host (`agents`): the Supervisor and the Coordinator resident, a reviewer per ticket (`reviewer/1`), the Supervisor chat |
| `machines/shell_worker.py` | the machine `worker` (`worker/1`): runs the `sh` code blocks of a ticket's description in the ticket's own empty folder (`ORCHESTRA_REPO` names the repository for a command about it), writes RESULT.md |

`orchestra.toml` has Orchestra's sections (the file bus), the task system's (`[project]` with
`work = "tasks"`, `[tasks]`) and `[agents]` with `serves = ["reviewer"]`: the worker role is the
program's. The tickets in `run.py` are written the way the task system's README prescribes for
its worker ("Writing tickets for the demo's worker").

## What you will see

`run.py` creates one milestone and twelve tickets as a planner would (requests over the bus),
one every 90 seconds, so the queue moves for a quarter of an hour. The worker does each in a few
seconds; the reviewer takes ten to twenty seconds with a real model, so the Tasks panel shows
`reviewer/1 · since Ns · <what it is doing>` on the ticket's card while it holds it. Two tickets
are written to go wrong:

- **DEMO-4 Record the SHA-256 of the README** computes a CRC. The reviewer rejects it; the
  worker (a program, which cannot act on prose) reports it BLOCKED with the reviewer's words;
  the attention event wakes the Coordinator, which changes the command to `shasum -a 256`,
  notes why and unblocks it; the worker runs it again and the reviewer approves. The banner
  shows for the ten or so seconds the ticket is blocked.
- **DEMO-5 Describe the event bus in prose** has no commands. The worker reports it BLOCKED;
  the Coordinator finds nothing a command would fix and escalates to the Supervisor, which
  leaves you a message: the banner and the count on the Supervisor button stay until you read
  it, and the ticket stays BLOCKED until someone decides.

Where to look, at http://127.0.0.1:8072:

- **Dashboard**: the Machines panel lists the Supervisor and the Coordinator (AI agents, resident),
  the host `agents` (`running · fills reviewer`), the worker (a program) and the task system's
  scheduler. Orchestra lists an ad hoc machine while it is active and for two minutes after its
  last event: the reviewer appears with the ticket and what it is saying while it holds one, and
  stays listed (with an idle chip) for two minutes after a review; `run.py`'s one-shot `planner`
  is listed for two minutes after it ran. The Tasks panel shows each ticket in flight with the
  instance that holds it, since when, and its last words. The Supervisor button in the navigation
  opens the chat.
- **Agents**: the event map (the task system's scheduler offers `task.assigned`, the reviewer
  answers `task.accepted` and `task.reported`, attention events wake the planners), the
  `engineering` team with the Coordinator and the reviewer, the Supervisor outside a team, the
  roles. Click an agent for its definition, tools, prompt and runs; a transcript for what it
  said.
- **Supervisor** (the button on every page): the chat with the resident Supervisor. Ask it what is
  going on; it reads the task system's snapshot and the events since its last turn. A message
  it leaves while you are away is a banner and a count on the button until you read it.
- **Machines**: `agents` (package agents, with its log), `supervisor` and `coordinator`
  (AI agents, continuous), `reviewer` with its instance `1` (ad hoc, a session per ticket; its
  card lists the tickets it is on),
  `worker` as observed from the bus (a program, no package), `task_scheduler` (package tasks),
  `planner` (run.py's one-shot requests), `orchestra-host`; and the event map between them.
- **Tickets**: the task system's page; click a ticket for its description, acceptance, notes
  (the worker's summary, the reviewer's verdict with its reasons).
- **Dynamics**: nothing for the first ten minutes. A shell ticket's whole life (offered, worked,
  reviewed, done) fits inside one 30 s discovery step, so the demo sets `step = "5s"` with a 60 m
  window in `orchestra.toml` (`[discovery]`): at 5 s the ticket levels move across several steps
  and can be stocks, and a level needs five such steps. The host rediscovers every five minutes;
  with the twelve tickets done (a run of 26 October 2026) it found five stocks: the tickets
  OPEN, READY_FOR_REVIEW, IN_REVIEW and DONE, and `agents working (reviewer)`, balanced by the
  reviewer's own turn counters; nine fitted flows (tickets created, the transitions between the
  states, the reviewer's turns started and finished); and two machines, `task_scheduler` and
  `reviewer`, each attributed its own counters. IN_PROGRESS is never a stock: the worker is done
  within one step. The page's "how it was found" lists every series left out and why. With
  `--tickets 3` there is nothing to balance and the page stays empty.

Logs: `run/host.log`, `run/web.log`, `run/scheduler.log`, `run/agents.log`, `run/worker.log` in
the target; the agents' own under `~/.orchestrator/<name>-<hash>/agents/logs/` (the host's
`agents-host.log`, which the Machines page opens, and each session's stderr and starting prompt).
