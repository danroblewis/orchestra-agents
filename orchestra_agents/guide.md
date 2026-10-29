# Configuring the agents: the operator's guide

For the Supervisor configuring the agents plugin (`agents`, the package `orchestra-agents`) in the
owner's repository: what each `[agents]` setting is for, how to choose it, what it costs, how to
check it took, and which choices are the human's. The agents are AI agents as Orchestra machines:
the Supervisor (you), the Coordinator, and the workers and reviewers that fill the task system's
roles. One process, the host (`agents.host`, which `orchestra run` keeps running), runs them all.

## Where the settings live

`[agents]` in `orchestra.toml`, or in the owner's private overlay. **The section is optional**: with
no `[agents]` at all the host still runs, on the default harness (below), so a new project needs no
configuration for its Supervisor to set it up. `enabled = false` turns the host off (the Agents
page stays). Change the settings only with your setup tools (`set_setting` with `agents.<key>`, or
`set_agent_roles` for `serves`), never by writing the file. Which file:

- the **repository** when the team shares the choice (every clone should run the same agents);
- the **overlay** when it is the owner's own: their model and its cost on their laptop, a harness
  only they have installed. When unsure, ask; the overlay is the safe default.

The host reads `[agents]` every second. `serves`, the timeouts and `coordinator_every` apply at
once. `harness`, `model`, `provider` and `thinking` apply when no turn is running: the resident
sessions (the Supervisor, the Coordinator) then start again on the new harness, as a new
conversation; their memory is the project context and the record Orchestra keeps, not the old
transcript. `control_port` needs `orchestra run` restarted.

## What the human decides

Propose, recommend, and wait:

- **The harness and the model, and so the cost.** Every agent turn is a model call: the
  Supervisor one per message and wake-up, the Coordinator one per hand-off and attention event, a
  worker and a reviewer one or more per ticket. Haiku is the cheapest model that does the work
  (a ticket's review is about a cent); a larger model is better at planning and code and costs
  several times more per turn.
- **Which roles AI fills** (`serves`): whether AI agents do the work (`worker`), review it
  (`reviewer`), both, or neither (programs do). With `work = "code"` (the task system's, also the
  human's choice) a worker changes the repository's code.
- **How many run at once**: the task system's `[tasks] workers` and `reviewers`. Each slot filled
  by an AI agent is money per hour of work.
- **`coordinator_every`**: a check-in costs a Coordinator turn every interval, whether or not
  anything changed.
- **What your own initiative may spend** (`review_usd_per_day`, `review_turns_per_day`) and **how
  often you may change your own spec** (`spec_changes_per_day`): the owner's caps on you. Propose a
  change with its reason; never raise them on your own.

## `[agents]`

```toml
[agents]
harness = "claude_code"   # claude_code | pi | codex | dummy; unset: claude_code if `claude` is found, else dummy
model = "haiku"           # the harness's model name; unset with an unset harness: haiku
provider = "anthropic"    # pi and codex: the provider; claude_code ignores it
thinking = "low"          # pi: off | low | medium | high; codex: the reasoning effort
tools_via = "auto"        # how agents call Orchestra's tools: mcp | bash | auto (default; below)
serves = ["worker", "reviewer"]   # the task system's roles these agents fill (default both)
coordinator_every = "30m" # a scheduled Coordinator check-in; unset (default): woken by events only
turn_timeout = "30m"      # a hard cap per turn; unset (default): none
silence_timeout = "20m"   # end a turn after this long with nothing from the harness (default 20m)
compact_tokens = 100000   # a session's context past this many tokens is compacted (default 100,000)
dummy_script = "demo/dummy.toml"  # the dummy harness's script; unset: .orchestra/dummy.toml if it exists
control_port = 0          # the Supervisor chat's local port; 0 (default): any free one
spec_changes_per_day = 5  # the Supervisor's changes to its own spec in 24 hours (default 5; a revert is never refused)
review_usd_per_day = 2.0  # what its review rounds and duty wake-ups may spend a day, in dollars (default 2.0)
review_turns_per_day = 48 # ... and in turns, for a harness that reports no cost (default 48)
enabled = true            # false: no host, no agents
# [agents.prompts] worker = "prompts/worker.md"   # a role's prompt replaced by a file of the repository's
```

### Choosing the harness

- **`claude_code`**: Claude Code (`claude -p`), which must be installed on the PATH `orchestra run`
  has and logged in once (`claude` by hand). The default when `claude` is found, with Haiku. It
  compacts its own context. The Supervisor and the Coordinator run without its editing tools.
- **`pi`**: the Pi coding agent (`pi`), any provider and model it supports (`provider`, `model`,
  `thinking`).
- **`codex`**: OpenAI's Codex CLI (`codex exec`); its MCP tools need OpenAI's own API.
- **`dummy`**: scripted agents with no model, no network and no cost: a worker commits a token file
  and reports ready, a reviewer approves, the Supervisor hands a chat message to the Coordinator,
  which makes a milestone and a ticket. For demos and tests, and the default when no `claude` is
  found. A dummy Supervisor cannot read the repository or set anything up; say so to the human and
  recommend a real harness. `dummy_script` (or `.orchestra/dummy.toml`) adds rules: README
  "Dummy agents".

### How the agents call Orchestra's tools (`tools_via`)

- **`mcp`**: through Orchestra's MCP server, which the host gives each Claude Code or Codex turn.
  When Claude Code reports the server not connected, or refuses a call to it, the turn fails and
  says why, and no Claude Code turn starts for a while.
- **`bash`**: no MCP server at all; each session runs its tools from its shell, `orchestra agents
  tool <name> '<json>'` (the system prompt names them and the exact command). The same tools, the
  same checks, the same identity. For an organisation whose Claude Code policy allows only listed
  MCP servers: when the human says "use bash for tools", set `[agents] tools_via` to `"bash"`
  (`set_setting`, key `agents.tools_via`).
- **`auto`** (the default): the MCP server, until it is found blocked: Claude Code reports it not
  connected, or refuses a call to it (a hook in the Claude Code settings, as an organisation's
  whitelist of MCP servers is often written, or a permission rule). Then every session calls the
  tools from its shell until Orchestra restarts, the turn that found it runs again that way (from
  where it stopped, when a call was refused), and you are woken once with the reason (tell the
  human, and offer `bash` to make it permanent).

The sessions start again on the new setting after their turn. Pi always calls the tools through
its extension and the dummy harness in-process: the setting changes nothing there; Codex takes
`bash`, and `auto` is the MCP server on Codex.

The harness the host runs on is on the Agents page and in `orchestra agents status`; when it was
chosen by default, both say so and how to change it.

### `serves`

A role left out is left to other machines: the task system's shell worker or check reviewer
(`[[machines]]`), or anything that speaks its contract. Leave a role out only when something fills
it: an unfilled role leaves every ticket waiting. `check_setup` shows who fills each role now. A
program beside the AI agents in one role takes tickets it was not written for (the shell worker
blocks on a ticket that gives no commands): give it a filter, or take it out of the role.

### Timeouts and context

`silence_timeout` ends a turn that has said nothing for that long (a hung tool, a stuck model);
raise it only for work with long silent steps (a long build inside a turn). `turn_timeout` caps
every turn; a reviewer or worker cut off reports `failed` and the ticket is retried, so a cap that is
too short wastes turns. `compact_tokens` bounds each resident session's context: lower is cheaper
per turn and forgets sooner.

## The organisation

Beyond the built-in agents, the Supervisor designs custom roles, agents and teams in conversation
with the human (`org_guide`); they live in the repository under `.orchestra/`. A team's probes are
commands on an interval; a custom agent may be scheduled (`every`, `at`) or woken by events
(`wake_on`). Each scheduled agent costs a turn per interval: say so when proposing one.

## Machine specs (`.orchestra/machines/<name>/`)

An agent with a machine spec runs with it: `machine.toml` (its `model`, its `[review] every`, its
duties with what wakes each and the tool groups each needs), `role.md`, `duties/<duty>.md`,
`ledger.md`. The Supervisor's is written the first time the host runs (model `sonnet`, a review
every 30 minutes, the duties `keep_flow` and `review_round`); after that you change it only with
`add_duty`, `edit_duty`, `remove_duty` and `revert_change`, each a commit with its reason (on the
side branch `orchestra/supervisor` when the checkout is dirty). A spec's `model` wins over
`[agents] model` for that agent when the harness takes the name (Claude Code: `haiku`, `sonnet`,
`opus`). Your review round wakes you only when its quick check finds something new; past the
day's cap it does not wake you, and nor do your duties' own events; the owner's chat always does.

## Checking it took

- `check_setup`: the roles and who fills them, the work mode, what runs.
- `orchestra agents status` (the human's terminal) or the Agents page: the host, its harness and
  model, each agent's state.
- After a harness or model change: the host's log (`read_machine_log agents.host`) says the
  sessions started again on it; the next turn runs there.

## Pitfalls

- `claude` installed but not logged in: every turn fails with an authentication error in the
  session's log; the human runs `claude` once.
- A harness not installed (`pi`, `codex`): every session fails to start; the host's log says so.
- `serves = []` with no other worker: nothing ever works a ticket.
- A large model on every role multiplies the cost of every ticket by its rounds of review.
- A duty that wakes on a busy event (every `session.settled`, every `gate.started`) is a turn each
  time: wake on what signals trouble (`orchestra.model.stalled`, `gate.rejected`), not on activity.
