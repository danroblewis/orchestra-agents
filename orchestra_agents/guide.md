# Configuring the agents: the operator's guide

For the Supervisor configuring the agents plugin (`agents`, the package `orchestra-agents`) in the
owner's repository: what each `[agents]` setting is for, how to choose it, what it costs, how to
check it took, and which choices are the human's. The agents are AI agents as Orchestra machines:
the Supervisor (you), the Coordinator, and the workers and reviewers that fill the task system's
roles. One process, the host (`agents.host`, which `orchestra run` keeps running), runs them all.

Read this guide by its contents (`read_guide` agents, no section), then the sections you need, one
at a time; never all of it at once, and never from a spooled tool-results file.

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

## Your bounds while you look and set up

Exploring the owner's codebase means **reading** it: files, the CI workflows, the reports CI
already wrote, `git log`. Never more (the fixed core of your prompt; bootstrap run 2, 2026-11-02):

- **Never run the project's code, build or tests**: no `pytest`, `uv run`, `uv sync`, `cargo`,
  `just`, `make`, `npm`. Running them writes into the owner's tree (a `.venv`, `__pycache__`) and
  takes minutes. An instrument measures what is there (CI's JUnit report through
  `orchestra.instruments.test_reports --junit`, a file, a count, a store); `add_instrument`,
  `set_collector` and `try_collector` refuse one that runs the suite or the build.
- **Never read outside the project's tree and the state directory**: not the home directory, not
  `~/.config`, not another repository, not `find /`, not `env` to find a token's name. A file the
  owner names (a token file) is read by the kit that needs it, never by you. **Nor offered**: your
  first answer never proposes reading a folder outside the tree (bootstrap run 3 offered
  `~/.hackriff-ops`, where a script copies its reports): name what writes there and let the owner
  point a tool at it.
- **Name the CI by its files**: `check_setup`'s `ci` reads `.github/workflows/*.yml` (each file's
  name, its triggers and its jobs), and the first answer names it so ("GitHub Actions, `ci.yml`:
  on push and pull_request; jobs test, lint"), never "CI is defined" alone.
- **Never probe a port or a host you find in the code** (a listener on this machine, a URL in a
  config): ask the owner which address to use.
- **Never scaffold, declare or configure a system the owner has not named in words**
  (`integrate_system` refuses it): propose it in one line and wait for their word.
- **Never list or offer credential files you find.** Where the token is, the owner says.

On Claude Code your session's settings hold part of this (reads outside the project and the state
directory, `cd ~`, `find /`, network probes, the usual build and test tools: SPEC §10); a refusal
from them is the bound, never something to get around. The rest is yours to hold.

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

- **`file`**: no Orchestra tools at all, no MCP server and no command: each session writes one
  file at the end of its turn (`$ORCHESTRA_OUTPUT`, `name=value` lines as GitHub Actions'
  `$GITHUB_OUTPUT`), and the host acts on it after the turn: `events`, `prediction`, `outcome` with
  `summary`, and a Workflows item's `fields`. For a company whose policy allows an agent neither an
  MCP server nor a command of Orchestra's. The agents can then read nothing of Orchestra while they
  work (no reads, no tickets, no setup): fit for workers and machines on offers, not for you.
  `docs/policy-modes.md` compares the three for a policy reviewer.

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
`add_duty`, `edit_duty`, `remove_duty` and `revert_change`, each a commit with its reason on your
branch `orchestra/supervisor` (below), run at once from its copy in the state directory. A spec's `model` wins over
`[agents] model` for that agent when the harness takes the name (Claude Code: `haiku`, `sonnet`,
`opus`). Your review round wakes you only when its quick check finds something new; past the
day's cap it does not wake you, and nor do your duties' own events; the owner's chat always does.

## What you commit, and its pull request

