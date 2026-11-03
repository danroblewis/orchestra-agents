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

The owner's words on what the plugin is (2026-10-27), which the summary above follows:

> The Agents plugin extends the Machine, it gives us an implementation of a Machine. Normally,
> the target repo would be responsible for defining its own Machines, and the author of the
> target repo does this by writing code and programs that publish messages and subscribe to
> messages on the event bus. That can happen either by writing to our event bus with the
> appropriate `source` name for the machine, or it can come implicitly by setting up an
> orchestra-bridges thing for it. The orchestra-agents plugin provides a common resource for
> doing this. It also provides the UI modifications.

Asking for an agent on the bus is the Workflows floor's: an `offer.made` to a role a confirmed
Universal Machine serves (section 3.1).

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
hand-offs are kept in the Supervisor's agent state (`handoffs`, the last 30). One the Coordinator
has not planned 30 minutes on (or `[agents] coordinator_every`, when longer) wakes the Supervisor
once, and its wake-ups' facts list such hand-offs by request id (`handoff_overdue.py`).

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
| the Workflows floor asks a planner (a guard passed) | `attention.needed:coordinator`, `attention.needed:supervisor` |
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

### 3.1 Meeting the Workflows floor (`floor.py`, `floor_roles.py`, `floor_runner.py`, `offer_turn.py`)

A project may have the task system, the Workflows plugin, or both; one host answers both. The
contract is orchestra-workflows' `docs/machine-contract.md`, described there once; this section
says only how the agents meet it. Its names, its claim rule and its withdrawal rule are copied by
hand into `floor.py` as `protocol.py` copies the task system's (nothing is imported from it;
`tests/unit/test_floor.py` checks the copy, name by name and on a shared table of event sequences,
against the plugin the `dev` extra installs).

**Who serves which role** (`floor_roles.py`). A confirmed Universal Machine (machine-design.md
§2.4) is a machine spec with `role = "<floor role>"` in its `machine.toml`, in
`.orchestra/machines/<name>/` or in the state directory's `machines/<name>/` (the repository's
wins when both hold the name), whose name is a custom agent (confirm creates its role and agent;
a spec whose name is no agent is ignored, as every spec is). That agent answers the role's offers;
when two specs name one role, the first by name does. The built-in worker and reviewer answer the
floor roles of their names when `[agents] serves` lists them and no spec takes the role.

**A turn** (`floor_runner.py`). For each `offer.made` with a role served here, the host takes a
slot of the agent (`triage/1`, `worker/1`) for the session `<agent>-<item>` (one turn per item's
session at a time; an offer addressed `to` an instance is taken only as that instance, when it is
the agent's and free), announces the instance (`orchestra.machine.started` with `roles`: the
floor accepts a claim for a role not named after the machine by it) and claims
(`offer.claimed`, the offer as causationid, the item as subject). It reads the bus back from where
it read the offer to its claim: the first claim after the offer and before any withdrawal holds it
(`floor.winner`); a loser starts no session and says nothing more. The holder runs the agent's
turn (`offer_turn.run_turn`, shared with the task system's runner) with the brief, in the handed
`worktree` resource's directory, or, without one, in the repository read only; it says
`offer.alive` with what the agent is saying now at least every `heartbeat_s`
(`offer_turn.heartbeat`); and it finishes (`offer.finished`) with the outcome the agent's exit tool
reported (`floor.outcome_of`): `report_outcome` (granted to a confirmed machine's agent, checked
against the offer's `outcomes`, which the host keeps on the session's record) names it; the
worker's `report_ticket` is `ready` or `blocked`, the reviewer's `report_review` `approved` or
`rejected`. **The BLOCKED rule**: a turn that ends without an outcome after its one nudge, or with
one the offer does not list, finishes `blocked` with why. An `offer.withdrawn` naming the offer kills the turn (the transcript shows "released by the
Workflows floor") and nothing more is said for it. When the host stops mid-turn nothing is
finished, and a claim it reads back while stopping opens no session: the item waits out the lease,
the floor takes the offer back and offers it again, and the stopped instance counts as a silent
holder toward the step's rounds.

**Attention.** The floor's `attention.needed` (`floor.ATTENTION_NEEDED`, data `to`: the planner
role) wakes the Coordinator (`attention.needed:coordinator` in its `wake_on`) or the Supervisor
(`attention.needed:supervisor`), matched on `to` as the task system's `task.attention_needed` is;
the wake-up (`floor.wake_reason`) names the item, its workflow and step, the guard and the reason.

Not yet: passing an item on directly (the contract's pass-on, `offer.made` with `ends`), and
`facts` on a finish other than a file-mode session's `fields` (section 6, `tools_via = "file"`: the
finish carries them as `facts: {set: ...}`); a worker on the floor finishes `ready` and the workflow's next step is the
floor's to offer.

## 4. The host (`host.py`, `residents.py`, `turns.py`, `control.py`, `cli.py`)

`orchestra agents run` is one process per project, and no machine: the runtime the configured
agents run in (section 1). Orchestra's launcher
starts it: the plugin declares it in `Plugin.processes` (`web.processes`) whether or not
`orchestra.toml` has an `[agents]` section (the owner, 2026-10-28: a new user configures nothing;
their Supervisor sets the project up), so `orchestra run` keeps it running in the interpreter
Orchestra runs in, as the process `agents.host`; `[agents] enabled = false` declares none. It is started with `--wait`: while another host holds the project, or the control port is
taken, it waits for that to clear instead of exiting, so a host restarted while the previous one
still shuts down takes over afterwards; and it watches the `orchestra run` that started it (every
2 s, `parent.py`), stopping as on SIGTERM when that process is gone (killed before it could stop
its processes), so no host outlives its launcher. Its shutdown closes the running turns together, waits up
to `SHUTDOWN_REPORTS_S` (10 s) for their reports, then closes the resident sessions together, each
close at most `closing.CLOSE_S` (5 s): 20 s at worst, under the 30 s stop grace it declares
(`web.STOP_GRACE_S`), so Orchestra's launcher never kills it mid-way. It:

- never announces itself and never publishes as itself (`events.py` refuses a publish as
  `agents`, or with no source). It announces every agent instance it starts, as that instance
  (an AI agent, on which harness and model; a resident agent is continuous, its session always
  up, one woken at a time of day or every interval scheduled, a per-ticket or other ephemeral one
  ad hoc; `web.declared` says the same before it runs), so Orchestra's Machines page shows each agent as a
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
  | an agent that serves a Workflows floor role (section 3.1) | `offer.made`, `offer.withdrawn` | the floor's offers it answers and their withdrawals (floor_runner.py) |

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
  anything that will happen, or a claim of a change with no tool call; a promise to tell the
  human later is kept in a turn that called `hand_to_coordinator`, whose plan wakes it;
  `promises.py`, a heuristic) gets one more turn on the session to take it or say it is not done; the human gets
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
every point `instrument=agents.turns` (and `project`). The series: `agents_working{role, machine}` (every measured
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
  compacts its own context (`--autocompact`), at a window set from the model's context in one place
  (`claude_code.compact_window`, since 2026-11-02, T26): 40% of it, never within 100,000 tokens of the
  session's starting context (its first request: the prompt, the project's CLAUDE.md and rules, the
  tools), never past the context; the context is the model's as a turn's result says it
  (`modelUsage.contextWindow`), 1,000,000 before the first (200,000 for Haiku), both kept in the
  session's metadata. (Run 1 passed 100,000 on a 1M model whose Supervisor started at 81,000 tokens:
  every tool answer crossed the trigger and no compaction got back under it.) A turn's cost is the
  change in Claude Code's `total_cost_usd`, which is a running total over a resumed session (the last
  total kept in the session's metadata; a lower one is a new session's). `CLAUDE_BINARY` names the binary to run.
- **Codex** (`codex.py`): `codex exec --json` per turn, resumed by thread id, the rollout under
  `$CODEX_HOME`; tools through the same MCP server, or from the shell with `tools_via = "bash"`
  (none with `file`, below).

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
command, and the dummy harness in-process; `mcp`, `bash` and `auto` change neither.

