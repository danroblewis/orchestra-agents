# The agents

AI agents as Orchestra machines. This package runs them: it keeps the resident ones up (the
Supervisor, the Coordinator, any custom agent), wakes them on the events they listen for and on
their schedules, starts a worker or a reviewer on a ticket when a task system assigns one, and
shows them in Orchestra's web UI (an Orchestra plugin, `agents`). Which product runs a session
(Pi, Claude Code, Codex) is a setting; the agents behave the same on each.

It meets the task system (orchestra-tasking) only on Orchestra's bus. This file is the design;
`AGENTS.md` is the short list of rules that must not be broken.

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

## 2. The organisation (`builtins.py`, `roles.py`, `org.py`, `org_store.py`, `prompts/`)

Three things, kept apart:

- A **role** is a job description: a prompt in the second person and the tool groups the job
  needs. The built-in roles are in `builtins.py` with their prompts in `prompts/` (a
  `<role>.tasks.md` variant is used when the project's work is tasks, not code); custom roles
  are JSON and markdown under `.orchestra/roles/` in the project repository.
- An **agent** is an instance of one or more roles in a team, and is what runs. Its kind says
  how it is engaged: `continuous` (a resident session, always up, woken by events), `scheduled`
  (every interval or at a daily time; resident or ephemeral), `ad_hoc` (a fresh session each
  time it is briefed or a subscribed event fires). Custom agents live under `.orchestra/agents/`.
- A **team** has one lead agent, members, responsibilities (standing conditions the lead checks),
  access grants (tool groups and repository paths), probes (commands on an interval, section 5),
  and a space under `.orchestra/spaces/<team>/` whose `tools/` is on its agents' PATH. Custom
  teams live under `.orchestra/teams/`.

The built-in organisation is the Supervisor (no team; it talks to the human) and the
`engineering` team: the Coordinator leads it, the worker and the reviewer are its members. The
worker and the reviewer are not resident: a session of each is started per ticket, on the task
system's assignment, for the roles this package serves (`[agents] serves`). A role served by
another machine (the demo's shell worker) is not an agent here.

Every agent's prompt is its roles' prompts, then its team chain's responsibilities, then its
access (`prompting.agent_prompt`). A built-in agent outside a custom team gets exactly its role
prompt. The prompt a session was started with is kept beside its log, so a transcript can show
what the agent was told whatever the harness records.

The Supervisor designs the organisation in conversation with the human (`prompts/org_guide.md`,
the `roles` tool group), and pauses, resumes, restarts, resets and deletes agents the same way;
the Agents page shows it. Writes under `.orchestra/` are committed to the base branch when the checkout is on
it and otherwise clean, and left for the human to commit when not.

## 3. Meeting the task system on the bus (`protocol.py`, `runner.py`, `tooling.py`)

All of it is CloudEvents on Orchestra's bus (`events.py` over `orchestra.events`); the names are
in `protocol.py`, and the task system implements the same names (its `tasking/assignments.py`
and `tasking/requests.py` hold the full field tables).

**Assignment** (`runner.py`, inside the host): the task system offers a ticket to a role with
`task.assigned` (assignment id, role, ticket, workspace, branch or null, lease, the ticket's
brief; a reviewer's offer carries the worker's summary and, in code mode, the commit and the
unit-test output). For a role it serves, the host starts a session for the ticket (`worker-<id>`
or `reviewer-<id>`) as the lowest free instance of the role (`worker/1`), answers
`task.accepted` from that instance, sends `task.working` at least every `HEARTBEAT_S` and sooner
when what the agent is saying has changed (the heartbeat's `text`: a line for the task system's
Tasks panel), runs the turn, and reports `task.reported` with the agent's report (a worker's
`report_ticket`: READY_FOR_REVIEW or BLOCKED with a reason; a reviewer's `report_review`:
APPROVED or REJECTED with feedback), or `failed` when the turn ended without one. A
`task.released` for its assignment kills the turn (`turn.interrupted`, with the reason; nothing is
reported for an assignment the instance no longer holds). A finished ticket's sessions (its
`ticket.transitioned` to DONE or CANCELLED) move to the archive, still readable.

