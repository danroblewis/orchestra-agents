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
- a **shared runtime** that runs the configured agents: the host (`orchestra-agents run`). The
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

The built-in organisation is the Supervisor (no team; it talks to the human) and the
`engineering` team: the Coordinator leads it, the worker and the reviewer are its members. The
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
request made is said too: `ticket.created`, `milestone.created`. The
Supervisor and the Coordinator may change tickets; a custom agent may when its roles or team
grant the `tickets` group; a worker or reviewer only reports.

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

`orchestra-agents run` is one process per project, and no machine: the runtime the configured
agents run in (section 1). Orchestra's launcher
starts it: the plugin declares it in `Plugin.processes` when the project's `orchestra.toml` has an
`[agents]` section (`web.processes`), so `orchestra run` keeps it running in the interpreter
Orchestra runs in, as the process `agents.host`; a project without the section gets the pages and
no host. It is started with `--wait`: while another host holds the project, or the control port is
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
  Supervisor refuses the chat with the reason. The web plugin proxies it; `orchestra-agents chat` attaches to it. A host that cannot
  bind the endpoint exits instead of running unreachable;
- wakes the Supervisor when a ticket it created (`ticket.created` with `by` = supervisor: the
  human's requests) is DONE, to leave the human one short message (`landings.py`); the tickets are
  followed in the Supervisor's agent state until they end, as the runner hears it
  (`protocol.ended`: landed, approved in tasks mode, or cancelled). A landing is otherwise not
  named in the Supervisor's digest;
- answers `orchestra-agents status` from its records (`org_views.agents_status`, the same
  picture the machine cards read): the host, the harness and model, the roles it fills, each
  resident agent's state and wake-ups, and the sessions on tickets.

Events the host and the agents publish: `session.started`, `session.settled`, `agent.said`,
`session.timed_out`, `session.error`, `session.interrupted`, `agent.crashed`, `agent.crash_loop`, `agent.corrected`,
`agent.compacted`, `agent.reset`, `agent.paused`, `agent.resumed`, `agent.messaged`,
`context.updated`, `probe.failed`, `probe.recovered`, `role.*`, `agent.*`, `team.*` on changes to
the organisation, `config.changed` and `machine.restarted` from the Supervisor's setup tools
(section 10), and a team's own declared types raised with `emit_event` (rate-limited, and
refused when the cause chain is three deep). The source of every one of them is an agent: a
session's events are the agent's instance (the slot bound to the session, or after the slot was
given back the instance its record names: a turn the previous host left running is cut as
`worker/3`, not as the host); an agent's tool calls are `ORCHESTRA_SOURCE`, the same instance; a
team's probe is the team's lead's (section 5). The host has no source of its own. The web plugin
publishes nothing.

Metrics (`measures.py`): the host writes none. It keeps the counters in its runtime record, moved
in the same write as the session's status, and the plugin declares the instrument that reads
them (`Plugin.collectors`, `web.collectors`): `orchestra run` runs `orchestra-agents metrics`
every discovery step, which prints them as Prometheus text from the record, and Orchestra labels
every point `instrument=agents.turns` (and `project`). The same text is served at
`GET /api/plugins/agents/metrics`. The series: `agents_working{role, machine}` (every measured
role: the resident agents and the roles `serves`, even at 0) and the counters
`agent_turns_started_total{role, machine}`, `agent_turns_finished_total{role, machine}`, named for
what they measure: `role` names the series, and `machine` (the instance's machine, `worker` for
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
`assistant_start`, `token`, `tool_start`, `tool_end`, `assistant_end`, `settled`); name its
transcript, which is the only one kept (`read_transcript` parses it); kill a turn; close.

- **Pi** (`pi.py`): one `pi --mode rpc` process per live session, resident between turns; tools
  through the extension `pi_extension.ts`, which calls `orchestra-agents api`.
- **Claude Code** (`claude_code.py`): `claude -p` per turn, resumed by session id, the
  transcript under `$CLAUDE_CONFIG_DIR`; tools through the MCP server `harness/mcp.py`. It
  compacts its own context (`--autocompact`). `CLAUDE_BINARY` names the binary to run.
