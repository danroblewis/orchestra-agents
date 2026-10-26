# The agents' demo

A project with nothing of its own: Orchestra, the task system (orchestra-tasking), the agents
(orchestra-agents) and one plain program. The reviewer is an AI agent on Claude Code with Haiku;
the worker is the task system's own program worker (`machines/shell_worker.py`, copied into the
target by `run.py` from the orchestra-tasking checkout the `dev` extra installs). Both work the
same queue. From the orchestra-agents repository:

    uv run demo/run.py                 # copies this folder to ~/orchestra-agents-demo and starts everything
    uv run demo/run.py /tmp/d --port 8072 --tickets 3     # a one-minute look instead of the four minutes

Needs: Claude Code installed and signed in, with `claude` on the PATH or named by
`CLAUDE_BINARY`; or the fake, `CLAUDE_BINARY=$PWD/tests/e2e/fake_claude.py uv run demo/run.py`,
a reviewer that checks the acceptance criteria against the workspace, as the e2e tests do (its
Coordinator and Supervisor only acknowledge, so a blocked ticket stays blocked). `run.py` refuses
to start without one or the other.

Ctrl-C stops everything. To start over, remove the target directory and run it again: `run.py`
removes the state a previous run left for that path (under `~/.orchestrator/<name>-<hash>/`)
before it starts. The default run (fifteen tickets: eight at once, then one every 30 s;
`--tickets`, `--burst`, `--every`) costs about ten cents in Haiku calls: twenty reviews (five
tickets are reviewed twice), the Coordinator's turns on the five rejected and the three blocked
tickets, and the Supervisor's on the three escalations (a run with these defaults on 26 October
2026: 3.4 M tokens, 2.9 M of them cache reads, in four minutes of work). While the demo is
up the Supervisor costs a call per chat message or escalation and the Coordinator one per
attention event (it has no schedule here: `coordinator_every` is not set); Ctrl-C when done.

## What runs

| Process | What it is |
|---|---|
| `orchestra run` | Orchestra's host: the Core strip shows it up, and it rediscovers the system dynamics model from the metrics |
| `orchestra web --port 8072` | Orchestra's web UI with both plugins: the task system's Tickets page, Tasks panel and scheduler card; the agents' Agents page, Supervisor button and agent cards |
| `tasking run` | the task system's scheduler: offers ready tickets to the roles over the bus, applies what comes back |
| `orchestra-agents run` | the agents' host (`agents`): the Supervisor and the Coordinator resident, a reviewer per ticket (`reviewer/1`), the Supervisor chat |
| `machines/shell_worker.py` | the machine `worker` (`worker/1`): runs the `sh` code blocks of a ticket's description in the ticket's own empty folder (`ORCHESTRA_REPO` names the repository for a command about it), writes RESULT.md |

`orchestra.toml` has Orchestra's sections (the file bus; `[discovery]` on a 5 s grid), the task
system's (`[project]` with `work = "tasks"`, `[tasks]` with one reviewer and a 1 s tick) and
`[agents]` with `serves = ["reviewer"]`: the worker role is the program's. The tickets in
`run.py` are written the way the task system's README prescribes for its worker ("Writing
tickets for the demo's worker"): every one's commands run for at least ten seconds (`sleep 10`
stands for work where there is none), so a ticket is seen in progress.

## What you will see

`run.py` creates one milestone and fifteen tickets as a planner would (requests over the bus):
eight at once, then one every 30 seconds, so a queue forms in front of the one reviewer and the
run lasts about four minutes. The worker takes ten seconds per ticket, two at a time; the
reviewer six to fifteen seconds with a real model, so the Tasks panel shows `reviewer/1 · since Ns · <what
it is doing>` on the ticket's card while it holds it and three or four tickets waiting for
review behind it. Eight tickets are written to go wrong:

- **DEMO-4 Add the numbers one to ten**, **DEMO-5 Write the status file**, **DEMO-7 Make a
  folder with two files**, **DEMO-9 Name the configuration file** and **DEMO-13 Take leave**
  each run a command that does not produce what the acceptance asks for (`seq 1 9` for the sum
  to ten, `pending` where `done` is wanted, one file where two are named, `ls *.yaml` where the
  file is a `.toml`, `hello again` for a farewell). The reviewer rejects; the worker (a program, which cannot act on prose) reports the
  ticket BLOCKED with the reviewer's words; the attention event wakes the Coordinator, which
  changes the command and unblocks it; the worker runs it again and the reviewer approves. The
  banner shows for the ten or so seconds each ticket is blocked.