**Requests** (`tooling.py`): an agent's ticket and milestone tools are requests on the bus
(`task.requested`, `task.change_requested`, `task.unblock_requested`, `milestone.requested`, …)
and queries (`task.query_requested` with an `op`: `list_tickets`, `get_ticket`, `snapshot`, …),
answered with `request.done` or `request.refused`; `TaskClient` waits for the answer. The
Supervisor and the Coordinator may change tickets; a custom agent may when its roles or team
grant the `tickets` group; a worker or reviewer only reports.

**Attention**: the task system asks a planner to look at something with `task.attention_needed`
(`to`: supervisor or coordinator, the reason, the ticket). The built-in planners wake on it like
any agent wakes on an event (`wake_on`: `task.attention_needed:coordinator`).

## 4. The host (`host.py`, `residents.py`, `turns.py`, `control.py`, `cli.py`)

`orchestra-agents run` is one process per project, the machine `agents`. It:

- announces itself with `orchestra.machine.started` (package `agents`, continuous, what it
  listens for, its log), and announces every agent instance it starts (an AI agent, on which
  harness and model; a resident agent is continuous, its session always up, a per-ticket or
  ephemeral one ad hoc), so Orchestra's Machines page shows each agent as a machine;
- opens the resident sessions up front and keeps them up: a process that exits between turns is
  recorded (`agent.crashed`) and restarted; three exits in fifteen minutes pause the agent
  (`agent.crash_loop`) and wake the Supervisor;