**`file`: no tools at all** (`harness/file_output.py`, since 2026-11-03, T34; the owner: "having AI
agents produce a JSON file at the end of their runs instead of Orchestra specific tool calls ...
whichever would satisfy my company's policies"; a reviewer's page: `docs/policy-modes.md`). The
session is started with no tool groups, no MCP server (Claude Code's `--strict-mcp-config` with an
empty config; Codex without the server's override; Pi without its extension) and without its token,
so nothing it runs can reach Orchestra while it runs (a dummy script's `tool` step is refused). The
harness names one file, `<sessions>/outputs/<session>.out`, in `ORCHESTRA_OUTPUT` and in the system
prompt, whose section (`file_output.prompt`) replaces the tools' and `NO_SUBSTITUTE` and names only
what the session's groups would have let it call; a session resumed from a transcript begun in another
mode is told in its first message, once. The file is GitHub Actions' `$GITHUB_OUTPUT` format, the
Workflows program host's (its SPEC §5.3; `file_output.read` is a copy of `programs/step.outputs`,
checked against it when the installed plugin has it): `name=value` lines and `name<<DELIM` blocks, a
value that parses as JSON is that value, a later line of a name wins. After the turn has ended (a turn
that timed out or failed is not acted on, and the record says so) the wrapper (`FileSession`, which
holds the token) acts on the reserved names in the order of their lines, each through
`caller.Caller` as the session, so with exactly the tools' checks: `events`, a JSON list of
`{type, subject?, data?}` (data a sentence, or `{note}`), each published by `emit_event` (its
envelope: as the machine's instance, subject and note in the data); `prediction`, one `predict_fix`
(`orchestra.prediction.made`); `outcome` with `summary`, the session's exit tool (`report_outcome`,
or `report_ticket`'s state and `report_review`'s verdict, `ready`/`blocked`/`approved`/`rejected`
taken for theirs); `fields`, a JSON object set with a floor offer's finish (`store.amend_report`;
`floor_runner` finishes with `facts: {set: fields}`). Any other name is an output. A line of neither
shape, an invalid event, a refused call: each is refused with its reason and the rest still go. What
was done, what was refused and the outputs are the session record's `output`. The file is removed
before each turn and once acted on. A turn that writes no file ends as one that ends without its exit
tool does (one nudge, then the BLOCKED rule).
**Every answer is bounded** (`answers.py`, since 2026-11-02, T26): `Caller.answer` gives no harness an
answer, or a refusal, over `answers.BOUND` (24,000 characters, set there alone), under the size at
which Claude Code spools an output to a file. Tools that list give a count and their first
`answers.LISTED` with "… and N more" (`answers.first`); what is still over the bound is cut with a
last line saying how much was left out and how to read the rest: a tool that only reads
(`harness/tools.PAGED`, each with a `page` argument the caller takes off before the call) is called
again with the same arguments and `page`; one that changes something is never called again for its
answer, so its line says the call is done and its answer is not paged. **The tool catalogue is
never cut** (since 2026-11-03, S3: the Supervisor's `--list` of whole descriptions was 34,000
characters, cut by the product at 80 lines, and it missed `set_setting`): `orchestra agents tool
--list` (`bash_tools.catalogue`) gives every tool's name first, all in one block of a few lines, then
one line each (its first sentence), the lines shortened until the whole is under the bound, never
`first`'s cap; `get_role` and `get_agent` put `tool_names` before the prompt, so a long prompt never
pushes them onto a later page.
**No host credentials in a session** (`harness/base.session_env`): every harness starts its process
with the host's environment minus each name that may hold a credential (`integrate_kits.CREDENTIAL_VARS`,
or ending in `_TOKEN`/`_SECRET`/`_PASSWORD`/`_KEY`; the host's `ORCHESTRA_*` tokens among them), keeping
only that harness's own sign-in (Claude Code's `CLAUDE_AUTH`, Pi's provider keys `PI_AUTH`, Codex's
`CODEX_AUTH`) and the session's own `SessionSpec.env`.
**No push, no pull-request writes** (`push_guard.py`; the owner, 2026-10-31: using Orchestra at work
"won't try to push things to master"). Every session runs with the owner's git and gh
credentials and its harness's permission checks off, so the host gives each session whose role is
not in `[agents] allow_push` (default none) a block in its environment, which every harness passes
on to the commands the agent runs (`SessionSpec.env`): a git configuration
(`GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n`, appended after the host's own) with
`url."orchestra-no-push::".pushInsteadOf` set to the empty prefix, which turns every push URL of
every remote, and one given on the command line, into a transport whose helper
(`git-remote-orchestra-no-push`, first on the session's PATH) only refuses; and `git` and `gh` first
on that PATH, written into `<state>/no-push/`, naming the real ones: `git` refuses `push` and
`send-pack` (a remote with an explicit `pushurl` is not rewritten, git's rule) and passes the rest;
`gh` passes the reads (`pr view`/`list`/`diff`/`checks`/`status`, `issue view`/`list`, `repo view`,
`run`, `search`, `api` with no method and no fields) and refuses every other command. Each refusal
says what to do instead: commit on the branch and report; the merge gate or the owner pushes, opens
and merges. Fetching, committing and merging locally are untouched. The Supervisor's session is
exempt (`push_guard.EXEMPT`), as it was before the block, until the owner decides otherwise: its git
writes are its tools' local commits (AGENTS.md rule 2), and what it sends as the owner, a push
among them, goes through the outbox, in the outbox's process. It stops a mistake, not an agent set
on pushing (the limit below). The roles are matched by the session's `ORCHESTRA_ROLE` (a built-in
role, or a custom agent's name); a change applies to sessions opened after it.

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
(`report_plan`), titled from the request's first words, and the Supervisor tells the human the plan,
and which ticket landed when one does (without the tasking plugin, which keeps the tickets, it hands
nothing on and says it changed nothing and why); a request for another harness or model is answered
that a scripted Supervisor cannot change it (nothing handed on); Orchestra's follow-up to a promise
is answered "Done above."; on the first run the Supervisor says it cannot read the repository and how
to get one that can; any other turn replies "Nothing to do." or that it is scripted and has no
answer for it. Without `[agents] dummy_script`, the repository's `.orchestra/dummy.toml` is the
script when it exists. A rule's conditions:
`role` (required; a role's or custom agent's name, or `"*"`), `ticket` (a glob on the ticket id),
`prompt` (a regular expression searched in the turn's prompt; its named groups are
`{match.<group>}` in the rule's steps and reply), `turn` (the session's nth turn,
counted from its transcript, or a list of them), `checkout` (whether the working directory is a
git checkout) and `tasking` (whether the tasking plugin is installed, `installed.tasking`). Its `steps`, in order: `say` (a message), `write` (a file, with `text` and
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
`provider`, `thinking`, `tools_via` (`mcp`, `bash`, `file` or `auto`, the default: section 6) (`model` is the default: a machine spec's own `model` wins for its agent
when the harness takes the name, section 12), `serves` (the task system's roles these agents fill; default worker and
reviewer), `allow_push` (the roles whose sessions may push and write pull requests; default none,
section 6), `spec_changes_per_day` (default 5: the Supervisor's changes to its own spec in 24
hours), `review_usd_per_day` (default 2.0) and `review_turns_per_day` (default 48: what its review
rounds and its duties' wake-ups may spend a day, section 12), `coordinator_every` (a scheduled check-in for the Coordinator, like `"10m"`; unset,
the Coordinator is woken by events only), `turn_timeout` (default none), `silence_timeout`
(default 20m), `compact_tokens` (default 100,000: the context, in tokens, past which a session is
compacted; Claude Code compacts at its own window, from the model's context, section 6; set to 0 it
is not asked to), `prompts` (per-role prompt file overrides,
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
Supervisor maintains; `machines/<name>/`, a confirmed Universal Machine's spec when it is not in the
repository (machine-design.md §2.1): its `machine.toml` (with `emits`, and `role` when it answers
the Workflows floor's offers), `role.md` (which names each stock or effect it reads as the series
it reads: its link, else its instrument's series, rewritten on a link or a keep), `role.json` and
`agent.json`, written by the Supervisor's confirm (`state_machines.py`), never committed, and read
by `machine_spec.load_all` (the one spec loader), `roles.all_roles`, `org_store.all_agents` and the
role's prompt beside `.orchestra/`, the repository's winning on a name; and, from the design tools
(section 10, "Designing the system"): `instruments/<name>.sh` (the measurement scripts the
Supervisor writes), `drafts/<name>/` (a drafted machine's implementation, not official) and
`trials/` (a script's trial round); and `control.token` (the Supervisor chat's control endpoint's
token, owner-only, `control.py`), `no-push/` (the push guard's `git` and `gh` shims for the
sessions it blocks, `push_guard.py`), `pending-restart.json` (a restart asked of the launcher and
not yet seen done, `restart.py`), `verdicts/` (the working directory of the turn a prediction's
verdict wakes, `verdicts.py`), `scratch/` (the worktree of a patch the Supervisor prepares,
`patching.py`; `scratch/branch-config/`, the branch's `orchestra.toml` as Orchestra's editor works a
change out on it, `config_change.py`), `upstream/` (the issues it drafts for other repositories,
`upstream.py`), `live_overlay.json` (each overlay change that keeps a change to `orchestra.toml` on
the Supervisor's branch live until merged, and a graduation's overlay command waiting for its move,
`live_layers.py`) and `machines/<name>/` with `.pending.json` (a spec change's live copy until
merged, `spec_git.py`).
Beside it, in Orchestra's state directory and not the agents' own, `integrate_system` writes a
scaffolded poller's files under `<state>/machines/<name>/` (the bridges' scaffold: its
`machine.toml`, with a top-level `kit`, and its program and test; `integrate_kits.py` edits the
`machine.toml`'s clearance and settings, and reads each change back with the bridges' own reader,
`orchestra_bridges.kits.machinefile.read`); those files are the bridges', read by them. The declarations are Orchestra's declared layer
(`orchestra.sd.intended`), reached only through `intended_api.py` and written only through its
host's endpoint (`POST /intended`, with the project's token; Orchestra's rule 5 as amended), so with
the host down a design call is refused. The Supervisor's agent state also keeps the turn its tools
record in their provenance (`turn`), its draft instruments (`drafts`), the changes its confirmed
machines' first turns announce and the predictions published about them (`machine_starts`) and the
refinement pass's proposal (`refinement`); the records two processes write are updated under the
runtime lock. `[agents]` adds `design_passes` (default 3) and `draft_expiry` (default 24h). The
organisation's custom records are in the repository under `.orchestra/`.

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
`GET /attention`
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

**The Supervisor's bounds** (2026-11-02, bootstrap run 2: a Supervisor on Opus, set up in a
clone of the owner's repository, ran the project's Python suite and made it an hourly instrument,
which wrote a `.venv` and `__pycache__` into the tree; probed another service's port it found in the
code; read the owner's home directory, listed their real token files and offered to use them; and
scaffolded a GitHub poller before the owner had named GitHub). What the Supervisor never does, in the
fixed core of its prompt (`prompts/supervisor_core.md`, which no spec removes):

- it never runs the project's own code, build or tests: it reads them, and runs Orchestra's own
  commands and its tools (the collectors it scaffolds run under the host);
- it never reads outside the project's tree and Orchestra's state directory for it: no `~`, no
  `find /`, no other repository, no credential store, no environment listing; a token file the
  owner names is read by the kit, never by the Supervisor;
- it never probes, connects to or scans a port or a host it finds in the code: it asks the owner
  which address to use;
- it never scaffolds, declares or configures a system the owner has not named in words: it may
  propose one in one line;
- it never lists or offers credential files it finds.

Where they are held besides the prompt. **Claude Code's settings** (`harness/bounds.py`): the host
gives the Supervisor's session alone its bounds (`SessionSpec.bounds`: the project's tree, the state
directory, its working directory; a worker runs tests in its workspace and is not bounded), and the
Claude Code harness writes a settings file passed with `--settings`, whose `permissions.deny`
Claude Code enforces in every mode, `--dangerously-skip-permissions` included: Read rules for the
credential stores (`~/.config`, `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.docker`, `~/.kube`, `~/.netrc`,
`~/.git-credentials`, keychains; Claude Code applies them to `cat` and the like in Bash too); Bash
rules for the home directory and the disk (`cd ~`, `ls ~/.*`, `find /`, `find ~`, `env`,
`printenv`), for probes (`curl`, `wget`, `nc`, `nmap`, `telnet`, `lsof -i`, `netstat`, `ss`, and
WebFetch to localhost) and for the usual build and test tools (`pytest`, `uv run`, `uv sync`,
`cargo`, `just`, `make`, `npm`, `go test` …); and a PreToolUse hook (`harness/bounds_hook.py`,
standard library only, failing closed) that denies Read, Grep, Glob and NotebookRead outside the
session's roots (its bounds, the installed packages, Claude Code's own directory for its saved tool
answers, the temporary directory). Claude Code says its Bash rules are no security boundary (`sh -c
'…'`, a full path to the binary, a script that opens files itself pass), so they are a second line,
not the only one. **The tools**: `integrate_system` refuses a system the owner has not named in their
words this session (`_named_by_owner`: its name, title or a usual alias in one of the session's
messages), scaffolding nothing; `add_instrument`, `set_collector` and `try_collector` refuse a
script or command that runs the project's build or tests (`runs_project.py`: a build or test tool at
a command's start, directly, through a runner or in `sh -c`, the text of a repository script the
command starts, or `orchestra.instruments.test_reports --run`), with the reason, nothing run.
**Held by the prompt alone**: another repository reached by a relative path from the shell, a port
read by a program the Supervisor writes, and not listing or offering credential files (the Read
rules cover the usual stores; a file elsewhere is the prompt's). Pi, Codex and the dummy harness
carry the prompt only.

**The Supervisor's own branch and pull request** (`side_branch.py`, `supervisor_pr.py`,
`live_layers.py`; the owner, 2026-11-02, self-improvement 5: "they probably need a pull request, but
we can use it locally while working"; scm 6–8: "plain branch plu PR", "the provider can push to the
remote, but not to the main or master branch"; the coordinator's rule, the same day: "used live" is
the private layer, "shared" is the pull request). Every change the Supervisor makes to the
repository is one commit on the branch it keeps for its own work, `orchestra/supervisor`, never on
the base branch: `orchestra.toml` (`config_change`, a setup tool with `target = "repo"`), its machine
spec and duties (`spec_git`), an override (`write_override`) and a graduation (`graduate_machine`).
The commit is made with git's plumbing on a private index, from the branch's tip while it holds
commits the base lacks, else from the base's head (`origin/<base>` when it is ahead). **The owner's
checkout is never touched**: not switched, written or staged, so `git status` is what the owner
left, the merge gate lands tickets as before, and pulling the merged pull request goes through.
**What is live until the merge**:

- `orchestra.toml`: the same change is made in the owner's overlay, which Orchestra reads at once;
  the branch's file is worked out by Orchestra's own editor over the branch's text
  (`config_change.branch_editor`, in a scratch file of the state directory). The pairing is kept in
  `live_overlay.json`, and the overlay's copy is retired, newest first, once the checkout's
  `orchestra.toml` gives the same configuration without it (merged and pulled) and while the
  overlay still holds what the change wrote. When the overlay sets the key itself (the owner's value
  wins) or cannot say the change (removing a key), it is not touched and the answer says the change
  applies once merged.
- a spec or duty: the machine's whole spec as changed, in the state directory
  (`<state>/agents/machines/<name>/`, with `.pending.json`), which `machine_spec.load_all` reads
  before the repository's; retired once the checkout's files are the same.
- an override: nothing (Orchestra loads overrides only from the checkout); it applies once merged
  and pulled, and the answer says so.
- a graduation: nothing; the launcher keeps the old command until the merge, and the answer says
  so. An entry whose command is in the owner's overlay is changed there once the moved program is
  in the checkout.

`live_layers.settle` retires and applies these; the review round, the digest and every proposal
call it. Then `supervisor_pr.propose`, through orchestra-scm's provider (`branch_pr`, configured by
`[scm]` and read through Orchestra's configuration): with a remote (`origin`) and `[scm]`, the
branch is pushed (the provider refuses main, master, the base and the default branch) and its pull
request opened at once, the description from the provider's template (the packaged one: Summary,
Testing, Jira ID). **The title and the Summary describe the whole branch** (since 2026-11-03,
bootstrap run 4: PR #53 was titled "Supervisor: outbox.kinds set", after its last commit alone,
over four commits): the title lists what the branch changes, each commit's `Supervisor-For` label
once, oldest first, in at most 60 characters (`TITLE_MAX`; "Supervisor: draft expiry, [scm], an
Outbox kind", the rest counted as "+N more"), and the Summary is one line per commit with its
reason (`branch_story`); Testing is what the tool verified, the Jira ID the owner's words when they
name a ticket key, else "none". A pull request already open for the branch is updated by the push,
never opened twice, and when the branch gained a commit its title and description are rewritten
through the provider (`update_pr(change, title=, body=)`); orchestra-scm's `branch_pr` has no
`update_pr` yet, so until it does they stay as opened and the answer says so. With the Outbox installed (orchestra-outbox
importable; the coordinator's decision), opening it is an outbound act its configuration approves:
the branch is pushed and the pull request is an Outbox draft of the kind `supervisor.pull_request`
(sender `github.pr_open`), opened when the owner approves it. Without a remote, `[scm]` or
orchestra-scm, the branch is made, nothing is refused, and the answer says the pull request is
pending that configuration. **`[scm]` as the Supervisor sets it** (since 2026-11-03, S3, the live
check: a Supervisor with no documentation guessed `template_sections` and its commits waited
unpushed): `set_setting` on `scm` or `scm.<name>` checks the section as it would be after the change
against orchestra-scm's own schema (`scm_settings.py`: the library's `settings.KEYS` and `problems`;
the keys `provider`, `repo`, `base`, `api_url`, `token_file`, `template`, `protect`), so an unknown
key is refused with the known ones named and nothing is written; a value shaped like a secret, a key
that names a token, and a `token_file` T27's rule refuses (`credential_paths`) are refused, the rule's
caution said. The guide's section on the Supervisor's own changes documents the section key by key,
with an example. Once `[scm]` is valid, the branch's commits that waited for it go in one push and
one pull request: carried by the change that set it (`target = "repo"`), else by
`supervisor_pr.catch_up` (a setup change that made no commit, as `[scm]` set in the overlay; a change
with nothing new; the review round, before its quick check; the digest, before it names the branch),
and the answer's `said` reads "the branch's N pending commits are now pushed as PR #…". A refused
push or pull request (no token, auth, a protected branch, the remote's hook) is said in the
provider's and git's own words (`refused`), the pull request already open kept, and tried again at
once when the tip or `[scm]` changes, else after `RETRY_S` (25 minutes: the next review round).
Merging is the merge gate's or the owner's. Every answer names the
branch and the pull request (`said`), the ledger's line repeats it, and "what changed"
(`since_digest`; its Items line counts `item.requested` and `item.opened` in the window and the
Workflows plugin's open items now, `workflow_tools.open_count`, and never says "none started" while
any are open: a start event opens an item with no item event, bootstrap run 3) has a line for the Supervisor's open pull request (merged, by the provider's
`learn`, until the checkout has it). A pull request that is an Outbox draft is said by the draft's
state now (`supervisor_pr.draft_state`: the Outbox's state API, else its `outbox.*` events on the
bus): sent, it is the pull request it opened (`#N`, kept in the record); rejected or failed, with the
Outbox's reason; waiting for the owner's approval only while it is `drafted`; never "waiting" once
sent (since 2026-11-03, T2j s16: draft d938f2ac sent as #53 at 10:30Z was said "waiting on the owner"). orchestra-scm is a dev dependency, imported only in
`supervisor_pr` and `scm_settings`, like the Outbox; the provider's own state is Orchestra's `<state>/scm/`. Limits:
one branch, so one pull request accumulates every change until it is merged; the organisation's records (`roles.commit_orchestra_files`) still commit to the
base branch when the checkout is clean there; a split proposal or the starting spec rides on the
branch with the next change's pull request; and a repair by override waits for the merge.

**Guides** (tool group `guides`; the Supervisor and the Coordinator): `list_guides` (each operator's
guide Orchestra serves, `orchestra.guides.collect`: Orchestra's own and each installed plugin's
`Plugin.guide`, this package's `guide.md` among them, with what it is about, its size and how many
sections) and `read_guide` (without `section`: the guide's contents, each `##` and `###` heading with
its section's size, and its opening, the text before its second `##`; with `section`: that section,
a `##` with its `###`). A piece is at most `guides.SECTION_CHARS` (16,000) characters; a longer one
ends with where to read on (`offset`) and, for a `##`, its subsections to ask for. They total about
100 KB, so they are read on demand, a section at a time; the Supervisor's prompt says to read the
contents first and the part a setting is in before changing it.

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
choice). **Amended 2026-11-01** (the owner's proposal 4, `machine-design.md` §2.1): a measurement
script of at most 100 lines is configuration the Supervisor writes itself, to the state directory,
through the design tools' `add_instrument` (below); only a longer one, or one the owner wants in
the repository, goes through the Coordinator. Another store is looked at before it is declared (`check_sources`, `source_schema`,
`preview_query`, `infer_source`, over `orchestra.sources`, by a declared name or by `url` and `auth`;
no credential's value is shown). **Every tool that lists, counts or reads another store's series
stays inside the source's bound** (since 2026-11-03, bootstrap run 4: `find_balance` and
`source_schema` listed all 337 names of a shared Prometheus twice and listed 327 and 351 series
across both namespaces, after the owner said "only our namespace hackriff, at most 200 series";
`source_bounds.py`): the bound is the source's `match` (for a store given by `url`, the `bound`
argument) and its `max_series`. Every selector sent (`source_schema`'s listings and lateness
probes, `find_balance`'s names, levels, candidates, reads and the `[[series]]` it declares) is
ANDed with it, a bound of several selectors read as their union (`or`); no listing asks for more
than `max_series` series; `preview_query` refuses a query on a bounded source that does not name
the bound's matchers; and a store with no bound (no `match`, or the match-everything default) is
never asked for every metric name: `source_schema` without `match` and `find_balance`'s search
refuse and say why (`find_balance` with given `candidates` still reads them).

**Seeing** (tool group `observe`; any role may be granted it; every list in these answers is its
count and its first `answers.LISTED`, a log line at most 300 characters): `check_setup` (who fills the roles,
as `fillers.py` observes them; the work mode; the gate's tests; the capacity limit; what the
launcher runs; each collector's last round; what each installed plugin's `Plugin.check` says against
the merged configuration, in its own words, a check that raises reported as `its check failed: <error>`
without stopping the others, `plugin_findings.py`; and `ci`, the project's CI as its
`.github/workflows/*.yml` say it, each file's name, triggers and jobs, read line by line from the
tree, `project_ci.py`, which the first answer names, bootstrap run 3), `read_config` (orchestra.toml, whether it can be
changed now, its recent commits; whether the owner has a private overlay and each key it sets,
secret values left out), `list_processes` (Orchestra's runtime record and the declared
collectors), `read_machine_log` (a process's or collector's log, Orchestra's host's, or the log a
machine announced, read only within the repository or the state directory, as Orchestra's web
server reads one), `list_metrics` (the project's store, as the Core page's catalogue),
`get_model` (Orchestra's model view, shortened), `read_instrument`, the four sources tools,
`snapshot` (the task system's query, offered only with the task system installed), `read_events`
(types or prefixes, `since`, newest or oldest first), and `list_machines` (Orchestra's machines view
over the bus: each machine's kind, engagement and announced notes, `disabled` first,
`machines_read.py`), `machine_ages` (each machine's age, below), and `read_outbox` (the outbox plugin's state API through Orchestra's web server:
the drafts waiting, sent and rejected, with how each was approved; `outbox_read.py`). All of it reads through Orchestra as a library; nothing is written.

**"What changed since"** (`read_events` with `since`: its `digest`, `since_digest.py`, which the
Supervisor replies with as it is; since 2026-11-03, T2j s16, the morning after on the standing demo).
**An owner reads it in a minute:** the reply is bounded to 1,500 characters (`LIMIT`; T2j's ran
about 4,000), leaving to "more" first the other events, the machines marked quiet, the machines by
age, the labels and the checks, then naming each list by its entries' names alone, then cutting
each disabled machine's reason to its first words, then naming fewer entries; whenever anything is
left out it says `Say "more" for the full list` before the model line, which stays last. The
owner's "more" is `read_events` with the same `since` and `full` (up to 20 entries per list,
unbounded). **What is wrong now comes first** ("Look first"), from the Workflows plugin's open items
(`item_state.py`: its Items list, each step's `within` guard, and each pile's typical stay, the
median of at least three stays measured by the plugin's own position view over the items' records):
an outside result that never came (an item at a step on an outside system three times the step's
typical stay, at least 30 minutes, or 6 hours with no typical stay yet, with the last thing the
window heard of it: "SCRUM-47 (#51) at sail_check 12 h, waiting on github (usually 6 min), last
heard: SAIL put on #51 at 19:59, nothing since"), attention raised and unanswered, past the step's
limit, and longer than the step usually takes (`since_state.py`); then the window's anomalies
(`since_anomalies.py`) and oddities (the host started three times or more), in the bounded reply in
one line by what each is about. **Replays are never anomalies:** an event a poller marked
`replayed: true` (read again at a resume) is counted apart in the first line ("N replayed at a
resume") and never flagged or listed as new, though what it closes stays closed. **Lists name the
newest first**, the ones still open before the rest, and the Items line names the open items now
with their step and age (those "Look first" named are "the N above"). Without the Workflows plugin
"Look first" says what the bus says alone.

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
  message `Supervisor: <what it is for>: <what changed>` (`set_setting`'s value shown, cut at 60
  characters), the reason as its body and a `Supervisor-For: <what it is for>` trailer, the label
  the pull request's title lists (`set_setting`'s `subject`, else named from the key: "draft
  expiry", "[scm]", "an Outbox kind"; since 2026-11-03, run 4's generic "outbox.kinds set"); a
  failed commit puts the file back. A change to the overlay is never committed. A change to
  `orchestra.toml` that the overlay overrides is answered with a `note` saying so;
- **a change that changes nothing** (`config_change.change`'s "nothing: … already") is neither
  committed nor announced, and the answer says so; every caller, the integrations' steps and the
  design tools' instruments included, publishes nothing for it (run 4: four no-op
  `config.changed` each began an era);
- publishes `config.changed` {section, change, for, reason, commit, target, applies, by} from the
  Supervisor (for the overlay, `change` names the keys without their values), its envelope's
  `subject` saying what kind of thing changed, in the shapes of the core's C3 (2026-11-03: an era
  begins only for `system.changed`, a predicted change, or a `config.changed` about
  `machine:<name>`, `definition:<name>` or `override:<name>`): `machine:<name>` for a
  `[[machines]]` program (`set_machine`, its removal) and for a machine's spec or duties
  (`duties.py` publishes a `config.changed` beside its `role.changed`), `definition:<name>` for a
  workflow declared or changed (`declare_workflow`, only when the file's text changed), and
  `override:<name>` (`write_override`'s `system.changed`); a change that cannot alter the
  system's behaviour is `setting:<key>` (`set_setting`, the roles, the work mode, the gate, the
  capacity), `collector:<name>` (`[[collect]]`, `[[scrape]]`, an integration's poller),
  `source:<name>` (`[[sources]]`) or `instrument:<name>` (the design tools' instruments), and
  begins no era;
- **never makes eras faster than discovery can fit a stock** (`era_guard.py`, since 2026-11-03:
  run 4's `eras_from = ["config.changed", "github.pr.merged"]` began an era every 5.2 minutes and
  the model stayed empty): `set_setting` on `[discovery]` reads, for each `eras_from` filter it
  adds (each configured one when the window or step changes), how often it matched on the bus
  (the median gap of the last 20,000 events' matches) and refuses one that comes faster than
  discovery's minimum era, `MIN_COVER` of the window (`orchestra.sd.discover`) and at least
  `2 * EVENTS_PER_PARAM` steps: 30 minutes on the default hour, with both numbers in the refusal.
  A filter matched fewer than three times is allowed with a caution. An era is for a change that
  can alter how the system behaves, not every merge; the guide says so (`operator.md`);
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

Limits: since 2026-11-02 the Supervisor commits to its own branch, never the base, and never touches
the checkout, so its changes neither move the base under a landing nor block one; the setup tools
still refuse while one lands. A commit to
the base branch (the organisation's records) while the task system's gate is landing a ticket makes that
landing fail (the base moved under it), as any commit there would: the task system pauses its
pipeline, puts the ticket back to APPROVED and asks the Supervisor for attention; `resume_pipeline`
lands it on the next run. A tool's answer about what applies describes the readers' rules; it
does not wait to see them act (except `restart_machine`, which watches the launcher).


**Designing the system** (tool group `design`, the Supervisor's alone, checked in the handler;
`design.py` and `design_dynamics.py`, `design_clearance.py`, `design_instruments.py`, `design_machines.py`,
`design_confirm.py`, `design_passes.py`, `state_machines.py`;
`prompts/design.md`; Orchestra's `machine-design.md`). The owner talks; the Supervisor declares,
measures, drafts and refines; the declared layer is reached only through `intended_api.layer`:
Orchestra's (`orchestra.sd.intended`, through its host's `POST /intended`, `GET /intended`, `GET
/intended/design`; the dependency is an Orchestra with it, and there is no other: with `orchestra
run` down, every design call is refused and nothing is written anywhere else). Each answer names
the page that draws what it recorded, the Machines page for a machine and the Dynamics page for a
stock, effect or flow, and `kept_in` says where it is kept. A draft edit's keys and names are Orchestra's own (`keys.Keys`,
`names.resolve`), so the plugin resolves an edit as Orchestra does: a stock no series measures is
keyed by the level its counters imply, and a discovered id is never accepted for a key. The core
names the tools import (the intended model's ops, names, keys and checks; the model's store, eras
and discovery's minimum era) are those Orchestra's `PLUGINS.md` §11 lists for a plugin, imported in
`intended_api.py` alone. A tool call has one deadline (`intended_api.one_call`): after an endpoint
timeout the rest of the call fails at once, saying what it applied, and `GET /intended/design` is
read once per call until a write; the host skips its design tick for five minutes after a timeout,
so wake-ups keep running.

Every tool takes a `reason`, records provenance itself (`{author, source, turn, message, quote,
reason, request}`, never `via` or `at`, which the store sets: the turn and the owner's message
that began it, which the host keeps, whole or, past Orchestra's 4000 characters, the part around
the quote, with anything secret-looking hidden; a `quote` must be the owner's words in that
message, at least three words or the whole message, on word boundaries, and none on a wake-up),
writes only through the layer and `config_edit`, calls no model, and answers with the change ids.
A call with a quote is the owner's (`author: owner`). **A request-backed call is the Supervisor's
answer to the owner's request**: `author: supervisor` with `provenance.request` the id of an
`orchestra.intent.requested` from `owner` that the layer noted (Orchestra's web server notes each
one it publishes; `GET /intended/design` lists those still open, each with its control, element
and change, which is what the call is checked against; a bare event on the bus is not one), naming
the same control, element and pending change, and backing one call only: the layer counts a request
answered once an op verified it, refuses a call citing one that is not open, and holds one open only
while it can still be acted on. A confirm in answer to the owner's request makes the element the
owner's (`confirmed: {by: owner, via: request}`). At start the host queues one Supervisor wake-up
for each request the layer still holds open, so a click made while it was down is acted on. Every
`confirm`, `discard` and `switch_kind` needs the owner's quote or such a request; changing what the
owner declared (a target on it, unmarking their expect mark, retracting it) needs their quote or
their request, as Orchestra's `guard_owners` says.

The tools: `declare_stock`, `declare_effect`, `set_target` (`compare`: below, above, within),
`declare_stock` and `declare_effect` take a `description` (what the owner says it means); `propose_measurements` (options in order with `cost`, `caveat`, and the
`system`, `series`, `url`, `command` and `bearer_env` each names, kept on the option
(`intended_api.OPTION_FIELDS`, Orchestra's `ops.OPTION`), from which `add_instrument` reads what to run; **clearance from facts**
(`design_clearance.py`): a `source` option, or
one naming a company system, or one reaching anything but this machine, the project's `[metrics]`
store and Orchestra's own endpoint (a URL or `host:port` elsewhere, a bare dotted host name, a host
given to `-h`/`--host`, `ssh`, `nc` or `psql`, a company CLI, a `curl`/`wget` whose URL comes from an
environment variable or has no scheme) is "needs approval", never read or previewed, whatever the
model says; a `[[sources]]` store or a scrape target off this machine is another system and needs
approval too; until data classes exist the owner cannot clear one, and the answers say so; a
secret-looking url or command is refused; `bearer_env` names a credential's variable, never its
value), `add_instrument` (runs only what its option proposed; refused for `existing_series`, for
"needs approval", for a script naming or reaching a company system, for one that runs the
project's build or tests (`runs_project.py`: an instrument measures, never runs the project's code;
`set_collector` and `try_collector` refuse it too), and for a name an entry in the
repository or the overlay (or a draft) already has, so expiry never touches the owner's
instrument; a script of at most 100 lines tried as Orchestra runs it, written to `instruments/`, its
`[[collect]]`/`[[scrape]]` entry in the overlay, one act, with Orchestra's `draft = true` and
`expires`; a `[[scrape]]` reads only its option's URL, so a script given with one is refused and
nothing is written; `simulated = true` and never `draft` for a placeholder, so discovery reads it
like any other, removed at its expiry unless kept and once a real option records; if the option's
record cannot be written after the entry is, the answer says the instrument records and what is
missing; `where = repo` tries it the same way and hands a
script that works to the Coordinator for `.orchestra/instruments/<name>.sh`, through a ticket, a
review and the gate: **a deviation** from `machine-design.md` §2.1, which drafts a pull request in
the outbox for the owner, because the agents make no pull request (A5's guard) and a worker's
ticket is how code reaches the repository here), `keep_instrument` (backed by the owner's quote or
their expect mark on the instrument's series or its element's linked stock; the flag taken off the
entry, written twice, so two configuration changes are recorded until `config_edit` drops a field
in one write (an open prediction is then judged interrupted and renewed, which the answer says);
recorded kept, then the option chosen, which the host's tick retries if that write failed
(`retry_chosen`); at its expiry, a draft the owner's expect mark backs is kept the same way, named
by the mark, and an unkept one is removed, each on its own; a machine that reads the element has its
records written again; every write, removal, keep and expiry announced as `instruments.changed`), `mark_expected`,
`ignore`, and their undoing, `unexpect` and `unignore`; `alias` (a declared machine joined to the name it is heard
under); `propose_attachment` and `revise_attachment` (an instrument's series measure a declared machine:
`machine`, `instrument`, the match as `prefix` and `labels`, `instance_label`; `design_passthrough.py`, each one op
passed through with provenance; there is no `find_machine_series`, not built: the match is given explicitly, read
from the store with `list_metrics` first); `link` (a manual binding, origin declared; publishes a confirmed machine's edits that
waited for it; a confirmed machine that reads the element has its records written again),
`name_part` (`design_names.py`: the owner's own name for a discovered stock, flow or effect, shown
on the Dynamics page as its name with the generated one beneath it, Orchestra's SPEC §4.6
"declared names": the part found by the name the page shows, `shown` with its tags, or its key,
two matches answered with one question, `asked`, and nothing declared; `declare_stock` or
`declare_effect` with the owner's name and `link` to the stock's key, or `declare_flow` between
its two stocks' keys with the name (a flow to or from outside, or one of two between the same
stocks, cannot carry one); the owner's quote required and the name in it; again with a new name it
re-declares the same id, `drop` retracts a declaration it made and refuses one that means more;
with `shown` left out, the owner's words alone are matched to the discovered stocks' shown names and
tags (plurals and a few everyday words for a step: "in flight" is `progress`), the best one named,
a tie one question, and none a refusal listing the model's stocks, so the Supervisor never says a
stock is missing without them; a declared stock of that name not yet linked is linked, never
declared twice; asked for "the stock that matters", the prompt has it `read_model` first, bootstrap
run 3's step 10),
`declare_subsystem` (`design_subsystems.py`, 2026-11-02: the owner's subsystems in words, Orchestra's
`docs/proposals/subsystems.md` §5 and its SPEC §4.6, §10; a box on the Dynamics and Machines maps,
how they are read, never a split of the model: it binds no series and changes no equation,
hypothesis or key): a name for a label's value (`value`, `label` when the owner said it), found
among the partitions Orchestra derives (`model.subsystems`, read in process through
`intended_api.partitions`, `orchestra.sd.subsystems`), a value two labels carry asked as one
question, one another subsystem names renamed; or parts gathered (`members` found by the name the
page shows with their tags, as `name_part` finds them, `machines` by an id the model, the declared
layer or the bus knows; two matches one question, `asked`; a part already in another subsystem
refused, naming it: one subsystem at a time); or one dropped (`subsystem`, `drop`: `retract`).
The owner's quote required and the name in it, sent with `author: owner`, `source: supervisor`.
Unquoted, it is the Supervisor's proposal: only a label's value, named by the value, which
Orchestra keeps `proposed` and draws no box for until the owner confirms (`confirm
subsystem:<id>`, quoted). `read_design` lists the subsystems and the partitions. The digest of
`read_events` with `since` carries one Subsystems line at most once a day (`subsystem_offer.py`,
the time kept in the Supervisor's state, `subsystem_line_at`): its proposals waiting, else the
model's default partition when it has two groups, keeps more flows inside a group than cross, and
none of its values is named, with its crossing count, said and never judged),
`declare_machine` (the envelope Orchestra checks, engagement as flat `mode`, `at`, `tz`, `missed`,
and the opaque `spec`, whose fields are given flat beside the envelope (a nested `spec` is taken for
one release): Universal `invariants`, `steps`, `uses`, said in no AI terms;
Programmatic fields by mode, `role` for an offer-driven one, and the Universal fields as its
specification; ids `^[a-z][a-z0-9_]{1,40}$`; a Universal one managed here drafted in `drafts/` in
the format it runs from; a use no tool group covers is said; its cost as drafted: turns a day by
its schedule, or by how often what wakes it came in the last day, read by time and scaled up from a
shorter history, saying so; priced by the harness's model, nothing on the dummy harness), `refine_machine` (flat changes
applied all or none, each a change with the value it replaced: `add_`/`remove_` receives, emits,
reads, tags, invariants and uses, `steps`, the engagement, and `set_field` for a text field only;
unquoted, or on a confirmed machine even quoted, a pending change; on a confirmed machine the spec
edits of one call are one pending change, since each sets the whole spec), `switch_kind` (the Universal |
Programmatic switch: `{set: {kind}}` and nothing else, so Orchestra applies it at once in answer to
the owner's `switch_kind` request, also on a confirmed machine; the spec is kept; a confirmed
Universal Machine switched to Programmatic has its agent stopped and its records removed, and the
program is handed to the Coordinator; switched back, it runs as an agent again; a switch recorded as
a pending change does the same when it is confirmed, `design_machines.apply_kind`),
`declare_event`, `declare_expected_effect` (flat edits, each with its `kind`, checked here with an
error naming the fields it needs, then through Orchestra's grammar; on a confirmed machine a
pending change, published once confirmed; the horizon at least three runs by the schedule or by the
observed rate of what wakes it, and at least what discovery needs to see an era), `confirm` and
`discard`, and `read_design` (group `observe`: the layer's summary plus each machine's fields and
each event type's declared and heard publishers and listeners).

**Connecting a system** (`design_integrate.py`, `integrate_kits.py`, `integrate_schedule.py`; task
I3, Orchestra's `docs/INTEGRATING.md` as a flow). `integrate_system` takes the system's name, what
the owner wants from it, and the strategy (INTEGRATING.md §2; by default `source` for a
Prometheus-API store and `poller` otherwise; also `collect`, a measurement alone; the bridges are a
library with no readers of their own, the owner, 2026-11-01). For `poller` and `collect` it
scaffolds the bridges plugin's kit (`orchestra_bridges.kits.scaffold`, a dev dependency here,
imported only in `integrate_kits`) into the owner's repository (`.orchestra/machines/<name>/`, the
kit's `--root repo`, live from there: a coding tool may edit it; its own four files are one commit
on `orchestra/supervisor`, again when its `poller.toml` changes, proposed with that branch's pull
request, and the live copy is ignored in this checkout only, `.git/info/exclude`, so `git status`
stays the owner's and pulling the merged pull request replaces the ignored copy with the same files,
tracked from then: `integrate_branch.py`, bootstrap run 3, whose 28 untracked files were the 7
pollers' scaffolds; what a collector writes at run time, its cursor and cache, is the state
directory's already; one handed to the Coordinator, adopted, or in the state directory is not
carried), or into Orchestra's state directory (`<state>/machines/<name>/`, `--root state`) only
with `root: state`, when the owner said the repository must stay clean; it runs the
program's own test against the kit's fake in a child Python with every credential-looking variable
left out of its environment, and only then fills the owner's words and settings into its
`machine.toml` (the fake serves the template's world; a `token`, `token_env` or clearance in the
settings is refused). A poller's system is declared as a machine managed elsewhere (`managed:
external`, programmatic, continuous, `spec.source` `bot.<system>`, joined to that observed source by
Orchestra's `alias` op), with an `appears` expected effect into the stock the owner named (declared
when new); the poller itself is never declared (Orchestra's rule 2). A failed test declares and
plans nothing and hands the script to the Coordinator (`integrate_handoff.hand_on`, `work.requested`
naming the directory, the failure and the kit's rules); so does a system with no template of its
own (the generic `http` kit), whose integration goes on to wait for approval meanwhile, the
hand-off published only once its declarations held and its record was saved. A repository scaffold
handed on is first moved out of the owner's checkout to `<state>/machines/<name>/` (a worker's
worktree is cut from the base branch and lacks the untracked files, and git would refuse to land a
copy over them): the requirements tell the worker to copy it to `.orchestra/machines/<name>/` in its
own worktree, adapt and commit it there, and the ticket's review and gate land it; the record reads
the landed copy once the checkout holds it, and `approve_integration` waits for it, since the
landed script is what runs. An integration whose test failed is called again by the same name: its
test runs again where the script is now (the draft, or the landed copy), and on a pass it goes on
as a first call would, nothing scaffolded again and no second hand-off. A scaffold left with no
record (a killed call) is removed before scaffolding, unless git tracks it. A test that
could not run because pytest is not in Orchestra's Python declares and plans nothing either, but is
no failure and is handed to no one (state `test not run`): the answer gives the reinstall with the
`dev` extra, and the same call again begins it afresh; `approve_integration` refuses both. If a
step after the declaration fails, the error names what stayed in the declared layer (the machine,
the stock), each with its change ids; the answer's `changes` lists every declaration.
**A collector already in the project is configured, not scaffolded again** (T18, 2026-11-02: a
project such as the work demo ships its collectors in `.orchestra/machines/`). Before scaffolding,
`integrate_system` asks the bridges which collectors the project holds
(`integrate_kits.shipped`, over `orchestra_bridges.kits.integrations.listing`: under each of the
bridges' roots, a settings file found by `machinefile.locate` and read by their own reader, its kit
its `kit` key), never a machine's description of itself. The one `name` names, else the only one
from the system's kit, that no integration has yet, is adopted: its own test runs, the owner's
settings are filled into it, the system is declared and its run planned as for a new scaffold, and
nothing is scaffolded beside it (`adopted` in the answer and the record). Several from one kit and
no `name` are refused, naming them; a `name` from another kit is refused; a new name still
scaffolds. A store whose strategy was left out, with such a collector, is measured by it
(`collect`) rather than planned as a `[[sources]]` entry, and a measurement alone may share the
store's name (no machine is declared for it). An adopted directory is never removed or moved: one
git tracks, whose test fails, is handed to the Coordinator to adapt where it is, in the worker's own
worktree. `approve_integration` then says whether the system's actors (`bot.<system>`,
`person.<system>.<account>`) are heard on the bus (`heard`, from the Machines page's view), and,
when not yet, how to confirm it after the first poll (`next`).
**The settings' shapes** (`integrate_settings.py`: `check_settings`, `set_keys`): a string, number, boolean, a list of
them, a table of them (written inline: the Slack kit's per-channel `cadence`, the promql kit's
`aliases`; the tool's schema gives them as rows, `{channel, every}` and `{label, as}`, since it types every
object's keys) or a list of such tables (written as `[[settings.<key>]]` blocks replacing the old ones:
the promql kit's `queries`). Each key and value is checked as before (no credential's key at any
depth, nothing shaped like a secret), and the kits' own meanings too: each query a series' `name`
and its PromQL `query` (and `help`), names unique; each of `selectors` and `match` bounded by the
promql kit's own `bounded` (never the whole store); `cadence` a channel's id to a duration;
`max_backoff` a duration. What is written is read back by TOML and then by the bridges' reader, and
put back as it was when either refuses it.
Without the kits nothing is written, and the answer says so and names INTEGRATING.md's routes (the
bridges plugin installed, or the one-file poller of its §5.2, which is code and goes to the
Coordinator). `source` writes nothing either: the `[[sources]]` entry is planned. The answer
carries `ask_owner`: the owner's approval and where the token is (a variable's NAME or a file's
path), put to them in the chat. `approve_integration` takes a verified quote of the owner (refused
otherwise), the credentials by reference (`integrate_kits.credentials`: `token_env` or
`token_file`, and `user_env` or `user_file` for an HTTP Basic account such as Jira Cloud's email;
a name is one the kits take, `orchestra_bridges.kits.settings.ENV_NAME`, a path is absolute,
nothing that looks like a secret). **A token file is refused only where a commit could carry it**
(`credential_paths.py`, 2026-11-02; until then any `.git` in any parent refused it, so a file in a
harness's ignored `creds/` and every path under a home directory that is itself a repository were
refused, the refusal's own hint among them): inside the project's own repository tree (ignored or
not), or tracked by the repository that encloses it (`git ls-files`). Under a parent repository
that ignores it (`git check-ignore`) it is allowed; untracked and not ignored there, it is allowed
with a caution in the answer (a `git add -A` there would take it). The refusal names the path and
why, and its hint is a place the rule allows (`~/.config/orchestra-bridges/<file>` when it passes,
else a description). The check runs when `integrate_system` is given where the token is (its
optional `token_env`/`token_file`/`user_env`/`user_file`: refused before anything is scaffolded,
recorded for the approval) and again at `approve_integration`, against the file as it is then, the data class (`metadata only` by
default) and the site's address when the owner has not given it. **An address is never guessed**
(`integrate_address.py`, bootstrap run 3, where the template's `https://slack.com` was approved and
the first poll sent a test token there): `base_url` is taken only from the owner's words this
session (its host, and port when given, in one of their messages, the quoted one among them) or a
loopback address on this machine (a kit's fake, or what the kit's probe found running here); a
template's default, an adopted collector's file and a vendor's usual host are not, and a guessed
one is refused with nothing written. With none, `ask_owner` asks for it in one sentence, naming the
template's value for the owner to confirm in their words, and `approve_integration` approves
nothing; it writes
`clearance = "approved"`, `data_class` and those references into `machine.toml` (dropping the
template's `token_env` when the owner named only a file; the scaffolded test reads them from
there, so it is not run again), says whether each variable is set and each file is there and
`chmod 600` (never reading it), then the plan to the owner's overlay through `config_change`: a
`[[machines]]` entry with `restart = "always"` (a process Orchestra's launcher keeps, never an
announced machine: the poller only relays, Orchestra's rule 2; INTEGRATING.md §5.5's Workflows
route is not taken for that reason) and a `[[collect]]` entry for the series, or the `[[sources]]`
entry with `auth.bearer_env` or `auth.bearer_file`, each announced (`instruments.changed` or
`config.changed`). **The entries are written at the approval** (a deviation from the task's
wording, which schedules at `integrate_system`): an uncleared poller run for good only waits,
re-reading `machine.toml`, but its `[[collect]]` measurement would fail every round until the
approval. Each
integration's record (its plan, test, clearance and variable's name) is the Supervisor's state,
`integrations`, in `runtime.json`.

**A machine's three ages** (the owner, 2026-11-02: "a programmatic machine might start its life in
the .orchestrator/machines/ directory, but eventually we want it to be refactored out of that
directory into the actual project directory, and it should be replaced by a simple `npm run` or
`cargo run` or `python3 -m` command"; and 2026-10-27, new functionality starts as an AI agent machine
and is replaced by programs). The lifecycle is the Supervisor's knowledge, in its guide ("A machine's
three ages") and its design prompt: agent, program in `.orchestra/machines/<name>/`, graduated; what
is worth a machine, program versus agent, the company-wide value test for a machine and a stock, and
the share rule (the majority of the system never lives in `.orchestra/`). `declare_machine`'s answer
says the machine's age (`design_machines.age_line`). Two tools carry it:

- `machine_ages` (group `observe`, read only, `machine_ages.py`): each machine heard on the bus in
  its age, joined to its `[[machines]]` entry first by the launcher's process that started it (the
  machines view's `processes`, from each announcement's `process`, which the launcher sets as
  `ORCHESTRA_PROCESS`): a name the project's `[[machines]]` holds is that entry, in the age its
  command says (graduated outside `.orchestra/`); a `<plugin>.<name>` is a plugin's process and never
  a machine's age (the provider is never the machine, Orchestra's rule 2), so it joins no entry and
  the machine is `unknown` unless the project holds its directory. An announcement without
  `process` falls back to the log it announced (the launcher's `<state>/logs/machines/<entry>.log`),
  its id, or a command naming `.orchestra/machines/<id>/`, and the row says so (`by: fallback`, the
  answer's `fell_back` and its note); a program with none of these and no directory is `unknown`.
  The agents' own announcements (the host's, so the runner's and the floor runner's, and the
  demos') go through Orchestra's `Events.announce`, so each carries the launcher's process; the counts, the share of programs in
  `.orchestra/`, and those due to graduate (a program in `.orchestra/` first heard a week ago or
  more in the events read). Whether an agent is a program waiting to be written is "not measured".
  Its line comes before the Subsystems line (at most once a day, `subsystem_offer.py`) and the model
  line, which is last (`since_digest`).
- `graduate_machine` (group `design`, `machine_graduate.py`, `graduate_wiring.py`): a dry run by
  default, kept in the Supervisor's state (`graduations`); then, on the owner's quoted yes and only
  for the plan that dry run showed, the directory moves (`git mv`, or a plain move when git does
  not track it) into the project's tree by its language (Python: `<pkg>/<name>/` with
  `__init__.py` and a `__main__.py` that runs the entry, `python3 -m <pkg>.<name>` or
  `uv run python -m …` with a `uv.lock`; Node: `scripts/<name>/` and a script in `package.json`,
  `npm run <name>`; Rust: `src/bin/<name>/` and a `[[bin]]`, `cargo run --bin <name>`, or a crate of
  its own as a workspace member under `crates/<name>/`, `cargo run -p <crate>`), and every
  `[[machines]]` and `[[collect]]` entry that runs it gets the new command (its arguments kept)
  through `config_edit`. The code is not changed, so the announcement is the same and the machine
  stays the same machine on the bus; the answer names the source to hear again. Refused, with
  nothing moved: an agent's spec (`role.md`), a directory no `[[machines]]` entry runs, one the
  configuration names elsewhere, an uncommitted edit to the directory, the manifest or
  `orchestra.toml`, a destination that exists. **Nothing is committed to the base branch, and the
  checkout is not touched**: the move, the wiring, the manifest and an entry in `orchestra.toml` are
  one commit on `orchestra/supervisor` (a commit of `orchestra.toml` alone would name code its commit
  does not hold), proposed as its pull request (section 10; since 2026-11-02); the launcher keeps the
  old command until it is merged and pulled; an entry in the overlay is changed once the program is
  in the checkout (`live_layers`).

**Publishing a draft prediction.** An edit names keys (or declared ids, read through their
`found_as`); the judge reads the era's ids, so each `stock`, `from` and `to` is resolved to the
current model's id when it is published (`intended_api.resolve`, over the newest saved version
through `ModelStore`), and an edit whose key has no id there waits, saying why ("not linked to a
discovered stock yet: published once you say which one it is" for any declared stock or effect
with no link, whatever reason the layer gives, or "not in the current model");
with Orchestra's layer, Orchestra's own verdict (its "unpublishable draft" items) is read as well.
`publish_waiting`, on a link and on the host's minute tick, reads nothing while no confirmed machine
has an edit waiting.

**Confirm**, by who runs it: one managed elsewhere starts nothing, writes nothing and publishes
nothing here (no `system.changed`: nothing changed, and an era would interrupt every open
prediction); its expected effect is kept on the declaration as an expectation; a Universal one managed here, in
this order: the confirm, its spec, role and agent records written to `machines/` and run from there
with no `create_role` or `create_agent` (its role's tool groups from `spec.uses`, `escalate` with
them; a scheduled one reads its receives each run, listed in what it announces it listens for; a
daily one's last run set to the confirm so it first runs at its next `at` in its `tz`, unless
`missed` is `run once`; a run missed while the host was down is skipped with `missed = "skip"`),
then its expected effect published as the Supervisor's `orchestra.prediction.made`, subject
`machine:<name>`, about a change id chosen first, which the machine's first turn announces as
`system.changed`, recorded on the declaration in a second call and in the Supervisor's own
predictions, so a verdict wakes it (`verdicts.py`); a Programmatic one managed here waits for its
program (a worker's), and its change is announced with `announce_change` once it runs (the answers
name the change id, also for an edit published later). A confirmed pending change rewrites the
records and restarts a resident session; one that changes `kind` or `managed` stops or starts the
agent (`apply_kind`); one that changes the engagement counts the schedule from the confirm. A
prediction an era interrupts is renewed with the change that began the new era, for what is left
of its horizon counted from that entry's own start (at least what one needs), the interrupted
prediction matched by its id (the verdict's `causationid`) and recorded as judged. **Discard** of
a proposal removes it, kept only in the history; of a declared element it is `retract`, backed by
the owner's quote (Orchestra refuses a page's discard request with no change on a declared element,
so retiring a confirmed machine goes through the chat; the coordinator's list asks whether such a
request should mean retract), and a declared machine is then retired (its records removed, its session stopped by the host's next look); discarding a stock
or effect removes its unkept drafts and simulated placeholders and names the instruments the owner
kept, which stay.

The owner's page request, `orchestra.intent.requested`, wakes the Supervisor only when its source
is `owner` (a fixed wake-up, never capped). **The `refine_map` duty is built into the plugin**
(`prompts/duties/refine_map.md` is part of the Supervisor's fixed prompt, like `operator.md`), so
every Supervisor has it whatever its spec lists: the review round runs the refinement passes, a
quick heuristic check with no model, when the declared layer changed, and they run again after the
owner's confirm or discard is answered (the findings in that answer); the first pass runs every
check of `machine-design.md` §3 (an expected part is matched by its key, `name{labels}` or `a -> b
by counter`, against the last saved versions; a type a machine listens for that no declared machine
emits is "not heard on the bus yet", and when a machine heard on the bus publishes that family
already it is named as the expected publisher, never a new one proposed), each later one (at most `design_passes`) follows the
neighbours the one before proposed to redraft, and it stops when one finds nothing new; it wakes
the Supervisor once with one batched proposal; it proposes, never declares.

**Workflows** (tool group `workflows`, the Supervisor's; `workflow_tools.py`,
`harness/tools_workflows.py`; orchestra-workflows' SPEC §7.1, §8, §10). The owner describes a
workflow (an item's steps, who does each, when each is done, its outcomes and guards); the
Supervisor writes it with `declare_workflow`, the only way a definition is written from here. The
Workflows plugin is an **optional dependency**, imported lazily in that one module for its
validated writer (`define.check`, `define.write`), its loader (`define.load`) and its record's
read API (`record.api.Record`): the one exception to rule 3's "nothing is imported from that
plugin". Without the plugin each tool says it is not installed. `declare_workflow` takes the
definition in §7.1's shape (its schema spells out each key), a `reason` and an optional `quote`
(verified as the design tools verify one); the plugin's checks run first and any refusing problem
refuses the call, every problem said, nothing written; otherwise it is written to the state
directory (`~/.orchestrator/<key>/workflows/<name>/workflow.json`, the default) or, only with the
owner's quoted words, to the repository's `.orchestra/workflows/<name>/workflow.json`, **left
uncommitted**: the answer says so and the Supervisor hands it to the Coordinator to land through a
ticket and a pull request (rule 2). The answer carries the path, the plugin's notes (what the
project still lacks for it, shadowing) and where it shows (the plugin's Workflows, Items and Piles
pages); the ledger keeps the line. `list_workflows` reads the loaded definitions back (where each
is defined, its planner, starts, each step's who, needs and outcomes, the loader's problems and
notes); `explain_item` gives an item's summary, its fields with the facts that set them, its
position per workflow, the facts not counted and its last facts. The guide's "Workflows" section
is the Supervisor's reference.

**Starting work** (tool group `work`, the Supervisor's and the Coordinator's; `request_work.py`,
2026-11-01, W9). `request_work` publishes the plugin's `item.requested` (its machine contract:
`workflow`, `brief`, optional `parent`; subject the new item's id, `<workflow>-<6 hex>` unless
given) and, with `fields`, one `item.fact` setting them. Whose request it is follows the design
tools' provenance rule: the Supervisor asks only for the owner, so it must quote the owner's words
in their message of the turn, and the request goes out as `owner` with `via: supervisor`, as
Orchestra's page requests (`orchestra.intent.requested`) do; without a verified quote it is
refused. The Coordinator's request is its own, as `coordinator` (a planner by the contract).
Refused with nothing published: the plugin not installed, a workflow the loader does not load or a
request does not start, an id the record already holds, and no floor to offer it (no live
`orchestra run`, or its `workflows.floor` process not running). The answer carries the id, the
Items page link (`/items/<id>`) and `explain_item` to follow it.

## 11. The model and predictions (`modeling.py`, `model_text.py`, `run_text.py`, `verdicts.py`, `prompts/modeling.md`)

Orchestra discovers a system dynamics model of the system from its metrics, lets any machine read
it, and records and judges a machine's prediction of a change (Orchestra's SPEC §4.5). The agents
get that as tools, so an agent can say what a change will do before it makes it, and is told
afterwards whether it did. The tools hold Orchestra's Python objects (`orchestra.sd.reads.ModelReads`
for the reads, `orchestra.sd.predictions.Predictions` for a prediction, as the agent's instance,
its `source`) in the tool server's process: no HTTP, no token of Orchestra's.

Predictions come from the design tools (`declare_expected_effect` and `confirm`, `design_confirm.py`) and `predict_fix`.

**The tools, in two groups.** `model` reads: the model (`read_model`), series (`read_series`),
the eras and what changed at the last boundary (`read_eras`), observability, an equation checked
(`check_equation`), predictions and their verdicts, and the modeling guide. `predict` states the
caller's expected effect of its own change as a prediction (`predict_fix`: `{flow, factor}`,
`{flow, unchanged}`, `{stock, below|above}`, or nothing for the null test) and announces the
change (`announce_change`).

**Answers an LLM can use.** Each answer leads with `summary`: lines with the key numbers first
(`  drain (ready -> outside) = k * ready [k=0.3]; fitted on 500 events, ±4%`), written by
`model_text` and `run_text` from Orchestra's JSON. Every number comes only with `raw: true`;
series points only with `points: true`, paged.

**Grants** (`builtins.py`): the Supervisor and a worker have both; the Coordinator and a reviewer
`model` (they read and compare, and predict nothing). A custom role or team may be granted either.
Beyond the groups: only the agent that made a prediction announces its change, once
(`AgentStore.add_prediction`).

**Announcing the change.** Nothing is judged until the change is announced with the prediction's
change id. A worker calls `announce_change`: `when: now` publishes `system.changed` (id: the change,
subject: the ticket) as the worker's instance; `when: landed` waits for the ticket's end
(`protocol.ended`: the gate landed it, or in tasks mode its review approved it) and the host then
publishes it as that instance (`modeling.announce_landed`, from the runner's tick; a cancelled
ticket's change is never announced). The Supervisor passes `change` to the setup tool that makes
a configuration change, whose `config.changed` then carries it as its id.

**The verdict wakes the predictor** (`verdicts.py`). The host matches `orchestra.prediction.judged`
by its `correlationid` (the change) to the agent that made it: a resident or custom agent gets a
wake-up saying the verdict; a worker, whose ticket session is over by then, gets one short turn of
its own (`<role>-<ticket>-verdict-<time>`, the `model` tools only, a scratch directory, not the
workspace) to read the verdict and say what it means. Every agent with `predict` lists the
verdict in what it listens for, and a per-ticket one its ticket's end too (`listening.py`). Any
agent may also wake on `orchestra.prediction.judged`, filtered by verdict
(`orchestra.prediction.judged:side effect`).

**The skill.** `prompts/modeling.md`, read with the `modeling_guide` tool, says what stocks, flows,
equations, origins, trust, eras, observability and verdicts mean, what to read before predicting,
how to state a prediction, and how to read a verdict. The equation language in it is Orchestra's own reference
(`GET /language`), filled in when it is read. The role prompts only say when to reach for it.

Limits: a verdict published while the host is down wakes nobody (it is still in
`read_predictions`); a read that needs `orchestra[sd]` beside the agents says so when it is missing.


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

- needs a reason in a sentence; is one commit of `.orchestra/machines/` alone (`spec_git.commit`)
  on the Supervisor's branch `orchestra/supervisor` (since 2026-11-02 never on the base branch,
  section 10), made with git's plumbing on a private index so the checkout is untouched, and
  proposed as its pull request. Until it is merged and pulled, the spec runs from its live copy in
  the state directory (`<state>/agents/machines/supervisor/`); then from the checkout;
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
expected effect (`{flow, factor}`, `{stock, below|above}`, ...), and its
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
`ISSUE`, `def override`), committed alone on `orchestra/supervisor` with its pull request (section
10), never on the base branch and not written in the checkout, so it applies once merged and
pulled; announced as
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

