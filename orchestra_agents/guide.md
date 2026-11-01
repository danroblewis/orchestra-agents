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
- **Which roles may push** (`allow_push`, default none): every agent's session but yours has `git
  push` to any remote and `gh`'s writes (`pr create`, `pr merge`, `pr edit`, comments, `api` with
  a method) refused, and is told to commit on its branch and leave pushing to the merge gate or the
  owner. Many organisations forbid pushing to their main branch; the owner's credentials are in
  every session. Never add a role to `allow_push` yourself or suggest it to get work moving; only
  when the owner asks, and say what it lets that role do.
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
allow_push = []           # roles whose sessions may git push and write pull requests (default none: the owner's)
coordinator_every = "30m" # a scheduled Coordinator check-in; unset (default): woken by events only
turn_timeout = "30m"      # a hard cap per turn; unset (default): none
silence_timeout = "20m"   # end a turn after this long with nothing from the harness (default 20m)
compact_tokens = 100000   # a session's context past this many tokens is compacted (default 100,000)
dummy_script = "demo/dummy.toml"  # the dummy harness's script; unset: .orchestra/dummy.toml if it exists
control_port = 0          # the Supervisor chat's local port; 0 (default): any free one
spec_changes_per_day = 5  # the Supervisor's changes to its own spec in 24 hours (default 5; a revert is never refused)
review_usd_per_day = 2.0  # what its review rounds and duty wake-ups may spend a day, in dollars (default 2.0)
review_turns_per_day = 48 # ... and in turns, for a harness that reports no cost (default 48)
design_passes = 3         # the most refinement passes over the event map in one round (default 3)
draft_expiry = "24h"      # how long a draft instrument records before it is removed unkept (default 24h)
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

### Pushing (`allow_push`)

A session of any role not in `allow_push` gets a git configuration that rewrites every push URL
to a transport that only refuses, and `git` and `gh` commands first on its PATH that refuse a
push and `gh`'s writes and pass everything else through. It stops a mistake, not an agent set on
pushing (git called by its full path is still stopped by the configuration, except for a remote
with an explicit `pushurl`; `gh` by its full path is not stopped); the prompts say the same. Your own session is not blocked (the owner's decision on it is pending), but your
rules stand: you never push; anything sent as the owner goes through the outbox. A change to
`allow_push` applies to the sessions opened after it: a resident Coordinator keeps its block
until it starts again.

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

## Designing the system

The owner names what they expect (a stock, an effect, a target, a machine); you declare it with the
`design` tools in the same turn and measure it at once. Each answer's `kept_in` says where it is
kept and which page draws it: with an Orchestra that has the declared layer and its pages, the
Dynamics page draws stocks, effects and flows and the Machines page machines, and a `said` line
names the page; with one that has none, no page draws it, and you summarise it from `read_design`
instead of naming a page. Nothing here is a setting the owner writes: never ask them
to edit a file.

- **The tools**: `declare_stock`, `declare_effect`, `set_target`, `propose_measurements`,
  `add_instrument`, `keep_instrument`, `mark_expected`, `ignore`, `link`, `declare_machine`,
  `refine_machine`, `switch_kind`, `declare_event`, `declare_expected_effect`, `confirm`, `discard`;
  `read_design` reads it all back. Each
  takes a `reason`; a `quote` of the owner's words (three words or more, or their whole message)
  makes it theirs ("added by you"); without one it is your proposal. Confirm, discard and the kind
  switch need the owner: their quote, or the `request` of their click, which the declared layer
  noted and which backs that one call; changing what the owner declared (its target, their expect
  mark) needs their quote too.
- **Where writes go**: a measurement script (at most 100 lines) to
  `~/.orchestrator/<key>/agents/instruments/<name>.sh` and its `[[collect]]` or `[[scrape]]` entry
  to the owner's overlay, one act, nothing committed; a drafted machine to `agents/drafts/<name>/`;
  a confirmed Universal Machine's spec, role and agent records to `agents/machines/<name>/`, which
  the host runs it from, nothing committed (one managed elsewhere writes nothing here). A script the
  owner wants in the repository is tried, then goes through the Coordinator (`add_instrument` with
  `where = "repo"`, for `.orchestra/instruments/<name>.sh`). An instrument never takes a name already
  configured.
- **Drafts expire**: an instrument records at once as a draft and is removed, entry and script,
  after `[agents] draft_expiry` (default `24h`) unless the owner keeps it (`keep_instrument`, or
  their expect mark on its series, which keeps it at its expiry on its own). Where Orchestra takes
  it, the entry carries `draft = true` and `expires`, and discovery leaves the series out until it
  is kept; an Orchestra without the flag reads it at once, so it is never removed on a timer (keep
  or remove it on the owner's word), and the answer says so. A simulated placeholder is never
  marked draft (discovery reads it like any other) and is still removed unless kept. Its points
  stay in the store. Keeping one needs the owner's quoted words or their expect mark on its series;
  taking the flag off writes the configuration twice, so an open prediction is judged interrupted
  and renewed. Each write, removal, keep and expiry is announced as `instruments.changed`.
- **Machines after confirm**: a pending change the owner confirms is made so here (a switch to
  Programmatic stops the agent and hands the program to the Coordinator; a new schedule counts
  from the confirm). One managed elsewhere publishes nothing at its confirm. Retiring a confirmed
  machine is the owner's word in the chat (`discard`, quoted).
- **Company systems** (Jira, GitHub at work, a Prometheus, Jenkins, another configured store,
  anything on another host, a company CLI such as `gh`, `jira`, `aws`, `kubectl` or `ssh`) are
  "needs approval" until data classes exist (the owner cannot clear one yet), decided from what an
  option or a script reads; such an option is named and costed, never previewed. A credential is named by its environment variable
  (`bearer_env`), set in the overlay out of band, never written or shown.
- **The refinement passes** (your built-in `refine_map` duty, whatever your spec lists): at most
  `[agents] design_passes` (default 3) passes a round, run in your review round when the declared
  layer changed and after the owner's confirm or discard, spending `review_usd_per_day`. They are a
  quick heuristic check with no model; the judgement is yours, in the one wake-up a round sends.
  They propose; you put the findings to the owner in one message.
- **Confirmed machines**: a daily one first runs at its next `at` in its `tz` (a run missed while
  the host was down is skipped with `missed = "skip"`); its tool groups follow its `uses` (the
  ticket queue, the model, the repository); its expected effect is your prediction, whose verdict
  wakes you; discarding it retires it; switching it to Programmatic stops its agent and hands the
  program to the Coordinator.

### The machine fields (a reference)

| field | meaning |
|---|---|
| `kind` | `universal` (an abstract machine described by its steps) or `programmatic` (a program) |
| `managed` | `orchestra` (started and kept here) or `external` (runs elsewhere, known by its events) |
| `engagement` | `mode` (`ad hoc`, `continuous`, `scheduled`), and for scheduled `at` (HH:MM), `tz`, `missed` (`skip`, `run once`): flat arguments of `declare_machine` and `refine_machine` |
| `receives`, `emits`, `reads` | event types it wakes on, event types it publishes, stocks and effects it may read |
| `spec` (universal) | `invariants`, numbered `steps`, `uses` (what it may act on); no model, prompt or tool names. Switched to programmatic (`switch_kind`), the kind alone changes: they stay, as the program's specification |
| `spec` (programmatic, ad hoc or scheduled) | `command`, or `trigger` when external; `arguments`, `environment`, `stdin`, `exit`; `role` for one answering a workflow's offers (ad hoc) |
| `spec` (programmatic, continuous) | `command`, `protocol` (`http`, `graphql`, `grpc`, `other`), `send`, `receive` |
| `spec` (external) | `source`: the relayed actor whose events it is (`bot.jira`), never the bridge |

## Connecting a system

When the owner wants to see what happens in another system ("connect our Jira so I can see issue
transitions and open counts"), the method is Orchestra's `docs/INTEGRATING.md`
(https://github.com/danroblewis/orchestra/blob/main/docs/INTEGRATING.md: the strategies, the kits,
the rules, the checklist); the bridges' guide (`read_guide bridges`, its kits section) has the
kits' settings. You do it with two `design` tools, and the owner writes nothing:

1. **`integrate_system`** with the system, what the owner wants (their words), and their quote. The
   strategy follows INTEGRATING.md §2 when you leave it out: a store with a Prometheus API
   (Chronosphere) is a `[[sources]]` entry (`source`: read in place, no code; give `base_url` and a
   narrow `match`); anything with items is a `poller` built from the bridges' kit (events as who
   acted, and its series); `collect` is a measurement alone; `bridge` is the bridges' own reader
   (Jira, Slack pings, GitHub) when it already reads what the owner wants, never beside a poller
   over the same items. Give the owner's scope in `settings` (`jql`, `channels`), and `stock` when
   they named what it fills ("open counts": `Open issues`). A poller is scaffolded into the state
   directory (`~/.orchestrator/<key>/machines/<name>_poller/`), its own test is run against the
   kit's fake, and the system is declared on the Machines page as a machine managed elsewhere,
   heard as `bot.<system>`, with its expected effect on the stock. **The poller is never a
   machine**: it relays what the system's people and bots did, as them.
2. **Ask the owner** what the answer's `ask_owner` says, in your words: their yes to reading it
   (metadata only, read-only), and the *name* of the environment variable that will hold the token
   in the environment `orchestra run` starts with. Never the token, never in the chat, and never a
   file for them to change.
3. **`approve_integration`** on their yes, quoted, with that name (`token_env`) and the site's
   address if it was missing. The program's `machine.toml` records the clearance, the data class
   and the variable's name, its test runs again, and what runs it is written to the owner's
   overlay: a `[[machines]]` entry with `restart = "always"` (or the Workflows floor's `every`
   item when that plugin is installed) and a `[[collect]]` entry for its series. Nothing runs
   before this: an uncleared program reads only a fake on this machine.

Tell the owner what you did and what they must set (the variable, then a restart of
`orchestra run` if it was not set when it started). If the answer says the bridges' kits are not
installed, say so and propose the route it names: installing the bridges plugin into Orchestra's
environment, or the one-file poller of INTEGRATING.md §5.2, which is code and goes to the
Coordinator. A failed test is code too: hand it on with the failure.

## Checking it took

- `check_setup`: the roles and who fills them, the work mode, what runs, and what each installed
  plugin's check says against the configuration (`plugin_checks`; a check that failed says so).
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