- each second: reloads the config, matches new events on the bus against every agent's
  `wake_on` (a filter after the colon matches the transition target, a status, a probe name,
  the role an attention event is addressed to, or an agent-raised event's subject), queues
  scheduled wake-ups, runs the teams' probes, answers assignments, and runs one turn per woken
  agent in a thread with the reasons and a snapshot of the project (a query to the task system).
  When the task system does not answer, the prompt says so instead of showing an empty project,
  and a wake-up that was only a schedule is skipped without a turn;
- bounds every turn: it ends when the agent settles, after `silence_timeout` with nothing from
  the harness, or at `turn_timeout` when set (`turn.timed_out`, with what the agent was doing);
  a harness failure is `turn.error`; a restart mid-turn is `turn.interrupted`;
- bounds every session's context: past `session_max_bytes` of transcript since the last
  compaction it asks the harness to compact (`agent.compacted`), and rotates the session when
  the harness cannot (`agent.reset`, the old transcript archived as a run); the events that
  concern one session are also kept on its record, so its transcript shows them without a read
  of the bus;
- on start, refuses to run beside another host of the same project, then stops any turn the
  previous host left running (no orphans: every turn's process is recorded on its session from
  the harness's `turn_start`) and marks the cut. After a host crash the task system re-offers
  the assignment the dead host held when its lease runs out: the host reports nothing it did
  not see end. At a clean stop it waits briefly for each open assignment's `task.reported`
  (`failed`) to go out;
- serves the Supervisor chat on a local control endpoint (`control.py`, 127.0.0.1, the port
  recorded in the runtime record): one turn on the resident session per message, prefixed with
  the project context when it changed and the events since the last turn, streamed as NDJSON.
  The web plugin proxies it; `orchestra-agents chat` attaches to it.

Events the host and the agents publish: `session.started`, `session.settled`, `agent.said`,
`turn.timed_out`, `turn.error`, `turn.interrupted`, `agent.crashed`, `agent.crash_loop`,
`agent.compacted`, `agent.reset`, `agent.paused`, `agent.resumed`, `agent.messaged`,
`context.updated`, `probe.failed`, `probe.recovered`, `role.*`, `agent.*`, `team.*` on changes to
the organisation, and a team's own declared types raised with `emit_event` (rate-limited, and
refused when the cause chain is three deep). The source of a session's events is the agent's
instance; the host's own are `agents`. The web plugin publishes nothing.

Metrics (`measures.py`, Orchestra's metrics store): `agents_working{role}` and the counters
`agent_turns_started_total{role}`, `agent_turns_finished_total{role}`, named for what they
measure; the model generator decides what is a stock or a flow.

## 5. Probes (`probes.py`)

A team may declare probes: a command with an interval, run in the team's space with the team
chain's tools on PATH. Exit 0 means the condition holds. The host remembers each probe's last
result and emits `probe.failed:<name>` and `probe.recovered:<name>` only on the transition, so an
agent subscribed to a probe wakes once per failure. (The task system's own probes, `[[probes]]`
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

The tools are defined once, as data, in `harness/tools.py`, in groups; every harness exposes the
same catalogue to its model, and every call goes through `tooling.handle`, which checks the
caller's role (from the environment the session was started in, never from the agent).

## 7. Settings and state (`config.py`, `store.py`)

`[agents]` in the project's `orchestra.toml`: `harness` (pi | claude_code | codex), `model`,
`provider`, `thinking`, `serves` (the task system's roles these agents fill; default worker and
reviewer), `coordinator_every` (a scheduled check-in for the Coordinator, like `"10m"`; unset,
the Coordinator is woken by events only), `turn_timeout` (default none), `silence_timeout`
(default 20m), `session_max_bytes`, `compact_tokens`, `prompts` (per-role prompt file overrides,
relative to the repository), `control_port` (default any free port). The task system's `[project]` `base_branch`,
`ticket_prefix` and `work` are read for the prompts. The bus and the metrics store are
Orchestra's, found through the Orchestra project.

State is in `~/.orchestrator/<key>/agents/`: `runtime.json` (sessions and archived sessions, each
with the host's markers for its transcript; the reports an agent's tool handed to the host;
queued wake-ups; each agent's state; probe states; the control endpoint; the metric counters,
`metric_counts`; and `supervisor_seen_at`, when the human last read the Supervisor), behind one
file lock and written only by `store.py`; `sessions/` (the harnesses' session records and, for a
harness that keeps its transcript here, the archive; a reset keeps the old transcript where its
harness keeps it and records the path on the archived run); `logs/` (the host's log and, under `agents/`, each
session's stderr and the prompt it was started with); `PROJECT.md`, the project context the
Supervisor maintains. The organisation's custom records are in the repository under `.orchestra/`.

## 8. The web plugin (`web.py`, `org_views.py`, `static/`)

Mounted by Orchestra's web app (`orchestra/plugins.py`):

- the **Agents page** (`/agents`): the event map (who wakes whom through which events, declared
  and observed), upcoming scheduled wake-ups, the teams with their agents, agents outside a team,
  the teams' probes, the roles; a drawer for each agent, team and role (definition, tools,
  prompt, runs) and for each session (its transcript, newest first, with the host's markers for
  cut-off turns, and the Supervisor's chat);
- the **Supervisor button** in every page's navigation and a banner when something waits on the
  human: a message from the Supervisor not yet read, and the task system's blocked tickets
  (read from its plugin when installed; its own banner is asked to stand aside) (`attention.js`);
- the agents' **machine cards** on the Dashboard and the Machines page (`machine-cards.js`): a
  resident agent is a row with what it last said; a worker or reviewer role is the tickets its
  instances are on, with what each is saying now; the host is a line.

API at `/api/plugins/agents`: `GET /org`, `/roles/{name}`, `/agents/{name}`, `/teams/{name}`
(the organisation); `GET /agents` (every agent and every session as the instance it runs as,
with the ticket it is on; the cards read this); `GET /state` (sessions and probes);
`GET /sessions/{id}/transcript` (paged, with the markers); `GET /eventmap`; `GET /attention` and
`POST /attention/seen`; `POST /supervisor` (a message to the Supervisor, proxied to the host,
streamed). The UI is read-only for humans except the Supervisor chat (Orchestra's rule 9): an
agent, a role, a team or a ticket is changed only by telling the Supervisor.

## 9. Cost

Every turn is a model call. The demo uses Claude Code with Haiku, the cheapest model that can
review a result. The Supervisor costs a call per chat message or escalation; the Coordinator one
per attention event and, only when `coordinator_every` is set, one per interval whether or not
anything changed. Everywhere a real model is not needed, the tests use the fakes in `tests/e2e/`
(one per harness, speaking the product's real protocol).