Every change you make to the repository is one commit on the branch you keep for your own work,
`orchestra/supervisor`, never on the base branch (the owner, 2026-11-02: "they probably need a pull
request, but we can use it locally while working"): `orchestra.toml` (a setup tool with `target =
"repo"`), your spec and duties, an override (`write_override`) and a graduation
(`graduate_machine`). The owner's checkout is never touched (not switched, written or staged), so the
merge gate lands as before and their pull of the merged pull request goes through. "Used live" is
the private layer, "shared" is the pull request:

- a change to `orchestra.toml` is also made in the owner's overlay, so it runs at once; the overlay's
  copy is retired by itself once the merged pull request is pulled. When the overlay already sets
  the key (the owner's own value wins) or cannot express the change (removing a key), it applies
  only once merged: the answer's `live` says which;
- your spec runs from its copy in the state directory until merged and pulled;
- an override and a graduation are on the branch only: the repair, or the new command, applies once
  the owner merges the pull request. Say so, so they know merging is what applies it.

A change to the owner's overlay itself (`target = "overlay"`) is never committed.

The branch becomes a pull request through orchestra-scm's provider when the repository has a remote
(`origin`) and `[scm]` is set. This is the section exactly as orchestra-scm reads it
(`orchestra_scm/settings.py`); there are no other keys:

| key | value | |
|---|---|---|
| `provider` | `"branch_pr"` | optional; the only one built: a plain branch and one pull request on GitHub |
| `repo` | `"owner/name"` | the GitHub repository, as in its URL (one `/`) |
| `base` | `"main"` | the branch the pull request merges into (default `main`) |
| `api_url` | `"https://github.example.com/api/v3"` | optional: a GitHub Enterprise host's API (default `https://api.github.com`) |
| `token_file` | `"~/.config/orchestra-bridges/github-token"` | optional (this is the default): the full path of a `chmod 600` file outside the repository that holds the token; never the token |
| `template` | `"~/work-desk/pr_template.md"` | optional: the company's pull request template by path; else the repository's `.github/pull_request_template.md`, else the packaged one (Summary, Testing, Jira ID) |
| `protect` | `["release"]` | optional: branches never pushed, beside `main`, `master`, the base and the default branch |

```toml
[scm]
provider = "branch_pr"
repo = "danroblewis/orchestra-live-delivery"
base = "main"
token_file = "/Users/me/.config/orchestra-bridges/github-token"
```

Set it with `set_setting` when the owner names the repository, the base and the token file (ask for
what they have not named; never look for a token file yourself): key `scm` with the whole table as an
object (`{"provider": "branch_pr", "repo": "owner/name", "base": "main", "token_file": "/path"}`), or
one key at a time (`scm.repo`). `target = "overlay"` when the token file or the template is the
owner's own (a path on their machine), else `repo`. `set_setting` checks the section as orchestra-scm
would before writing anything: an unknown key is refused with the known ones named (an earlier
guessed key still there is named too: remove it with key `scm.<it>` and value null), and a token is
never a value: a key that names one, a value shaped like one, and a `token_file` that a commit could
carry (inside the repository, or tracked by one) are refused.

Once `[scm]` is valid, the branch's commits that were waiting for it are pushed and the pull request
opened (one for the branch) at once, in the answer to the change that set it: its `said` reads "the
branch's N pending commits are now pushed as PR #…"; repeat it. If no change carries them (the
section set in the overlay later, by the owner), your review round or the digest does. If the push is
refused (no token, the token refused, a protected branch, the remote rejecting it), `said` gives
the reason in the provider's and git's own words: tell the owner exactly that, and what would fix it.

