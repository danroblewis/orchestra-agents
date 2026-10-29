# The agents

AI agents as Orchestra machines. This package runs them: it keeps the resident ones up (the
Supervisor, the Coordinator, any custom agent), wakes them on the events they listen for and on
their schedules, starts a worker or a reviewer on a ticket when a task system assigns one, and
shows them in Orchestra's web UI (an Orchestra plugin, `agents`). Which product runs a session
(Pi, Claude Code, Codex) is a setting; the agents behave the same on each. A fourth harness,
`dummy`, runs scripted agents with no model, so demos and the tests of everything around the
agents cost nothing.

It meets the task system (orchestra-tasking) only on Orchestra's bus. This file is the design;
`AGENTS.md` is the short list of rules that must not be broken.

**What the Agents plugin is** (the owner, 2026-10-27). A target repository normally defines its
own machines: its author writes programs that publish and listen on the bus under the machine's
own `source`, or a bridge relays another system's actors as machines. The Agents plugin is a
common resource for the same thing. It provides four things:

- an **implementation of a machine**: the AI agent, a universal machine that does whatever its
  role's prompt and tools make it do;
- the **means to configure one**: roles (its job and tools), the harness and the model that run
  it, how it is engaged (resident, scheduled, ad hoc; its `wake_on` events), and its permissions
  (tool groups, access, the team it answers to);
- a **shared runtime** that runs the configured agents: the host (`orchestra agents run`). The
  host is **not a machine**. It never announces itself, never publishes under its own name
  (`agents`) and writes no metrics; everything it does on the bus it does as the agent it is
  running (section 4);
- **UI additions** to Orchestra's web app: the Agents page, transcripts, the Supervisor chat and
  the agents' machine cards (section 8).

So the machines are the configured agents (`supervisor`, `coordinator`, `worker/1`, a custom
agent): each announces itself, publishes as itself and is known by what it publishes. The plugin
that provides them is never one of them.

## 1. What it is, and is not

- It knows **roles, agents, teams, sessions, transcripts, harnesses and tools**: who an agent is,
  what it is told, what it may call, which product runs it, and what it said.
- It does **not** know tickets. A ticket is what the task system said about it in an assignment's
  brief or a query's answer; every change to one is a request on the bus that the task system
  applies (section 3). It does no branching and no worktree management: the task system prepares
  the workspace and names it in the assignment; a reviewer reads the diff in it, nothing more.
- It imports nothing from the task system. The contract is the event names in
  `orchestra_agents/protocol.py`, the one source of truth on this side.
- Everything an agent does to the project is **visible on the bus**: its turns, its requests, its
  reports, with the ticket as subject and the agent's instance as source, so Orchestra's
  Machines page shows the agents as machines without reading any payload.
- It is **not a machine itself**, and neither is its host (above): Orchestra's Machines page shows
  the configured agents and nothing named `agents`.

## 2. The organisation (`builtins.py`, `roles.py`, `org.py`, `org_store.py`, `prompts/`)

Three things, kept apart:

- A **role** is a job description: a prompt in the second person and the tool groups the job
  needs. The built-in roles are in `builtins.py` with their prompts in `prompts/` (a
  `<role>.tasks.md` variant is used when the project's work is tasks, not code); custom roles
  are JSON and markdown under `.orchestra/roles/` in the project repository. A role record
  carries no schedule, wake rules or session: it is a job description, and an agent is what
  runs it.
- An **agent** is an instance of one or more roles in a team, and is what runs. Its kind says
  how it is engaged: `continuous` (a resident session, always up, woken by events), `scheduled`
  (every interval or at a daily time; resident or ephemeral), `ad_hoc` (a fresh session each
  time it is briefed or a subscribed event fires). Custom agents live under `.orchestra/agents/`.
- A **team** has one lead agent, members, responsibilities (standing conditions the lead checks),
  access grants, probes (commands on an interval, section 5),
  and a space under `.orchestra/spaces/<team>/` whose `tools/` is on its agents' PATH. Custom
  teams live under `.orchestra/teams/`. Of the access grants, tool groups are enforced: an agent
  gets only the tools of its groups (`ORCHESTRA_TOOLS`, read by every harness). Path grants are
  not: they are written into the agent's prompt ("Where you work") as instructions, and the
  harnesses run with their own permission checks off, so nothing stops an agent from reading or
  writing elsewhere in the checkout.

The built-in organisation is the Supervisor (no team; it talks to the human, sets the project up
and hands the human's requirements on) and the `engineering` team: the Coordinator leads it (it
plans what the Supervisor hands it into milestones and tickets), the worker and the reviewer are
its members. The
worker and the reviewer are not resident: a session of each is started per ticket, on the task
system's assignment, for the roles this package serves (`[agents] serves`). A role served by
another machine (the demo's shell worker) is not an agent here.

Every agent's prompt is its roles' prompts, then its team chain's responsibilities, then where
it works (`prompting.agent_prompt`). A built-in agent outside a custom team gets exactly its role
prompt. The prompt a session was started with is kept beside its log, so a transcript can show
what the agent was told whatever the harness records.

The Supervisor designs the organisation in conversation with the human (`prompts/org_guide.md`,
the `roles` tool group), and pauses, resumes, restarts, resets and deletes agents the same way;
the Agents page shows it. A change to a role or a team restarts the resident sessions of the
agents whose prompt it is part of (after their current turn), so a session always runs with
the organisation as stored, including a lead whose team was created after its own record. Writes under `.orchestra/` are committed to the base branch when the checkout is on
it and otherwise clean. When it is not (another branch, or other changes), the files are written
and left uncommitted, so the checkout is dirty until a human commits them; the tool's answer says
which happened.

## 3. Meeting the task system on the bus (`protocol.py`, `runner.py`, `tooling.py`)

All of it is CloudEvents on Orchestra's bus (`events.py` over `orchestra.events`); the names are
in `protocol.py`, and the task system implements the same names (its `tasking/assignments.py`
and `tasking/requests.py` hold the full field tables).

**Assignment** (`runner.py`, inside the host). Work passes directly between the machines that do
it; the task system listens, records, and takes back what a silent machine holds (the owner's
decision D17). An offer opens a turn: `task.assigned` from the task system's dispatch (assignment
id, role, ticket, workspace, branch and base or null, lease, the ticket's brief, the unit command
in code mode; a reviewer's carries `review` {summary, commit, test_output, worker}),
`review.requested` from the worker that finished (the same fields, for the reviewers), or
`changes.requested` from the reviewer that rejected, addressed `to` the worker instance that did
the work. For a role it serves, the host takes the offer as the instance of the ticket's session
(`worker-<id>` or `reviewer-<id>`), the lowest free one of the role (`worker/1`); an offer
addressed `to` an instance is taken only if that instance is this ticket's slot here. It claims it
(`task.accepted`) and reads its claim back from the bus before the session starts: the first
valid claim of an offer on the bus holds it (the bus's order decides a race, and the task system
records the same holder without a word to a loser), so an instance that loses spends nothing. It
then sends `task.working` at least every `HEARTBEAT_S` and sooner when what the agent is saying
has changed (the heartbeat's `text`: a line for the task system's Tasks panel), runs the turn,
and ends it as the agent decided: a worker's `report_ticket` READY_FOR_REVIEW asks the reviewers
directly (`review.requested`; in code mode after committing what the agent left, naming the
commit and the unit command's output on it; a branch with nothing beyond the base is reported
BLOCKED instead); a reviewer's `report_review` REJECTED asks the worker that did the work for
changes (`changes.requested`, to `review.worker`, leaving the worktree at the reviewed commit),
APPROVED goes to the task system's gate (`review.approved`); BLOCKED, or no report, is
`task.reported` (`failed` when the turn ended without one). Every event of a turn names the offer
as its `causationid`. A worker that asked for a review keeps its slot while the work is out, so
rejected work (the reviewer's, or the gate's, which the task system offers `to` the worker)
comes back to the same instance and the same session; the slot is let go when the ticket ends,
when another worker takes it, or when the task system takes the rejected work back to its queue
because it was not taken within its return timeout (as after a host restart, whose slots are
new). A `task.released` for its assignment kills the turn (`session.interrupted`, with the
reason; nothing is reported for an assignment the instance no longer holds). A finished ticket's
sessions move to the archive, still readable. A ticket has finished (`protocol.ended`) when the
gate lands it (`gate.landed`), in tasks mode when its reviewer approves it (`review.approved`:
nothing is merged), or when a planner's request is done whose result is the ticket in DONE or
CANCELLED (`request.done`, as a cancel). A
worker that redoes rejected work is reviewed again after every round: rejected work goes back to
its worker at once and the task system asks the Coordinator for attention only at every
`review_rounds_before_escalation`-th round; nothing in it stops the loop by itself (section 9).

**Requests** (`tooling.py`): an agent's ticket and milestone tools are requests on the bus
(`task.requested`, `task.change_requested`, `task.unblock_requested`, `milestone.requested`, …)
and queries (`task.query_requested` with an `op`: `list_tickets`, `get_ticket`, `snapshot`, …),
answered with `request.done` or `request.refused`; `TaskClient` waits for the answer. What a
request made is said too: `ticket.created`, `milestone.created`. Two tool groups divide them
(the owner, 2026-10-28: the human makes no tickets; they talk to the Supervisor, who conveys the
requirements; the Coordinator makes the milestones and tickets):

- `planning`: creating tickets and milestones, changing and closing milestones, and reporting a
  plan back (`report_plan`). The Coordinator's, and a custom agent's whose roles or team grant it.
- `tickets`: reading them (`list_tickets`, `get_ticket`, `project_status`, `list_milestones`) and the
  decisions on an existing ticket: `update_ticket`, `add_note`, `cancel_ticket`, `unblock_ticket`.
  The Supervisor's and the Coordinator's. The Supervisor keeps these directly because they are the
  human's intent, which is the Supervisor's to carry and which the Coordinator never changes: a
  priority the human sets, a cancel the human asks for, the objective, and an unblock after the
  Supervisor fixed the setup that blocked the ticket (it knows the blocker is gone; a round trip
  through the Coordinator would only delay it). Anything that changes what a ticket asks for, splits
  it or adds work goes through the Coordinator.

A worker or reviewer only reports.

**Hand-off** (`handoff.py`). The Supervisor's `hand_to_coordinator` (group `handoff`) publishes
`work.requested` {request, requirements, context, to: coordinator, by} as the Supervisor, its subject
the request's id (`r-…`). It wakes the Coordinator (`wake_on: work.requested:coordinator`), whose
wake-up carries the requirements whole. The Coordinator plans (a milestone, tickets with objective,
description, acceptance, verification and dependencies) and calls `report_plan`, which checks that
the request was handed on and not yet answered and that every ticket it names exists in the task
system (`list_tickets`), then publishes `work.planned` {request, to: supervisor, summary, milestone,
tickets [{id, title}], questions, by} as the Coordinator, its causation the hand-off. That wakes the
Supervisor (`work.planned:supervisor`), which tells the human; the tickets a plan names are followed
until they end (`landings.py`), so the Supervisor also tells the human when they are done. The
hand-offs are kept in the Supervisor's agent state (`handoffs`, the last 30).

**Attention**: the task system asks a planner to look at something with `task.attention_needed`
(`to`: supervisor or coordinator, the reason, the ticket), from the machine whose work is stuck
(`worker`, for a blocked ticket) or whose responsibility raised it (`reviewer`, `merge_gate`). The built-in planners wake on it like
any agent wakes on an event (`wake_on`: `task.attention_needed:coordinator`). The Supervisor also
wakes on Orchestra's `orchestra.process.gave_up` (its launcher stopped restarting a process): the
wake-up names the process, its last exit code and its log, `<state>/logs/machines/<name>.log`, and
its prompt has it read the log, fix a cause in the program's entry itself (section 10) and file a
bug ticket for a cause in the program.

**Sources, and no echoes.** The task system's scheduler is no machine: it never announces itself
and never publishes as `task_scheduler`. It publishes as the machine whose responsibility it
implements: offers as dispatch, an instance of the coordinator (`task.assigned` with source
`coordinator/dispatch`: choosing what is offered next is the coordinator's), releases of an offer
nobody claimed as dispatch too, and releases of a held turn as its role (`task.released` with source
`worker` or `reviewer`, no instance; a release addressed to an instance names it in
`data.instance`), answers as the asker (`request.done` and `request.refused`, with the request as
`causationid`; `ticket.created` and `milestone.created` for what a request made), the gate, staging and the pipeline as
`merge_gate`, probes as `probes`. It announces dispatch as `coordinator/dispatch` (`kind:
program`, `listens_for: [gate.conflict, gate.rejected, task.released, ticket.created]`, no
package): with the Coordinator agent (source `coordinator`, kind agent) it is one `coordinator`
machine on the Machines page, which lists what both listen for, says `agent ×1, program ×1`, and
keeps each instance's card apart, so the agent's harness and model stay on it. Nothing here reads an offer's
or a release's source: the claim rule (`protocol.holder`) is the bus's order of claims. And it
publishes no echo of another machine's act: there is no `ticket.transitioned`,
`ticket.note_added`, `ticket.updated`, `milestone.updated`, `milestone.closed` or
`scheduler.stopped`. What a machine did, that machine announced; a ticket's state is the task
system's record, asked for with the `get_ticket`, `list_tickets` or `snapshot` query, never heard.

**Waking on the task system.** An agent hears acts, not states. To wake when:

| ... | use (`wake_on`) |
|---|---|
| a ticket lands (code mode) | `gate.landed` |
| a ticket is done in tasks mode | `review.approved` |
| a worker is blocked | `task.reported:BLOCKED`, or `task.attention_needed:coordinator` (the task system asks the Coordinator) |
| a ticket is cancelled | `request.done:CANCELLED` |
| a ticket is created | `ticket.created` |
| work is rejected | `changes.requested` (a reviewer), `gate.rejected`, `gate.conflict` (the gate) |
| a probe fails | `probe.failed:<probe>` |
| what a ticket's state is now | no event: ask `get_ticket` |

An agent saved before 2026-10-27 may hold `ticket.transitioned` entries. Its record is read with
each retired entry replaced (`org.RETIRED`, `org.current_wake_on`): `:DONE` by `gate.landed`,
`:BLOCKED` by `task.reported:BLOCKED`, `:REJECTED` by `changes.requested`, `gate.rejected` and
`gate.conflict`, `:APPROVED` by `review.approved`, `:CANCELLED` by `request.done:CANCELLED`; any
other entry of a removed type is dropped (`org.GONE`). A new agent naming a removed type is
refused. A retired `:DONE` becomes `gate.landed` only, so in tasks mode such an agent should be
given `review.approved` as well.

## 4. The host (`host.py`, `residents.py`, `turns.py`, `control.py`, `cli.py`)

`orchestra agents run` is one process per project, and no machine: the runtime the configured
agents run in (section 1). Orchestra's launcher
starts it: the plugin declares it in `Plugin.processes` (`web.processes`) whether or not
`orchestra.toml` has an `[agents]` section (the owner, 2026-10-28: a new user configures nothing;
their Supervisor sets the project up), so `orchestra run` keeps it running in the interpreter
Orchestra runs in, as the process `agents.host`; `[agents] enabled = false` declares none. It is started with `--wait`: while another host holds the project, or the control port is
taken, it waits for that to clear instead of exiting, so a host restarted while the previous one
still shuts down takes over afterwards. Its shutdown closes the running turns together, waits up
to `SHUTDOWN_REPORTS_S` (10 s) for their reports, then closes the resident sessions together, each
close at most `closing.CLOSE_S` (5 s): 20 s at worst, under the 30 s stop grace it declares
(`web.STOP_GRACE_S`), so Orchestra's launcher never kills it mid-way. It:

- never announces itself and never publishes as itself (`events.py` refuses a publish as
  `agents`, or with no source). It announces every agent instance it starts, as that instance
  (an AI agent, on which harness and model; a resident agent is continuous, its session always
  up, a per-ticket or ephemeral one ad hoc), so Orchestra's Machines page shows each agent as a
  machine. Each announcement's `listens_for` is what the host watches on that agent's behalf
  (`listening.py`), so every subscription is drawn to the agent that reacts:

  | Agent | Listens for | Why |
  |---|---|---|
  | each agent | its `wake_on` types | its wake-ups |
  | each agent the host wakes (every resident, scheduled or ad hoc agent; not a worker or reviewer) | `request.done`, `request.refused` | the answers to its own requests: the snapshot each wake-up asks for as the agent, and its tools' requests. The task system publishes the answer as the asker (its D17) |
  | the Supervisor | `gate.landed`, `request.done`, and in tasks mode `review.approved` (`protocol.endings`) | the end of a ticket it created for the human, which it then tells the human about (`landings.py`) |
  | the Supervisor | `ticket.created` | how it remembers which tickets were its own (`by` = supervisor) |
  | the Supervisor, the Coordinator | `work.planned`, `work.requested` (their `wake_on`) | the hand-off of the human's requirements and its plan (section 3) |
  | a worker, a reviewer | `task.assigned`, `review.requested` or `changes.requested`, `task.released` | its role's `wake_on`: the offers it answers (runner.py) |

  What wakes an agent without an event on the bus is not listed (a human's message, another
  agent's brief or message, a crash loop: `builtins.Role.triggers`), and neither is what the host
  reads only for its own bookkeeping (a finished ticket's sessions go to the archive). The plugin
  declares the same agents and lists for the Machines page before they run (`web.declared`), and
  declares no machine for the host;
- opens the resident sessions up front and keeps them up: a process that exits between turns is
  recorded (`agent.crashed`) and restarted; three exits in fifteen minutes pause the agent
  (`agent.crash_loop`) and wake the Supervisor;
- each second: reloads the config, matches new events on the bus against every agent's
  `wake_on` (a filter after the colon matches a status, a probe name, the role an attention
  event is addressed to, the state a turn ended in (`task.reported`'s `report.state`: BLOCKED),
  the state a request left its ticket in (`request.done`'s `result.ticket.state`: CANCELLED), or
  an agent-raised event's subject; section 3's table), queues
  scheduled wake-ups, runs the teams' probes, answers assignments, and runs one turn per woken
  agent in a thread with the reasons, a snapshot of the project (a query to the task system)
  and who fills the task system's roles (`fillers.py`: the roles `[agents] serves`, and each
  instance seen on the bus accepting a role's assignment or announcing itself as one, with the
  `kind` it announced, so a planner never assumes the worker is a program or an AI agent).
  When the task system does not answer, the prompt says so instead of showing an empty project,
  and a wake-up that was only a schedule is skipped without a turn;
- bounds every turn: it ends when the agent settles, after `silence_timeout` with nothing from
  the harness, or at `turn_timeout` when set (`session.timed_out`, with what the agent was doing);
  a harness failure is `session.error`; a restart mid-turn is `session.interrupted`;
- bounds every session's context, in tokens as the harness's transcript records them (the
  tokens of its last model call; `Harness.context`), never by the transcript's size, which holds
  far more than the context: past `compact_tokens` a harness that compacts itself (Claude Code)
  is left to it, one that compacts on request (Pi) is asked, and one that cannot (Codex) has its
  session rotated (`agent.reset`, the old transcript archived as a run). `agent.compacted` is
  published once for each compaction the transcript records, whoever asked for it (Claude Code's
  `compact_boundary`, Pi's `compaction` entry, Codex's `compacted`), with the tokens before and
  after when the harness says; it always means a real compaction. The events that
  concern one session are also kept on its record, so its transcript shows them without a read
  of the bus;
- on start, refuses to run beside another host of the same project, then stops any turn the
  previous host left running (no orphans: every turn's process is recorded on its session from
  the harness's `turn_start`) and marks the cut. After a host crash the task system re-offers
  the assignment the dead host held when its lease runs out: the host reports nothing it did
  not see end. At a clean stop (Ctrl-C, a deploy restart) it closes every live turn and waits
  briefly for each open assignment's `task.reported` (`failed`) to go out, because the
  contract has no machine-sent release (`task.released` is the task system's verb only, published
  as its dispatch or as the role). The task
  system counts a worker's failed report against the ticket's crash budget (the ticket goes
  back to OPEN) and a reviewer's as a review that ended without a verdict (the review is
  redone, up to its no-verdict budget; `tasking/reports.py`);
- serves the Supervisor chat on a local control endpoint (`control.py`, 127.0.0.1, the port
  recorded in the runtime record, and the control token in `agents/control.token`, owner-only,
  asked for on every message: a page of another site could otherwise post to the port, around
  Orchestra's web login): one turn on the resident session per message, prefixed with
  the project context and who fills the roles when either changed, and a digest of the events
  since the last turn: a count per type and at most eight events that concern the Supervisor
  (attention addressed to it, a worker's turn ended blocked (`task.reported`, its
  report's state BLOCKED), the gate, staging, a process Orchestra gave up
  on, an agent's crash loop; `prompting.CONCERNS`), however busy the bus was, so a turn does not
  fill the session's context. The notices are marked as the Supervisor's alone, and the human's
  message follows whole after `[The human's message]` (`prompting.chat_turn`). Streamed as NDJSON.
  A reply that claims an action the turn did not take (a promise of a later action not tied to
  anything that will happen, or a claim of a change with no tool call; `promises.py`, a
  heuristic) gets one more turn on the session to take it or say it is not done; the human gets
  the correction after the reply. The same check runs on the Supervisor's wake-ups, whose reply
  is left for the human as a message. The prompt alone did not keep the replies true (a live
  test, 2026-10-27: "I have not done anything" right after two commits; two DONE tickets called
  in flight), so the Supervisor is given facts and its reply is checked against them, without
  the model. Every tool call that changes something is answered with what it changed (`done`: a
  commit's id, a ticket's id) and kept in the agent's state with the time, where every
  harness's calls pass (`tooling.handle`, `ledger.py`). In front of each Supervisor turn, chat or
  wake-up, go the changes its tools made since its previous turn began and the state, read from
  the task system just then, of the tickets it is dealing with (the ones it or the human named
  in recent turns, the ones it created for the human; `facts.py`). After the reply, a sentence
  saying nothing was done while the record shows changes made in that turn, or one placing a
  ticket it names on the wrong side of done (the snapshot read again), gets a correction written
  by Orchestra appended to the reply: the changes, or each ticket's true state (`truth.py`,
  narrow heuristics). The human sees it on the live chat and as an `agent.corrected` marker on
  the transcript. A session runs one turn at a time: a chat and a wake-up queue behind each other, a pause, restart or reset waits for the running one, and a paused
  Supervisor refuses the chat with the reason. The web plugin proxies it; `orchestra agents chat` attaches to it. A host that cannot
  bind the endpoint exits instead of running unreachable;
- wakes the Supervisor once, at start, when the project is not set up (`first_run.py`): no
  `[project]` and no instrument (`[[collect]]`, `[[scrape]]`, `[[sources]]`) in the configuration,
  and no setup recorded in the Supervisor's agent state (`setup`: {ran_at, harness}, written when
  the setup's turn ends without an error; a setup that ran on the dummy harness does not count once
  a real harness runs the agents). The wake-up gives the goal (understand the repository, configure
  Orchestra and the installed plugins for it), not steps; the Supervisor's prompt says what setting
  up means (section 10);
- follows `[agents]` harness, model, provider and thinking: when one changes, once no turn runs,
  the resident sessions are closed and open again on what is configured (a new conversation on the
  new harness);
- wakes the Supervisor when a ticket it created (`ticket.created` with `by` = supervisor: the
  human's requests) is DONE, to leave the human one short message (`landings.py`); the tickets are
  followed in the Supervisor's agent state until they end, as the runner hears it
  (`protocol.ended`: landed, approved in tasks mode, or cancelled). A landing is otherwise not
  named in the Supervisor's digest;
- answers `orchestra agents status` from its records (`org_views.agents_status`, the same
  picture the machine cards read): the host, the harness and model, the roles it fills, each
  resident agent's state and wake-ups, and the sessions on tickets.

Events the host and the agents publish: `session.started`, `session.settled`, `agent.said`,
`session.timed_out`, `session.error`, `session.interrupted`, `agent.crashed`, `agent.crash_loop`, `agent.corrected`,
`agent.compacted`, `agent.reset`, `agent.paused`, `agent.resumed`, `agent.messaged`,
`context.updated`, `probe.failed`, `probe.recovered`, `role.*`, `agent.*`, `team.*` on changes to
the organisation, `config.changed` and `machine.restarted` from the Supervisor's setup tools
(section 10), `work.requested` and `work.planned` (the hand-off, section 3), and a team's own declared types raised with `emit_event` (rate-limited, and
refused when the cause chain is three deep). The source of every one of them is an agent: a
session's events are the agent's instance (the slot bound to the session, or after the slot was
given back the instance its record names: a turn the previous host left running is cut as
`worker/3`, not as the host); an agent's tool calls are `ORCHESTRA_SOURCE`, the same instance; a
team's probe is the team's lead's (section 5). The host has no source of its own. The web plugin
publishes nothing.

Metrics (`measures.py`): the host writes none. It keeps the counters in its runtime record, moved
in the same write as the session's status, and the plugin declares the instrument that reads
them (`Plugin.collectors`, `web.collectors`): `orchestra run` runs `orchestra agents metrics`
every discovery step, which prints them as Prometheus text from the record, and Orchestra labels
every point `instrument=agents.turns` (and `project`). The same text is served at
`GET /api/plugins/agents/metrics`. The series: `agents_working{role, machine}` (every measured
role: the resident agents and the roles `serves`, even at 0) and the counters
`agent_turns_started_total{role, machine}`, `agent_turns_finished_total{role, machine}`, and, for an
agent with a machine spec, `agent_load{machine, signal}` (its overload signals as its last review
round measured them, section 12) and `agent_spend_dollars_total{machine}` (what its turns cost as
the harness reported it), named for what they measure: `role` names the series, and `machine` (the instance's machine, `worker` for
`worker/1`) is the measured system's own dimension, which discovery may read as attribution; the
model generator decides what is a stock or a flow. Until 2026-10-27 the host wrote these points
itself at each turn's start and end; the names and the `role` and `machine` labels are kept,
`instrument` is new, and the level is sampled each discovery step instead of at each change, so a
turn shorter than a step may not move it (the counters still count every turn).

## 5. Probes (`probes.py`)

A team may declare probes: a command with an interval, run in the team's space with the team
chain's tools on PATH. Exit 0 means the condition holds. The host remembers each probe's last
result and emits `probe.failed:<name>` and `probe.recovered:<name>` only on the transition, so an
agent subscribed to a probe wakes once per failure. The host is no machine, so the event is
published as the agent whose responsibility the team is: its lead, or the Supervisor for a team
with none. (The task system's own probes, `[[probes]]`
in `orchestra.toml`, are its; they are not these.)

## 6. Harnesses and tools (`harness/`)

A harness is the product that runs a session. Each implements the same interface
(`harness/base.py`): open a named, resumable session with a system prompt, a working directory
and tool groups; run one turn and stream events in one normalized vocabulary (`turn_start`,
`assistant_start`, `token`, `tool_start`, `tool_end`, `assistant_end`, `settled`, and `notice` when
the harness changed how it runs the agent, which the host logs and wakes the Supervisor with); name its
transcript, which is the only one kept (`read_transcript` parses it); kill a turn; close.

- **Pi** (`pi.py`): one `pi --mode rpc` process per live session, resident between turns; tools
  through the extension `pi_extension.ts`, which calls `orchestra agents tool`.
- **Claude Code** (`claude_code.py`): `claude -p` per turn, resumed by session id, the
  transcript under `$CLAUDE_CONFIG_DIR`; tools through the MCP server `harness/mcp.py`, or from
  the shell (`[agents] tools_via`, below). It
  compacts its own context (`--autocompact`). `CLAUDE_BINARY` names the binary to run.
- **Codex** (`codex.py`): `codex exec --json` per turn, resumed by thread id, the rollout under
  `$CODEX_HOME`; tools through the same MCP server, or from the shell with `tools_via = "bash"`.

**How tools reach the model** (`[agents] tools_via`). `mcp`: Orchestra's MCP server, as above.
`bash`: no MCP server (Claude Code keeps `--strict-mcp-config`, with an empty config, so no other
server loads either); the session runs `orchestra agents tool <name> '<json>'` from its shell
(`cli.cmd_tool`), and the system prompt gains a section naming the command, as it can be run
whatever the session's PATH and working directory, and each tool with one line
(`harness/bash_tools.py`; `--help` gives a tool's schema). Some organisations' Claude Code policy
allows only listed MCP servers; every session can run commands. `auto` (the default): MCP, until
the server is found blocked; then every Claude Code session of this host calls the tools from its
shell until it restarts (`mcp_status.Fallback`), said once as a `notice`. Blocked is: Claude Code's
init event reports the server not connected, or connected with none of its tools (a `permissions`
deny rule), and then that turn is stopped before the model runs and run again from the shell; or a
call to one of its tools comes back refused by Claude Code (a `PreToolUse` hook, as an
organisation's whitelist of MCP servers is often written, or a permission rule:
`mcp_status.refusal`), and then that turn is stopped at the refusal and the session is resumed from
the shell, told that its call was refused and to carry on from there. Claude Code keeps a resumed
session's system prompt as the session began (a new `--append-system-prompt-file` is not read), so
a session begun on the MCP server that now calls the tools from its shell (after the switch, or a
changed `tools_via`) gets the shell section in its next message instead, once (`SHELL_NOW`; the
session's metadata records which way its prompt says). With `mcp` a refused call
fails the turn as a server that did not connect does, below. The command
and the MCP server call through one path (`harness/caller.py`: the schema check, the session by
its token, `tooling.handle`), and the token reaches the command only through the session's
environment, never its prompt or output. A transcript shows a call from the shell as the tool it
called (`bash_tools.parse`). Pi calls its tools through its extension, which runs the same
command, and the dummy harness in-process; the setting changes neither.
Sessions of the Supervisor and the Coordinator are **read-only** (`builtins.Role.read_only`,
`SessionSpec.read_only`): they read, search and run commands in the checkout, and their harness
withholds its file-editing tools (Claude Code: `--disallowedTools Edit,Write,NotebookEdit`; Pi:
`--exclude-tools edit,write`). Codex has no per-tool switch short of a sandbox that would also stop
the agents' own tools from committing `orchestra.toml`, so there it is the prompt's rule only. The
shell stays: a command can still write, and the prompts say it must not.
Every Claude Code session, whatever its role, is also without the product's own tools that stand in
for Orchestra's work (`TaskCreate`, `TaskUpdate`, `TaskList`, `TaskGet`, `TodoWrite`, the `Cron*`
tools, `ScheduleWakeup`, `RemoteTrigger`: a private todo list and schedules that die with the
process); subagents (`Task`) stay. A Claude Code turn whose first event (`system`/`init`) does not
list the `orchestra` MCP server as `connected` is stopped there, before the model runs, as a
failed turn whose reason names the server's status, the managed settings that can block MCP
servers and the command to start the server by hand (`harness/mcp_status.py`); so is a turn whose
call to one of its tools Claude Code refuses (a hook or a permission rule), stopped at the refusal,
its reason naming the settings where hooks and permission rules live; it is a
`session.error` in the transcript, the host log and the attention banner. No Claude Code turn
starts again for five minutes, doubling to an hour, unless those settings change or the host
restarts. The MCP server that cannot load its tools exits non-zero and writes why to the session's
log. Every session's prompt ends with the rule: without Orchestra's tools, say so and stop.

- **Dummy** (`dummy.py`, `dummy_agent.py`, `dummy_script.py`): a scripted agent with no model, no
  network and no cost. Each turn is one `python -m orchestra_agents.harness.dummy_agent` process
  (in its own process group, like a `claude -p` turn, so a release kills it and a later host finds
  it as an orphan); it plays its role's rule from the project's script, calls the agents' tools
  the way the MCP server does (only the session's tool groups, `tooling.check_args`, then
  `tooling.handle` with the session's role), and writes its transcript,
  `sessions/dummy/<session>.jsonl`, in the common message shape. The host, the runner, the task
  system and the web cannot tell it from a model, so a test or a demo on it exercises everything
  but the model. It never compacts (its transcript records no tokens).

**Scripts.** A script is a TOML file of `[[rule]]` tables, named by `[agents] dummy_script`
(relative to the repository; re-read every turn). On each turn the first rule whose conditions
all hold is played; the built-in rules (`harness/dummy_defaults.toml`) come after the file's, so a
script says only what differs, and `harness = "dummy"` alone is a working team: a worker writes
`work/<ticket>.md` in a code ticket's worktree (`RESULT.md` in a task's folder), commits it and
reports READY_FOR_REVIEW; a reviewer approves; the Supervisor hands a chat message to the
Coordinator (`hand_to_coordinator`), which makes a milestone and a ticket from it and reports the plan
(`report_plan`), and the Supervisor tells the human the plan; on the first run the Supervisor says it
cannot read the repository and how to get one that can; any other turn replies "Nothing to do." or
that it is scripted. Without `[agents] dummy_script`, the repository's `.orchestra/dummy.toml` is the
script when it exists. A rule's conditions:
`role` (required; a role's or custom agent's name, or `"*"`), `ticket` (a glob on the ticket id),
`prompt` (a regular expression searched in the turn's prompt; its named groups are
`{match.<group>}` in the rule's steps and reply), `turn` (the session's nth turn,
counted from its transcript, or a list of them) and `checkout` (whether the working directory is a
git checkout). Its `steps`, in order: `say` (a message), `write` (a file, with `text` and
`append`), `run` (a shell command in the working directory; its output and status are the
result, and the script goes on), `commit` (everything, with this message; a commit even when
nothing changed), `tool` (an agents' tool, with `args`; a refusal is the result, and the script
goes on; `keep` names its answer, whose fields later steps of the turn read as
`{kept.<name>.<field>}`), `sleep` (seconds), `fail` (the turn fails with this error: `session.error`, and a
per-ticket turn reports `failed`). `reply` is the turn's last message (default: the last tool's
answer or `say`). `{ticket}`, `{role}`, `{turn}`, `{instance}` and `{message}` (the human's message
in a chat turn) are replaced in every string. A script that does not parse fails the turn with
the reason. The demo's script (`demo/dummy.toml`) is the worked example.

The tools are defined once, as data, in `harness/tools.py`, in groups; every harness exposes the
same catalogue to its model, and every call goes through `tooling.handle`, which checks it
against the caller's role before it runs (`access.py`):

- **Who is calling.** The host gives every session it starts a token,
  `ORCHESTRA_SESSION_TOKEN`, beside `ORCHESTRA_ROLE`, and records only the token's SHA-256 with
  the role and the ticket it was issued for (`runtime.json` `grants`). The MCP server and
  `orchestra agents tool`, the two ways a tool call reaches `handle` from outside the host, take
  the role and the ticket from that record (`access.identify`), never from the environment. The
  environment alone would be cosmetic: every agent has a shell, and a shell inside a worker's
  session could run `ORCHESTRA_ROLE=supervisor orchestra agents tool pause_agent ...`. Now that
  call still carries the worker's token and is refused, saying the session was started as
  `worker`; a missing token, one the host did not issue, and one whose session has ended (closed,
  or a new session of that id opened, or a new host started) are refused too.
- **What it may call.** `access.authorize` refuses an empty or unknown role, and any tool whose
  group the role does not hold now (`org.effective_tools`; a built-in name always means the
  built-in agent), naming the role and the group the tool needs. Some tools are then checked
  again by their handlers: the exit tools by role, the organisation's changes, pausing,
  resuming, restarting and resetting an agent (`api_org.MUTATIONS`), the `setup` group and the
  project context are the Supervisor's alone, whatever groups a custom role or team holds;
  ticket requests need `tickets`; messages follow the tree.
- **The limit.** The agents run as the same OS user as the host, with their harnesses'
  permission checks off. A process that sets out to impersonate another session can still read
  that session's token where the OS lets the same user read it (another process's environment
  or command line, the MCP configuration the Claude Code harness writes for a turn), or import
  this package and write `runtime.json` itself. The token makes the role the host's to give,
  not a variable to set; it is not a sandbox. Isolating a hostile agent needs another OS user or
  a container, which this package does not set up.

The pages and the CLI read the organisation through `api_org.read_org`, which serves only the
reads and reaches no agent's tool.

## 7. Settings and state (`config.py`, `store.py`)

`[agents]` in the project's `orchestra.toml`, optional: without it the agents run on the defaults.
`harness` (pi | claude_code | codex | dummy; unset: `claude_code` with `model` haiku when `claude`, or
`CLAUDE_BINARY`, is found, else `dummy`, and the Agents page, `orchestra agents status` and the
host's log say which was chosen and how to change it: `config.default_harness`), `enabled` (false:
no host),
`dummy_script` (the dummy harness's script, relative to the repository; section 6), `model`,
`provider`, `thinking`, `tools_via` (`mcp`, `bash` or `auto`, the default: section 6) (`model` is the default: a machine spec's own `model` wins for its agent
when the harness takes the name, section 12), `serves` (the task system's roles these agents fill; default worker and
reviewer), `spec_changes_per_day` (default 5: the Supervisor's changes to its own spec in 24
hours), `review_usd_per_day` (default 2.0) and `review_turns_per_day` (default 48: what its review
rounds and its duties' wake-ups may spend a day, section 12), `coordinator_every` (a scheduled check-in for the Coordinator, like `"10m"`; unset,
the Coordinator is woken by events only), `turn_timeout` (default none), `silence_timeout`
(default 20m), `compact_tokens` (default 100,000: the context, in tokens, past which a session is
compacted), `prompts` (per-role prompt file overrides,
relative to the repository), `control_port` (default any free port; a host that cannot bind it
exits with status 1). Every interval (`coordinator_every`, an agent's `every`, a probe's `every`;
`clock.py`) is written as Orchestra writes durations, a bare number of seconds or a number with
`ms`, `s`, `m` or `h`, plus `d` for days: `90s`, `10m`, `6h`, `1d`. The task system's `[project]`
`base_branch`, `ticket_prefix` and `work` are read for the prompts. The bus and the metrics store
are Orchestra's, found through the Orchestra project.

State is in `~/.orchestrator/<key>/agents/`: `runtime.json` (sessions and archived sessions, each
with the host's markers for its transcript; the reports an agent's tool handed to the host;
queued wake-ups; each agent's state, with the changes its tools made, `actions`; the live
sessions' token grants, `grants` (digests only); probe states; the control endpoint; the metric counters,
`metric_counts`; and `supervisor_seen_at`, when the human last read the Supervisor), behind one
file lock and written only by `store.py`; `sessions/` (the harnesses' session records and, for a
harness that keeps its transcript here, the archive; a reset keeps the old transcript where its
harness keeps it and records the path on the archived run); `logs/` (the host's log and, under `agents/`, each
session's stderr and the prompt it was started with); `PROJECT.md`, the project context the
Supervisor maintains. The organisation's custom records are in the repository under `.orchestra/`.

## 8. The web plugin (`web.py`, `org_views.py`, `static/`)

Mounted by Orchestra's web app (`orchestra/plugins.py`):

- the **Agents page** (`/agents`): upcoming scheduled wake-ups, the teams with their agents (each
  with its wake rules and what it emits; who publishes and who listens is Orchestra's Machines
  page, which observes it), agents outside a team,
  the teams' probes, the roles; a drawer for each agent, team and role (definition, tools,
  prompt, runs) and for each session (its transcript, newest first, with the host's markers for
  cut-off turns, and the Supervisor's chat);
- the **Supervisor button** in every page's navigation and a banner when something waits on the
  human: a message from the Supervisor not yet read, and the task system's blocked tickets
  (read from its plugin when installed; its own banner is asked to stand aside) (`attention.js`).
  A message is a reply in the Supervisor's transcript written for the human since the human last
  read the chat (`attention.py`): a chat reply, or a reply to a wake-up that says something (an
  empty reply or a bare acknowledgement, "Nothing to do.", calls nobody); the chat it opens marks
  that reply "for you", and shows in full what the Supervisor said that its transcript does not
  hold. A reply that arrives while the chat is open in front of the human is read there. In a
  transcript a wake-up is one line, what woke the agent, with Orchestra's prompt folded under it;
- the **agent sessions in the task system's ticket drawer** (`ticket-sessions.js`, through the
  drawer's extension point, the DOM event `tasks:ticket-drawer`): every agent turn that worked on
  the ticket (each worker turn, each review round, and the Coordinator's, the Supervisor's or
  another agent's turns about it: why it was woken, the human's words, what it wrote or a tool
  call names the ticket), with role, instance, when, outcome and last words, live while one runs
  and kept after the ticket is done; each opens inline as one turn read at a glance (the prompt
  and each tool call folded, the last words open) or as the whole session;
- the agents' **machine cards** on the Dashboard and the Machines page (`machine-cards.js`): a
  resident agent is a row with what it last said; a worker or reviewer role is the tickets its
  instances are on, with what each is saying now, and the sessions it finished, a click from their
  transcripts after the instance has moved on. The host has no card: it is no machine (whether
  it is up shows on the Agents page and on each agent's session chip).

The agents' look, `agents.css`, is a stylesheet the plugin declares, which Orchestra links in every
page's head. `agents.js` renders markdown itself (marked and DOMPurify, code coloured by
highlight.js, each loaded once from cdnjs); Orchestra renders none.

API at `/api/plugins/agents`: `GET /org`, `/roles/{name}`, `/agents/{name}`, `/teams/{name}`
(the organisation); `GET /agents` (every agent and every session as the instance it runs as,
with the ticket it is on; the cards read this); `GET /state` (sessions and probes);
`GET /sessions/{id}/transcript` (paged, with the markers; each message carries its index `i`);
`GET /sessions?ticket=<id>` (the agent turns on a ticket, `session_turns.ticket_sessions`) and
`GET /sessions/{id}/messages?start=&end=` (one turn's messages in order, with its markers);
`GET /metrics` (the agents'
measurements as Prometheus text, as the instrument reads them); `GET /attention`
(the unread message, and whether the host that answers is up: with it down the banner and the
button stop offering an answer) and `POST /attention/seen`; `POST /supervisor` (a message to the
Supervisor, proxied to the host, streamed). Every read re-reads `[agents]` from `orchestra.toml`,
as the host does each tick. The UI is read-only for humans except the Supervisor chat
(Orchestra's rule 9): an agent, a role, a team or a ticket is changed only by telling the
Supervisor. The pages, these routes and the static files are behind Orchestra's web login
(Orchestra's SPEC §10.2, PLUGINS.md §4.4.1): a browser's session, whose `POST`s must come from
Orchestra's own pages, or `Authorization: Bearer <orchestra web token>` from a program.

## 9. Cost

Every turn is a model call. The demo uses Claude Code with Haiku, the cheapest model that can
review a result. The Supervisor costs a call per chat message or escalation; the Coordinator one
per attention event and, only when `coordinator_every` is set, one per interval whether or not
anything changed. A reviewer served here costs one review turn per round of a worker that
redoes rejected work (plus a Coordinator turn every `review_rounds_before_escalation` rounds),
until the worker reports BLOCKED or a planner cancels or unblocks the ticket: a worker that
ignores `rejected_by` and redoes the same work is a loop of paid reviews, and the brake is the
worker's (the task system's shell worker reports BLOCKED on an identical rejection). Everywhere a
real model is not needed, the tests use the fakes in `tests/e2e/` (one per harness, speaking the
product's real protocol), and the dummy harness (section 6), which has no product to fake, costs
nothing at all: `demo/run.py --dummy` is the whole demo for free.

## 10. The Supervisor as the project's operator (`observe.py`, `observe_model.py`, `setup_tools.py`, `config_change.py`, `restart.py`)

The human talks only to the Supervisor (Orchestra's rule 9), so when the system cannot do what
the human asks, the Supervisor is the one who sees why and fixes the setup. Its prompt
(`prompts/operator.md`, part of both of its prompts) gives it a duty: when a ticket blocks or is
rejected twice for the same reason, or a request of the human's is not met, look first, then
check whether the system can do it at all (which machines fill which role, the kind of work, the
gate's tests, whether what is needed runs), fix the setup with its tools, and tell the human what
it changed and why; never keep filing tickets the system cannot complete. It never edits a file in
the checkout, and new code is a worker's, through a ticket, a review and the gate. Repairs are the
exception (the owner, 2026-10-28, decision D20: the Supervisor carries the self-healing duties for
now, code patches included), and only through its patch tools, under the guardrails of section 12:
a patch to the project's own code is written in a scratch worktree and lands through a ticket, a
review and the gate; a patch to an installed plugin is a project-local override pinned to the
installed version, with the upstream issue drafted in the outbox for the owner's approval.

**Guides** (tool group `guides`; the Supervisor and the Coordinator): `list_guides` (each operator's
guide Orchestra serves, `orchestra.guides.collect`: Orchestra's own and each installed plugin's
`Plugin.guide`, this package's `guide.md` among them, with what it is about, its size and its
sections) and `read_guide` (one whole, or one section). They total about 100 KB, so they are read on
demand; the Supervisor's prompt says to read the part a setting is in before changing it.

**Setting up** (`first_run.py`, `prompts/operator.md` "Setting up the project"). Orchestra wakes the
Supervisor once on a project that is not set up (section 4), and the human may ask any time ("set up
this project", "re-check the setup"). The goal, not steps: read the guides, explore the repository
read-only (its session has no editing tools; it may run the test command once to time it), set
what is plainly right with the setup tools (the task system's `[project]`, the test-report
instrument, a `[[scrape]]` of an existing `/metrics`, the gate's stages), ask the human only their
choices in one message with a recommendation for each (`work = "code"`, `push`, anything acting as
the human, the model and its cost), check what it declared works, and report what it set and why.
It creates no tickets.

**Measurements** (`instruments.py`, `source_tools.py`, `prompts/operator.md` "Measurements"). Adding a
collector is the Supervisor's, from one chat message: it reads Orchestra's guide, finds where the
number lives, chooses a `[[scrape]]` of an existing `/metrics`, a `[[sources]]` store, or a
`[[collect]]` command; tries the command once exactly as Orchestra would (`try_collector`: Orchestra's
`collect.round`, its environment and parser; what would be stored, bad lines, refused names, the
time it took); declares it (`set_collector`); and reads it back (`read_instrument`: the Instruments
page's record, its log, the points it stored, waiting up to two minutes for the next round). A
one-liner over tools already there is configuration, so the Supervisor declares it itself
(`set_collector` with `shell: true`: `["sh", "-c", …]`, at most 300 characters, one line): it is
reviewed as a commit of `orchestra.toml` with its reason, can be tried before it is declared, and
asks nothing of the human. Anything longer is a script, which is code: the Supervisor hands it to
the Coordinator, and it lands through a ticket, a review and the gate (`work = "code"`, the human's
choice). Another store is looked at before it is declared (`check_sources`, `source_schema`,
`preview_query`, `infer_source`, over `orchestra.sources`, by a declared name or by `url` and `auth`;
no credential's value is shown).

**Seeing** (tool group `observe`; any role may be granted it): `check_setup` (who fills the roles,
as `fillers.py` observes them; the work mode; the gate's tests; the capacity limit; what the
launcher runs; each collector's last round), `read_config` (orchestra.toml, whether it can be
changed now, its recent commits; whether the owner has a private overlay and each key it sets,
secret values left out), `list_processes` (Orchestra's runtime record and the declared
collectors), `read_machine_log` (a process's or collector's log, Orchestra's host's, or the log a
machine announced, read only within the repository or the state directory, as Orchestra's web
server reads one), `list_metrics` (the project's store, as the Core page's catalogue),
`get_model` (Orchestra's model view, shortened), `read_instrument`, the four sources tools,
`snapshot` (the task system's query), and `read_events`. All of it reads through Orchestra as a library; nothing is written.

**Changing the setup** (tool group `setup`; the Supervisor only, checked in the handler, and not a
group a custom role or team may be granted): `set_agent_roles` (`[agents] serves`),
`set_work_mode` (`[project] work`), `set_gate_unit` (`[[gate.unit]]`), `set_capacity_limit`
(`[tasks] workers`, `reviewers`), `set_machine` (`[[machines]]`), `set_collector` (`[[collect]]` or
`[[scrape]]`), `remove_entry` (any of those arrays), `set_setting` (any key, or removes it), and
`try_collector` (a trial round, which writes nothing but runs a command). Each
takes a `target`: `repo` (the default; `set_setting` must be told) for the project's settings in
`orchestra.toml`, or `overlay` for the owner's own or their company's settings (identities,
channels, templates) in the private overlay outside the repository (Orchestra's SPEC §8.1). The
prompt (`prompts/operator.md`) says which goes where. Each tool:

- refuses a reason shorter than a sentence, and for `repo` unless the repository root is a git
  checkout on the base branch and `orchestra.toml` has no uncommitted change;
- changes the configuration only through Orchestra (`project.orchestra.config_edit`, Orchestra's
  SPEC §8.2), which keeps the file's comments and layout and refuses a result that Orchestra's
  readers (its settings, `[[machines]]`, `[[collect]]`/`[[scrape]]`) or any installed plugin's
  check (`Plugin.check`: the agents' `[agents]` reader, `web.check`, and the task system's) accept
  less well than before; nothing here opens or edits the file;
- refuses a change whose new lines look like a secret, in either file;
- for `repo`, commits `orchestra.toml` alone, whatever else is changed in the checkout, with the
  message `Supervisor: <what changed>` and the reason as its body; a failed commit puts the file
  back. A change to the overlay is never committed. A change to `orchestra.toml` that the overlay
  overrides is answered with a `note` saying so;
- publishes `config.changed` {section, change, reason, commit, target, applies, by} from the
  Supervisor (for the overlay, `change` names the keys without their values);
- answers with what applies now and what restarts, from the readers' documented rules: the
  agents' host reads `[agents]` every second; the task system's scheduler reads `[project]`,
  `[tasks]` and `[gate]` on every tick (its own SPEC owns what they mean); Orchestra's launcher and
  collectors read the file when it changes (Orchestra's SPEC §5.1, §5.2): a machine added starts,
  one removed stops, one whose command or restart changed restarts, a collector changed starts
  over. A change of the work mode also restarts the resident sessions whose prompt depends on it,
  after their current turn.

`restart_machine` restarts a `[[machines]]` program, including one Orchestra gave up on. Orchestra's
launcher has no restart command, so the entry is taken out in the owner's private overlay
(`config_edit.remove_entry(..., target="overlay")`: `enabled = false`), the runtime record is
watched until the launcher has stopped the program (its stop grace plus 10 s at most), and the
overlay is put back byte for byte (`config_edit.restore`; an overlay the restart created is
removed), which starts it again; `orchestra.toml` is not touched and nothing is committed, and `machine.restarted`
{name, reason, pid_before, pid} is published. It refuses without a running `orchestra run`, and for
a plugin's process (it restarts with `orchestra run`) or a collector (it runs on its interval). If
someone else changes the overlay in between, the entry is not put back and the answer says so.

Limits: a commit to the base branch while the task system's gate is landing a ticket makes that
landing fail (the base moved under it), as any commit there would: the task system pauses its
pipeline, puts the ticket back to APPROVED and asks the Supervisor for attention; `resume_pipeline`
lands it on the next run. A tool's answer about what applies describes the readers' rules; it
does not wait to see them act (except `restart_machine`, which watches the launcher).


## 11. The model, experiments and predictions (`modeling.py`, `model_text.py`, `run_text.py`, `verdicts.py`, `prompts/modeling.md`)

Orchestra discovers a system dynamics model of the system from its metrics, and lets any machine
experiment on a copy of it and commit an experiment as the prediction of a change (Orchestra's
USING_ORCHESTRA §5.8–5.9, SPEC §4.5). The agents get that as tools, so an agent can say what a
change will do before it makes it, and is told afterwards whether it did. The tools hold
Orchestra's Python API (`orchestra.sd.experiments.api.Experiments`, as the agent's instance, its
`source`) in the tool server's process: no HTTP, no token of Orchestra's.

**The tools, in four groups.** `model` reads: the model (`read_model`), series (`read_series`),
the eras and what changed at the last boundary (`read_eras`), observability, an equation checked
(`check_equation`), predictions and their verdicts, experiments, their diffs and runs, and the
modeling guide. `experiments` makes, edits (one edit, a batch, or the whole model with
`load_model`), runs forward and inverse, and discards the caller's own experiments. `replay` runs
any experiment over a past window. `predict` commits the caller's own experiment as the prediction
of its own change (`commit_prediction`) and announces the change (`announce_change`).

**Answers an LLM can use.** Each answer leads with `summary`: lines with the key numbers first
(`stock queue: base 12 -> 15; experiment 12 -> 22 by +2h (band 19–25); not trusted: flow
done: declared`), written by `model_text` and `run_text` from Orchestra's JSON. Trajectories and
every number come only with `raw: true`; series points only with `points: true`, paged.

**Grants** (`builtins.py`): the Supervisor has all four; the Coordinator `model`, `experiments`
and `replay` (it compares plans, and commits nothing); a worker all four; a reviewer `model` and
`replay` (it can replay a worker's experiment). A custom role or team may be granted any of them.
Beyond the groups: an experiment is its owner's to change (Orchestra refuses another instance);
a session on a ticket commits only an experiment it made on that ticket (`AgentStore.add_experiment`);
only the agent that committed a prediction announces its change, once (`AgentStore.add_prediction`).

**Announcing the change.** Nothing is judged until the change is announced with the prediction's
change id. A worker calls `announce_change`: `when: now` publishes `system.changed` (id: the change,
subject: the ticket) as the worker's instance; `when: landed` waits for the ticket's end
(`protocol.ended`: the gate landed it, or in tasks mode its review approved it) and the host then
publishes it as that instance (`modeling.announce_landed`, from the runner's tick; a cancelled
ticket's change is never announced). The Supervisor passes `change` to the setup tool that makes
a configuration change, whose `config.changed` then carries it as its id.

**The verdict wakes the predictor** (`verdicts.py`). The host matches `orchestra.prediction.judged`
by its `correlationid` (the change) to the agent that committed it: a resident or custom agent gets a
wake-up saying the verdict; a worker, whose ticket session is over by then, gets one short turn of
its own (`<role>-<ticket>-verdict-<time>`, the `model` tools only, a scratch directory, not the
workspace) to read the verdict and say what it means. Every agent with `predict` lists the
verdict in what it listens for, and a per-ticket one its ticket's end too (`listening.py`). Any
agent may also wake on `orchestra.prediction.judged`, filtered by verdict
(`orchestra.prediction.judged:side effect`).

**The skill.** `prompts/modeling.md`, read with the `modeling_guide` tool, says what stocks, flows,
equations, origins, trust, eras, observability and verdicts mean, how to test a change (read, draft,
replay to check the equations explain what happened, run forward, read what is not trusted), how to
ask an inverse question (hold what must not move; an ambiguous answer is a warning), and when to
commit and how to read a verdict. The equation language in it is Orchestra's own reference
(`GET /language`), filled in when it is read. The role prompts only say when to reach for it.

Limits: a verdict published while the host is down wakes nobody (it is still in
`read_predictions`); running the model needs `orchestra[sd]` beside the agents (reading it does
not), and a tool says so when it is missing.


## 12. The Supervisor grows (`machine_spec.py`, `spec_git.py`, `supervisor_spec.py`, `duties.py`, `review.py`, `flow_signals.py`, `load.py`, `split_proposal.py`, `budget.py`, `patching.py`, `upstream.py`, `transcripts.py`)

The first phase of Orchestra's `docs/proposals/agent-first-machines.md`, as the owner decided it
on 2026-10-28 (Orchestra's `docs/roadmap/decisions.md` D20): the Supervisor takes on duties, keeps
the flow moving, repairs what blocks it, and asks of itself at every review whether it should be
two machines. The split itself is a later phase.

**The machine spec.** `.orchestra/machines/<name>/` in the project ("just machines"):
`machine.toml` (description, engagement, `model`, `[review] every`, and `[[duties]]`, each with
`listens`, the wake-up entries it adds to the agent's `wake_on`, and `tools`, the groups it needs),
`role.md` (how it works a wake-up), `duties/<duty>.md` (one standing duty each), `ledger.md` (why
each duty exists) and `proposals/` (what its review rounds proposed about itself). A spec is a role
plus an agent record given a contract: it belongs to the agent of its name, built in or custom,
and `org_store.all_agents` applies it (`machine_spec.apply`); a spec naming no agent is ignored. It
never grants a tool. A **built-in duty** (`builtin = true`: `talk_to_owner`, `setup`,
`hand_to_planner`) names part of the plugin's own prompt, which keeps shipping with the plugin;
it is listed, never copied, edited or removed. Orchestra reads none of it (its rule 2).

**The Supervisor's spec.** Written the first time the host runs in a project
(`supervisor_spec.ensure`, committed as `orchestra-agents: ...`): `model = "sonnet"` (the owner's
default for the Supervisor; Haiku for the narrow machines it will create), `[review] every =
"30m"`, the built-in duties, and two of its own: `keep_flow` and `review_round`. From then on the
files are the project's. Its prompt is, in order: the **fixed core** (`prompts/supervisor_core.md`,
shipped with the plugin and always first, so nothing in the spec can remove it: the owner's
channel, Orchestra's invariants, the outbox for acting as the owner, the guardrails below, a
prediction for every fix, the rules of self-healing), its built-in prompt
(`supervisor.md`/`supervisor.tasks.md` with `operator.md`), then the spec's loop and duties.

**Per-machine model.** The host opens an agent's session on its spec's `model` when the harness
takes that name (`Harness.model_for`: Claude Code takes `haiku`, `sonnet`, `opus` and full ids;
the dummy harness records it; Pi and Codex take none, and `[agents] model` stands). A changed
model reopens the resident session after its turn. The announcement names the model it runs.

**Changing the spec** (group `roles`, the Supervisor's alone): `list_duties`, `add_duty`,
`edit_duty`, `remove_duty`, `revert_change`. Duties are added by chat: the fixed core tells it that
a standing instruction ("from now on, ...", "whenever ...", "every morning ...") is a duty, written
in the same turn with the owner's words as the reason. Every change:

- needs a reason in a sentence; is one commit of `.orchestra/machines/` alone (`spec_git.commit`):
  on the base branch when the checkout is on it with no other uncommitted change and the merge gate
  is not landing; else on the side branch `orchestra/supervisor`, made with git's plumbing on a
  private index so the working tree and the owner's index are untouched. While the side branch has
  commits the checkout lacks, the specs are read from it; the next change on a clean base checkout
  merges it;
- is refused past `[agents] spec_changes_per_day` in 24 hours (a revert never is); refused for a
  built-in duty, for a tool group the Supervisor does not hold, for text that looks like a secret;
- is announced as `role.changed` (subject `machine:supervisor`, the commit, the reason), a line in
  `ledger.md`, and a restart of the resident session after the turn, so the next turn runs it (the
  conversation is kept);
- can be undone: `revert_change` undoes one spec commit by what it did to each duty, applied to the
  spec as it is now, so later changes to other duties stay; the owner may also `git revert`.

**Flow-keeping** (`keep_flow`). It wakes on Orchestra's domain-free statements
`orchestra.model.stalled` (subject `stock:<id>` or `flow:<id>`), `orchestra.machine.quiet`,
`orchestra.model.diverged`, and on `gate.base_broken`, `pipeline.paused` and
`outbox.rate_limit.unresolvable` (besides what the core already wakes it for). It reads the model,
the series, the events, machine logs and, new, agent transcripts (`read_transcript`, group
`observe`: an agent's latest session or one by id, the turns around a time, trimmed). Every fix
carries a prediction: `predict_fix` (group `predict`) commits `orchestra.prediction.made` with the
expected effect (`{flow, factor}`, `{stock, below|above}`, ...) without an experiment, and its
change id goes with the fix (a setup tool's `change`) or is announced with `announce_change`.
Orchestra judges it, and a verdict that did not pass is a review round's finding. The events wake
it through `machine_spec.apply`; their filters match the envelope's `subject`, which `events.flat`
now carries when the data has none.

**The review round** (`review.py`, in the host). Every `[review] every` (default 30m, never under
5m): a quick check with no model call (`flow_signals.check`: stalls, quiet machines and divergences
still open, a subject bounced three times in a day, the gate's broken base or a paused pipeline, a
failing instrument in Orchestra's runtime record, a verdict on its own prediction that did not
pass, fixes without a prediction), and its own overload signals (`load.measure`). Only a finding
whose key no earlier round woke it for, or a split proposal, queues a wake-up (`[Review round]
...`): a full turn. Otherwise the round costs nothing. The record is its agent state (`review`,
`next_review_at`, `load`).

**The day's budget** (`budget.py`). The Supervisor's own initiative (review rounds, and wake-ups
on events only its spec's duties listen for) stops at `[agents] review_usd_per_day` dollars
(Claude Code reports each turn's cost: `TurnResult.cost_usd`) or `review_turns_per_day` turns.
The owner's chat and what the fixed core wakes it for never wait on it. Spending is kept per day in
its agent state (`spend`, `spend_total`).

**Self-healing** (group `patch`, the Supervisor's alone, decided by the owner for now; a separate
Steward may split off later). `propose_patch`: a bug in the project's own code, when `work =
"code"`: the diff is applied and committed in a scratch worktree
(`~/.orchestrator/<key>/agents/scratch/patch-<name>`) on a branch `supervisor/patch-<name>`, the
worktree removed, and the Coordinator handed a request to land that commit through a ticket, a
review and the gate. `write_override`: a bug in an installed plugin: `.orchestra/overrides/<name>.py`
in Orchestra's override format (`TARGET`, `PACKAGE` pinned to the installed version, `KIND`,
`ISSUE`, `def override`), committed alone to the base branch when it can be, announced as
`system.changed` (subject `override:<name>`). Orchestra applies it by file in every process that
loads plugins, on that exact version only. Never Orchestra's core or this package.
`draft_upstream_issue`: the issue, with the override's code, as an outbox draft of kind
`upstream.issue` (orchestra-outbox's `github.issue_create`, always the owner's to approve); without
the outbox, or without that kind, nothing is sent and the issue is kept in
`~/.orchestrator/<key>/agents/upstream/`.

**The self-decomposition hook.** Each round measures the overload signals the owner named
(`load.py`: prompt over 45k tokens, over 30 tools, compactions, busy over 0.7 of the day with
wake-ups waiting, more than half the machines sending to it or over 20 types listened for,
wake-ups waiting over 10 minutes behind its turns, cost per turn rising 1.5 times, its predictions
failing more as duties grow), records them for the instrument (`agent_load`), and when any cross,
writes a split proposal into its spec (`proposals/split-<date>.md`, one commit, at most weekly for
the same signals): its duties clustered by cohesion (shared wake-ups and non-privileged tools),
which cluster could be a machine, whether the split is for load, privilege or engagement, and when
only the busy signal crossed, that scaling out may fit better. The round then wakes it to tell the
owner in two lines. Nothing is split: `split_machine` and `merge_machine` are the next phase.

Limits: tool count crosses at once (the Supervisor holds about a hundred tools), so the first round
writes a proposal; utilisation is measured from its own turns' events and counts a turn still
running; cost is known only on Claude Code; the quick check reads the last 20,000 events.