- **DEMO-6 Describe the event bus in prose**, **DEMO-11 Decide the project's name** and
  **DEMO-15 Write a haiku about the bus** have no commands. The worker reports each BLOCKED;
  the Coordinator finds nothing a command would fix and escalates to the Supervisor, which
  leaves you a message: the banner and the count on the Supervisor button stay until you read
  it, and the tickets stay BLOCKED until someone decides.

Where to look, at http://127.0.0.1:8072:

- **Dashboard**: the Machines panel lists the Supervisor and the Coordinator (AI agents, resident),
  the host `agents` (`running · fills reviewer`), the worker (a program) and the task system's
  scheduler. Orchestra lists an ad hoc machine while it is active and for two minutes after its
  last event: the reviewer appears with the ticket and what it is saying while it holds one, and
  stays listed (with an idle chip) for two minutes after a review; `run.py`'s `planner` is
  listed while it creates tickets and for two minutes after. The Tasks panel shows each ticket
  in flight with the instance that holds it, since when, and its last words, and the tickets
  waiting for review. The Supervisor button in the navigation opens the chat.
- **Agents**: the `engineering` team with the Coordinator and the reviewer, each with what wakes
  it and what it emits, the Supervisor outside a team, the roles. Click an agent for its
  definition, tools, prompt and runs; a transcript for what it said. Who publishes and who
  listens for what is the Machines page.
- **Supervisor** (the button on every page): the chat with the resident Supervisor. Ask it what is
  going on; it reads the task system's snapshot and the events since its last turn. A message
  it leaves while you are away is a banner and a count on the button until you read it.
- **Machines**: `agents` (package agents, with its log), `supervisor` and `coordinator`
  (AI agents, continuous), `reviewer` with its instance `1` (ad hoc, a session per ticket; its
  card lists the tickets it is on), `worker` as observed from the bus (a program, no package,
  with the log `run.py` gave it), `task_scheduler` (package tasks), `planner` (`run.py`'s
  requests, a scheduled program), `orchestra-host`; the event map between them, and the
  exchanges and loops in words (`task_scheduler ⇄ reviewer`, `worker → task_scheduler →
  reviewer` …).
- **Tickets**: the task system's page; click a ticket for its description, acceptance, notes
  (the worker's summary, the reviewer's verdict with its reasons).
- **Dynamics**: the model appears after the run has finished: the host discovers five and ten
  minutes after its start, so leave the demo up until the page fills. A shell ticket's whole
  life (offered, worked, reviewed, done) fits inside one 30 s discovery step, so the demo sets
  `step = "5s"` with a 60 m window in `orchestra.toml` (`[discovery]`): at 5 s the ticket levels
  move across several steps and can be stocks, and a level needs five such steps.
  What the model is expected to show, with the tickets done: the ticket states as stocks (OPEN,
  IN_PROGRESS between OPEN and READY_FOR_REVIEW, READY_FOR_REVIEW, IN_REVIEW, REJECTED,
  BLOCKED, DONE as where the material ends) and the reviewer's sessions (`agents working
  (reviewer)`), balanced by the task system's own transition counters, which name the machine
  whose report moved the ticket; a queue in front of the one reviewer whose drain is
  proportional to what waits (the reviewer's turns start in proportion to READY_FOR_REVIEW); the
  rework loop IN_REVIEW → REJECTED → IN_PROGRESS → READY_FOR_REVIEW → IN_REVIEW and the block
  loop through BLOCKED → OPEN; every equation fitted, with the alternatives it beat recorded.
  The Supervisor is not in it: its three turns (one per escalation) are below discovery's bar of
  five events, so the escalation path (ticket blocked → Coordinator → Supervisor) is not in the
  model, and "how it was found" says so. One honest sentence: this is a model of the task pipeline itself, since the demo has no domain
  of its own, so no loop between a domain and its process can appear here (Orchestra's
  `docs/roadmap/use-cases.md`, UC-3b, is the sample that will have one). The page's "how it was
  found" lists every series left out and why. With `--tickets 3` there is nothing to balance and
  the page stays empty.

Logs: `run/host.log`, `run/web.log`, `run/scheduler.log`, `run/agents.log`, `run/worker.log` in
the target; the agents' own under `~/.orchestrator/<name>-<hash>/agents/logs/` (the host's
`agents-host.log`, which the Machines page opens, and each session's stderr and starting prompt).