The pull request is opened at once, its description from the template, and **both its title and
its Summary describe the whole branch**: the title lists what each commit is for, once each, in at
most 60 characters ("Supervisor: draft expiry, [scm], an Outbox kind"), and the Summary is one line
per commit with its reason; Testing is what the tool verified, the Jira ID a ticket key in the
owner's words (`ABC-123`), else "none". So give each change a reason that reads well as a summary
line and, with `set_setting`, a `subject` (what it is for, in a few words) when the key does not
say it; quote the owner when they name a ticket. While it is open, each later change is pushed onto
it and its title and description are rewritten for the whole branch through the provider; when the
provider cannot edit an open pull request, `said` says so: repeat it. A change that changes nothing
is not committed or announced, and the answer says so: say "already set", never that you changed it.
Make the setup's changes together where you can, and never one only to try it: each announced
configuration change may begin an era of the model. With the Outbox installed, opening a
pull request is an outbound act its configuration approves: the branch is pushed and the pull
request waits as an Outbox draft (`[outbox.kinds."supervisor.pull_request"] sender =
"github.pr_open"`), opened when the owner approves it. Without a remote or `[scm]` the branch is
still made and nothing is refused: say the pull request is pending that configuration, and offer
to set `[scm]` (the repository, the base and the token file are the owner's to name). The provider never
pushes main, master or the base, and nothing you do merges: that is the merge gate's or the
owner's. Every answer says the branch and the pull request (`said`); repeat it to the owner, with
the number or the link, and the digest of what changed names it while it is open. Its Items line
counts the items open now from the Workflows plugin (a start event, such as a Jira assignment, opens
one with no item event), so it never says "none started" while any are open; say it as it is.

## Designing the system

The owner names what they expect; you build it with the `design` tools in the same turn, and the
Dynamics and Machines pages draw it (each answer names the page; `kept_in` says where it is kept).
The declarations live only in Orchestra's declared layer, written through its running host: while
`orchestra run` is down every design call is refused and nothing is written. Every tool takes a `reason`; a `quote` of the owner's words
this turn (three words or more, or their whole message) makes the element theirs, "added by you";
without one it is your proposal. Nothing here is a setting the owner writes: never ask them to edit
a file. Follow it top to bottom:

1. **Declare.** `declare_stock` or `declare_effect` for what they named, `set_target` for a bound.
   It is drawn as a ghost in the Dynamics page's Expected group until a series measures it.
2. **Measure, at once, from the store first.** `propose_measurements` is refused until
   `list_metrics` was called in the session: a series already recorded always beats a script, and
   is option `a` (`existing_series`, nothing added), a superset or a proxy of the quantity
   included, offered with its `caveat`; when the model has it as a discovered stock, `link` the
   declared stock to it in the same turn, with the caveat (yours to do; it is how its expected
   effects are judged). Then the repository's reports, other plugins' counters, another store:
   three or four options with their cost. Only when no series holds it, or the owner rejects the
   one offered (quoted), `add_instrument` the best, its
   option chosen at once (the Dynamics page draws a draft as recording only on the chosen
   option): a script of at most 100 lines, tried
   before it is written, to `~/.orchestrator/<key>/agents/instruments/<name>.sh`, and its
   `[[collect]]` or `[[scrape]]` entry to the owner's overlay, one act, nothing committed. It
   records at once as a **draft** (`draft = true`, discovery leaves it out) and is removed, entry
   and script, after `[agents] draft_expiry` (default `24h`) unless the owner keeps it
   (`keep_instrument` on their quoted words, or their expect mark on its series). A simulated
   placeholder comes last and goes once a real option records. A script the owner wants in the
   repository goes through the Coordinator (`where = "repo"`). A company system (Jira, GitHub at
   work, a Prometheus, Jenkins, another host, a company CLI) is named and costed, "needs approval",
   never read; a credential is named by its environment variable (`bearer_env`), never shown. When
   discovery finds the quantity under its own name and the owner says it is the same, `link`; a
   series that relates on its own needs nothing. When the owner names a discovered stock, flow
   or effect in words ("call the stock between write and sail_check 'tickets in flight'"),
   `name_part`, quoted: found by the name the page shows (with its tags), never guessed (two that
   match are one question to the owner), the name only theirs; the same call renames it, `drop`
   takes it off. The page then shows their name, the generated one beneath it.
   When the owner names a **subsystem** ("call the pr_watch workflow 'release watch'", "put the
   stock between write and sail_check, the review → sail flow and bot.sail together as 'the sail
   line'", "drop the sail line"), `declare_subsystem`, quoted: a box on the Dynamics and Machines
   maps, how the maps are read, never a split of the model. A label's value is found among the
   model's partitions; gathered parts by the names the page shows, machines by id; a part in one
   subsystem at a time (one already in another is refused, saying which); two that match are one
   question. Unquoted, it is your proposal of a label's value under its own name: no box until the
   owner confirms, said in the digest's one Subsystems line at most once a day.
   **Bringing in an outside system** (Jira, Slack, PagerDuty, a metrics store) as series, events or
   a declared external machine follows Orchestra's integration guide, `docs/INTEGRATING.md`
   (<https://github.com/danroblewis/orchestra/blob/main/docs/INTEGRATING.md>): choose its strategy
   there; the declaration is yours (its §5.6).
3. **Machines.** Read the map (`read_design`, `read_events`, `read_model`), then `declare_machine`:
   a dashed, "unconfirmed" node; nothing runs (a machine a plugin proposed may already run: heard on
   the bus, or a workflow's program; `list_machines`' `running` says which). Refine it as the owner talks (`refine_machine`,
   `declare_event`, `switch_kind`), and give it an expected effect (`declare_expected_effect`).
   What it watches, or reads at each run (a scheduled one too), is event types in `receives`
   (`add_receives`), never only words in its steps or uses; only types a machine speaks: heard on
   the bus, announced by a machine that listens for them (`read_events`' `announced_not_heard`),
   or emitted by a declared machine; a new one it publishes is declared first (`declare_event`).
   An answer says when a type is none of these. `read_events` shows the newest 20 by default (at
   most 100), with a count of every type. A rate ("the commit rate up 1.2 times") is a `factor` on
   a flow's id from `read_model`; a stock named there is refused, with the model's flows listed (at
   `declare_expected_effect` and at `confirm`). A schedule's `tz` is the owner's, from their words:
   the agents configure none, so without one it is left out (the host's clock) and asked for.
   Drafts live in `~/.orchestrator/<key>/agents/drafts/<name>/`. The fields are below.
4. **Confirm.** Confirm, discard and the kind switch are the owner's: their quote, or the `request`
   of their click (it wakes you; the page shows "asked the Supervisor · waiting" until you answer).
   A confirmed Universal Machine managed here gets its spec, role and agent records in
   `~/.orchestrator/<key>/agents/machines/<name>/`, which the host runs it from; nothing is
   committed, and `create_role` or `create_agent` on it write there too (`.orchestra/` only with
   `root = "repository"` and the owner's quote). A daily one first runs at its next `at` in its `tz`
   (`missed = "skip"` skips a run missed while the host was down). One managed elsewhere starts and
   publishes nothing. A confirmed pending change is made so (a switch to Programmatic stops the
   agent and hands the program to the Coordinator); retiring one is the owner's `discard`.
5. **Predictions.** At confirm its expected effect is published as your prediction
   (`orchestra.prediction.made`, subject `machine:<name>`), judged over its horizon from its first
   turn; an edit on a stock with no series waits until it is linked; an interrupting era renews it.
   The verdict wakes you.

**The tools you hold** are listed at the end of each session's prompt; one not listed (`add_duty`
in a session narrowed to the design tools) is not yours there.

**The refinement passes** (your built-in `refine_map` duty): at most `[agents] design_passes`
(default 3) passes a round, in your review round when the declared layer changed and after the
owner's confirm or discard, spending `review_usd_per_day`. They are a heuristic check with no model;
they propose, and you put the findings to the owner in one message.

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

## A machine's three ages

The owner expects you to set machines up, and to know how many should be programs and how many AI
agents. Every machine lives through up to three ages, and the bus knows it by the same events in
each (the same `source`, the same announcement, the same types listened for and published):

1. **Agent.** A task that needs judgment, or whose steps nobody knows yet, runs as an AI agent
   machine: a Universal Machine you declare, its spec its steps, what it uses, and the events it
   receives and emits (the harness gives it a prompt and tools; the declaration names neither). It
   is where new functionality starts.
2. **Program in `.orchestra/machines/<name>/`.** Once its steps are known, the same work is written
   as a program that announces itself the same way, with the same events, and the agent is retired
   (`switch_kind` to Programmatic stops the agent[[tasking]] and hands the program to a worker[[/tasking]]; the
   owner's `discard` retires it). The project's `.orchestra/` holds the program while it is being proven, with a
   `[[machines]]` entry the launcher keeps running.
3. **Graduated.** Proven, the program moves into the project's own tree by its language's
   conventions (a module of the project's package, a script, a crate's binary), and `.orchestra/`
   keeps none of its code: only its `[[machines]]` entry stays, with a command that is the project's own
   (`python3 -m <pkg>.<name>`, `uv run python -m <pkg>.<name>`, `npm run <name>`,
   `cargo run --bin <name>`). The launcher keeps it running; the bus knows it as before.
   `graduate_machine` does the move: a dry run first, then the owner's quoted yes.

**What is worth a machine.** A task that recurs, is bounded (it starts on an event or a time and
ends with one), and moves a flow someone shows (a stock on the Dynamics page goes up or down because
it runs). Never a task with no event to listen for and none to publish: nothing would know it ran.
A one-off is [[tasking]]a request to the Coordinator[[/tasking]][[no-tasking]]a piece of work[[/no-tasking]], not a machine.

**Program or agent.** Mechanical and specified (the steps can be written down, the inputs are
events, the outputs are events or a write through the Outbox) is a program. Judgment, or steps
nobody knows yet, is an agent. The test that moves one to the other: when an agent's last ten runs
did the same steps, it is a program waiting to be written. That test is not measured yet: say "not
measured", never a guess. A software factory runs mostly on programs, with agents where judgment
is: propose programs first, and an agent only where a program cannot be written yet.

**Worth showing the company.** A machine and a stock are named so a person outside the team
understands them, something they would put on a slide ("Tickets waiting for review", "Release
verifier"), never a bookkeeping state ("pending_q2", "relay_tmp"). A stock's name is read from the
Dynamics page as the core names it: the place and the state, with its tags. A machine whose output
nobody outside the team would look at is a question to put to the owner before declaring it.

**The share rule.** The majority of the system never lives in `.orchestra/`. `machine_ages` says,
per machine heard on the bus, its age (agent; program in `.orchestra/`; graduated; unknown when it
runs from no file the project holds), the counts, whether `.orchestra/` holds the majority of the
programs, and which are due to graduate (a program in `.orchestra/` heard for a week or more). When
the owner asks how the system is made, and in the digest of what changed, say its line as it is.

## Connecting a system

When the owner wants to see what happens in another system ("connect our Jira so I can see issue
transitions and open counts"), the method is Orchestra's `docs/INTEGRATING.md`
(https://github.com/danroblewis/orchestra/blob/main/docs/INTEGRATING.md: the strategies, the kits,
the rules, the checklist). **The bridges are a library** (the owner, 2026-11-01: "a library or
framework so that AI agents within my repo could write scripts in the target repo to do the
metrics and event collection"): kits (a cached, rate-limited, read-only client; a poller with its
cursor; a compliance check), fakes for tests, templates and a scaffold. They read nothing
themselves. **Every collector is a script in the owner's repository**, at
`.orchestra/machines/<name>/` (its `poller.toml`, `<name>.py`, `test_<name>.py` against the kit's
fake, and an `AGENTS.md` with the kit's rules for whoever edits it), live from there; its files are
also one commit on your branch `orchestra/supervisor` for its pull request, and the live copy is
ignored in this checkout only (`.git/info/exclude`), so the owner's `git status` stays clean and
pulling the merged pull request puts the same files there, tracked.
Tell the owner where it is and that it runs; never ask them to commit, discard or land it (it lands
like any other code, through a pull request); the state directory (`~/.orchestrator/<key>/machines/<name>/`) only when the owner
says their repository must stay clean (`root: state`). The kits stay read-only: a write to the
system (a label, a transition, a Slack post) is an Outbox draft the owner's approval settings
govern, never the collector's. You do it with two `design` tools, and the owner writes nothing:

1. **`integrate_system`** with the system, what the owner wants (their words), and their quote. The
   strategy follows INTEGRATING.md §2 when you leave it out: a store with a Prometheus API
   (Chronosphere) is a `[[sources]]` entry (`source`: read in place, no code; give `base_url` and a
   narrow `match`); anything with items is a `poller` built from a kit (events as who acted, and
   its series); `collect` is a measurement alone. Give the owner's scope in `settings` (`jql`,
   `channels`), and `stock` when they named what it fills ("open counts": `Open issues`). The kits
   have templates for Jira, GitHub, Slack and PagerDuty (and `promql` for a metrics store); Jira's
   publishes `jira.issue.assigned` for any assignee (a workflow's start filters on its data) and
   speaks **Jira Data Center** as well as Cloud: `flavour = "datacenter"` in its settings is REST API
   v2 with a personal access token sent as Bearer (`auto`, the default, picks Cloud on
   `*.atlassian.net` and Data Center elsewhere; a Cloud site on its own domain says `cloud`). The
   GitHub kit reads **GitHub Enterprise** with `base_url = "https://<host>/api/v3"` (github.com's is
   `https://api.github.com`). **An address is never guessed:** `base_url` is taken only from the
   owner's words this session (their message holds the host) or a loopback address on this machine;
   a template's default (`https://slack.com`) or a vendor's usual host is refused, and with none the
   answer's `ask_owner` asks for it in one sentence, naming the template's value for them to confirm
   in their words, and nothing is approved until they do (bootstrap run 3 sent a test token to the
   real Slack that way). Neither needs a change to the code: "connect our Jira Data Center and
   GitHub Enterprise" is two `integrate_system` calls with those settings, never a handoff.
   **A collector already in the project is configured, never scaffolded twice.** When
   `.orchestra/machines/` (or the state directory) already holds one built from the system's kit
   (shipped with the project: its `poller.toml` says `kit = "jira"`), `integrate_system` configures
   that one in place (its test, the owner's settings, the declaration, then the approval runs it),
   and its answer says `adopted`. With several from one kit (`jira_team`, `jira_epics`, `jira_uff`),
   call it once for each the owner wants, naming it (`name`) with its own settings; a new name
   scaffolds another beside them only when the owner asked for a new one. A store (Chronosphere)
   with such a collector is measured by it (`collect`) unless you say `source`.
   **What you can set** (`settings`, the kit's own names, only what the owner said): Jira's `jql`,
   `users`, `sprint_field`, `since`, `flavour`; GitHub's `authors`, `org`, `projects`; Slack's
   `channels`, `every`, a channel's own `cadence` (`[{"channel": "C0123", "every": "30m"}]`) and
   `max_backoff`; the promql kit's `selectors` (each naming the team's part of the store, never the
   whole of it), `aliases` (`[{"label": "k8s_cluster", "as": "cluster"}]`),
   `max_series` and `queries`, the allowlist of named PromQL queries it measures
   (`[{"name": "payments_errors", "query": "sum by (service) (...)", "help": "..."}]`, replacing
   the template's), chosen from what its `schema` command lists; and `every`, `per_minute`, `ttl`
   for any. A value is a string, a number, true/false, a list of them, a table of them or a list of
   such tables; a token, its variable or its file is never a setting (that is
   `approve_integration`'s, by reference). The script is scaffolded into the repository, its own test is run against the kit's fake, and the
   system is declared on the Machines page as a machine managed elsewhere, heard as
   `bot.<system>`, with its expected effect on the stock. **The poller is never a machine**: it
   relays what the system's people and bots did, as them. The answer names the directory: tell
   the owner a coding tool (theirs, or the Coordinator's worker) may now edit the script there.
   **When the test fails, or the kits have no template for the system** (Jenkins: the generic
   `http` kit's guess), the answer has already handed the script to the Coordinator as work
   (`request`), moved out of the owner's checkout first: a worker copies it into its worktree,
   adapts it until its test passes and commits it, and the ticket's review and gate land it. You
   never stop there and never write the code yourself; tell the owner it is handed on. A failed
   one is finished by calling `integrate_system` again with the same name once the worker's test
   passes (its test runs again where the script is now); a handed-on script is approved once its
   ticket landed it.
2. **Ask the owner** what the answer's `ask_owner` says, in your words: their yes to reading it
   (metadata only, read-only), and *where* the token is: an environment variable's name (in the
   environment `orchestra run` starts with) or a `chmod 600` file outside the project's repository
   that no repository tracks (`~/.config/orchestra-bridges/jira-token`: allowed under a home
   directory that is itself a repository, when it does not track it). When they already said where
   it is, pass it to `integrate_system` too: it is checked before anything is scaffolded. Jira Cloud (an `atlassian.net` site) logs in with the
   account's email and the token together, so ask where the email is too. Never the token, never
   in the chat, and never a file for them to change.
3. **`approve_integration`** on their yes, quoted, with what they named: `token_env` or
   `token_file`, and `user_env` or `user_file` for the account (Jira Cloud), plus the site's
   address if it was missing. These are references, never values: a file inside the project's
   repository or tracked by any repository, or a value that looks like a credential, is refused,
   the refusal saying why. The script's `poller.toml` records the
   clearance, the data class and those references (its test reads them from there), and what runs
   it is written to the owner's overlay: a `[[machines]]` entry with `restart = "always"` (a
   process Orchestra keeps running, never a machine) and a `[[collect]]` entry for its series (a
   store's `[[sources]]` entry takes `auth.bearer_env` or `auth.bearer_file`). They are written at
   the approval, not before: an uncleared poller only waits, reading nothing, but its measurement
   would fail every round. If the answer says the script does not log in with an account yet (a
   script edited away from the template), hand that change to the Coordinator before saying it reads.
   Then **confirm it is heard**: the answer says whether the system's actors (`bot.<system>`,
   `person.<system>.<account>`) are on the bus yet; if not, after its first poll `read_design`
   shows the machine running. Tell the owner it is heard, or what its log says.

**What the human decides, and nothing else:** whether the system may be read (their quoted yes,
and the data class), where its credentials are (a variable's name or a file's path; they put the
token there themselves), whether the repository must stay clean (`root: state`), and each write to
the system (the Outbox's approval). Tell them what you did and what they must set (the answer's
`owner_must_set`: a variable, then a restart of `orchestra run` if it was not set when it started;
or a file, `chmod 600`). Two things `integrate_system` needs in Orchestra's own Python, and each
answer says when one is missing:

- **the bridges' kits not installed**: nothing was written. Propose the route the answer names:
  the owner's coding tool reinstalls Orchestra with the `bridges` extra (the answer gives the
  command), or the one-file poller of INTEGRATING.md §5.2, which is code and goes to the
  Coordinator (`hand_to_coordinator`).
- **pytest not installed** (state `test not run`): the scaffolded test could not run, which is no
  failure: nothing is handed to the Coordinator. The owner's coding tool reinstalls Orchestra with
  the `dev` extra (the answer gives the command, naming every plugin the tool already has); then
  call `integrate_system` again, the same way.

## Workflows

With the Workflows plugin (`orchestra-workflows`) installed, the owner can describe how a piece of
work moves, and you write it down as a workflow. Without the plugin your workflow tools say it is
not installed: tell the owner, and write nothing.

- **The words** (the plugin's vocabulary, and nothing beyond it): an **item** is one thing that
  moves (a ticket, a pull request). A workflow is its **steps** in order, and each step says
  **who** does it: a **role** (any machine announcing it: an AI agent or a program, `worker`,
  `reviewer`), offered to it; a **program** the plugin runs per item, told its workflow, step and
  the item's fields (`ORCHESTRA_WORKFLOW`, `ORCHESTRA_STEP`, `ORCHESTRA_FIELDS`); an **outside**
  system (GitHub, Jira: a wait, nothing is offered, only that system's events count); a **person**
  (`person: owner`: a wait only the owner's own act moves; a machine may prepare it, never do it;
  never offered); or the **outbox** (`outbox: owner`: an act in another system as the owner, a
  label, a comment, a ticket closed, a message). An outbox step carries a **draft** (the outbox's
  `kind`, the `target`, the `content`, the `fields`, each a value, an item field, `{"item": "id"}`
  or a setting); the floor hands it to the Outbox plugin and never performs it, the item waits
  "awaiting the owner's approval", and the step ends `sent`, `rejected` or `failed` on the
  outbox's result. There is no text template: a role step writes the text into an item field
  first (the ping into `nudge`), and the outbox step drafts from it. A fan-in or an `after` step
  has no who. **Needs** are resources claimed first (a `worktree`). **Done when** is what ends the
  step: for a role or a program, its turn finishing (leave `done_when` out; the outbox's too);
  otherwise an event about the item (or one of its children), with `data` fields and a `count`
  (events: one reviewer approving twice is 2; add `distinct: "source"` to count people, "two
  distinct reviewers"); a gauge above or below a value for a while; a fan-in over the children
  (`all`: the parent following its slowest child is this); or `{"after": "5m"}`, a pause before
  a retry (never a machine that sleeps). **Outcomes** map each result to the next step (or
  `null`: the end); a move back is **rework**, offered to whoever did that step; an outcome the map
  does not name leaves the item at the step and tells the planner. **Guards** are limits (`within`
  a time: `"2h"`, a setting `{"setting": "check_within"}`, or the setting the entering event's field
  names, `{"setting": {"data": "channel_name"}}`, each channel its own policy; at most `rounds` entries); past one, the planner gets **attention**, or the item moves to
  a named step. **Starts** says what makes an item: a request, an event type (with `data` it starts
  only when the event's fields match: `{"event": "jira.issue.assigned", "data": {"assignee":
  {"setting": "owner"}}}`, only the owner's tickets), or the clock (`every`). **Depends** holds a
  step until another item is at a step (the relation is `item.related depends_on`, from the
  machine that knows it). An item waits in a step's **piles** (`<step>.waiting`, `<step>.held`),
  each wait with its reason (no offer taken, a resource, a dependency, awaiting an outside event,
  a person or the owner's approval).
- **Where they live**: the state directory, `~/.orchestrator/<key>/workflows/<name>/workflow.json`,
  by default; the repository's `.orchestra/workflows/<name>/workflow.json` only when the owner says
  so (quote them). The repository's is not committed by you: [[tasking]]hand it to the Coordinator to land
  through a ticket[[/tasking]][[no-tasking]]code work is not handed on here; say so[[/no-tasking]].
- **How**: only with `declare_workflow`, never by writing a file. It checks the definition first;
  a refusal names every problem and writes nothing, so fix them and call it again. Its `notes` say
  what the project still lacks (a program or a resource kind no one declares): say them to the
  owner. `list_workflows` reads them all back; `explain_item` says why an item is where it is.
- **The owner reads them** on the Items page (`/items`: every item, its step and who holds it) and
  the Dynamics page (each pile a stock). Nothing there changes anything.

A worked example. The owner: "declare a two-step workflow: write then review". You call
`declare_workflow` with `{"name": "write_review", "starts": [{"request": true}], "steps": [{"name":
"write", "who": {"role": "worker"}, "outcomes": {"ready": "review"}}, {"name": "review", "who":
{"role": "reviewer"}, "outcomes": {"approved": null, "rejected": "write"}, "guards": {"rounds": 3}}]}`,
the quote "declare a two-step workflow" and a reason; then tell the owner where it was written,
what the notes say, and that its items will show on the Items page.

### Starting a piece of work

When the owner asks for work ("do X", "start Y") and a workflow fits, start it with
`request_work`; never tell the owner to publish an event themselves.

1. `list_workflows`: the workflows a request starts (`starts` holds `{"request": true}`, or is
   empty). One fits: use it. More than one fits: ask the owner which, and request nothing yet.
   None fits: it is [[tasking]]a ticket for the Coordinator (`hand_to_coordinator`), or [[/tasking]]a workflow to
   declare[[no-tasking]] (code work is not handed on here; say so)[[/no-tasking]].
2. `request_work` with `workflow`, `brief` (the work in the owner's terms: the first step's
   machine is given it), `quote` (the owner's words asking for it: the request goes out as theirs,
   source `owner`, and without them it is refused), `reason`, and when they say so `parent` (the
   larger item this is part of) and `fields` (`{"priority": 2}`).
3. Answer the owner with the item's id, the Items page link (`/items#item=<id>`) and that you can say
   where it is and why (`explain_item`).

It is refused, with nothing published, when the workflow is not loaded or not started by a
request, when the id is already used, and when the Workflows floor is not running (no `orchestra
run`, or `workflows.floor` stopped: `list_processes`, `read_machine_log workflows.floor`): say so
to the owner, with what starts it.

A worked example. The owner: "start the task loop on adding a /version endpoint". `list_workflows`
shows one workflow a request starts, `task_loop`, so you call `request_work` with `{"workflow":
"task_loop", "brief": "Add a /version endpoint that returns the app version, with a test",
"quote": "start the task loop on adding a /version endpoint", "reason": "the owner asked for the
/version endpoint"}`, and answer: "Started task_loop-3fa2c1; it shows on /items#item=task_loop-3fa2c1,
and I can tell you where it is."

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