- **Codex** (`codex.py`): `codex exec --json` per turn, resumed by thread id, the rollout under
  `$CODEX_HOME`; tools through the same MCP server.
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
reports READY_FOR_REVIEW; a reviewer approves; the Supervisor answers that it is scripted and
changed nothing; the Coordinator and any custom agent reply "Nothing to do." A rule's conditions:
`role` (required; a role's or custom agent's name, or `"*"`), `ticket` (a glob on the ticket id),
`prompt` (a regular expression searched in the turn's prompt), `turn` (the session's nth turn,
counted from its transcript, or a list of them) and `checkout` (whether the working directory is a
git checkout). Its `steps`, in order: `say` (a message), `write` (a file, with `text` and
`append`), `run` (a shell command in the working directory; its output and status are the
result, and the script goes on), `commit` (everything, with this message; a commit even when
nothing changed), `tool` (an agents' tool, with `args`; a refusal is the result, and the script
goes on), `sleep` (seconds), `fail` (the turn fails with this error: `session.error`, and a
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
  `orchestra-agents api`, the two ways a tool call reaches `handle` from outside the host, take
  the role and the ticket from that record (`access.identify`), never from the environment. The
  environment alone would be cosmetic: every agent has a shell, and a shell inside a worker's
  session could run `ORCHESTRA_ROLE=supervisor orchestra-agents api pause_agent ...`. Now that
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

`[agents]` in the project's `orchestra.toml`: `harness` (pi | claude_code | codex | dummy),
`dummy_script` (the dummy harness's script, relative to the repository; section 6), `model`,
`provider`, `thinking`, `serves` (the task system's roles these agents fill; default worker and
reviewer), `coordinator_every` (a scheduled check-in for the Coordinator, like `"10m"`; unset,
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
  (read from its plugin when installed; its own banner is asked to stand aside) (`attention.js`);
- the agents' **machine cards** on the Dashboard and the Machines page (`machine-cards.js`): a
  resident agent is a row with what it last said; a worker or reviewer role is the tickets its
  instances are on, with what each is saying now. The host has no card: it is no machine (whether
  it is up shows on the Agents page and on each agent's session chip).

The agents' look, `agents.css`, is a stylesheet the plugin declares, which Orchestra links in every
page's head. `agents.js` renders markdown itself (marked and DOMPurify, code coloured by
highlight.js, each loaded once from cdnjs); Orchestra renders none.

API at `/api/plugins/agents`: `GET /org`, `/roles/{name}`, `/agents/{name}`, `/teams/{name}`
(the organisation); `GET /agents` (every agent and every session as the instance it runs as,
with the ticket it is on; the cards read this); `GET /state` (sessions and probes);
`GET /sessions/{id}/transcript` (paged, with the markers); `GET /metrics` (the agents'
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
it changed and why; never keep filing tickets the system cannot complete. It never changes code,
in an emergency or otherwise: code is a worker's, through a ticket, a review and the gate.

**Seeing** (tool group `observe`; any role may be granted it): `check_setup` (who fills the roles,
as `fillers.py` observes them; the work mode; the gate's tests; the capacity limit; what the
launcher runs; each collector's last round), `read_config` (orchestra.toml, whether it can be
changed now, its recent commits; whether the owner has a private overlay and each key it sets,
secret values left out), `list_processes` (Orchestra's runtime record and the declared
collectors), `read_machine_log` (a process's or collector's log, Orchestra's host's, or the log a
machine announced, read only within the repository or the state directory, as Orchestra's web
server reads one), `list_metrics` (the project's store, as the Core page's catalogue),
`get_model` (Orchestra's model view, shortened), `snapshot` (the task system's query), and
`read_events`. All of it reads through Orchestra as a library; nothing is written.

**Changing the setup** (tool group `setup`; the Supervisor only, checked in the handler, and not a
group a custom role or team may be granted): `set_agent_roles` (`[agents] serves`),
`set_work_mode` (`[project] work`), `set_gate_unit` (`[[gate.unit]]`), `set_capacity_limit`
(`[tasks] workers`, `reviewers`), `set_machine` (`[[machines]]`), `set_collector` (`[[collect]]` or
`[[scrape]]`), `remove_entry` (any of those arrays), `set_setting` (any key, or removes it). Each
takes a `target`: `repo` (the default; `set_setting` must be told) for the project's settings in
`orchestra.toml`, or `overlay` for the owner's own or their company's settings (identities,
channels, templates) in the private overlay outside the repository (Orchestra's SPEC §8.1). The
prompt (`prompts/operator.md`) says which goes where. Each tool:

- refuses a reason shorter than a sentence, and for `repo` unless the repository root is a git
  checkout on the base branch and `orchestra.toml` has no uncommitted change;
- changes the configuration only through Orchestra (`project.orchestra.config_edit`, Orchestra's
  SPEC §8.2), which keeps the file's comments and layout and refuses a result that Orchestra's
  readers (its settings, `[[machines]]`, `[[collect]]`/`[[scrape]]`) or the agents' `[agents]`
  (`config_change.agents_check`) accept less well than before; nothing here opens or edits the
  file;
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

