# Rules for working on the agents

Read `SPEC.md` first. It is the design. This file is the short list of rules that must not be
broken without updating the spec and saying so in the commit.

## Invariants

1. **The agents change no ticket.** Every ticket or milestone change is a request on the bus that
   the task system applies (`protocol.py`); a worker or reviewer only reports, through its one
   exit tool, and the host passes the report on. Nothing here reads or writes the task system's
   store, and nothing imports from `tasking`.
2. **No branching, no worktrees.** The task system prepares the workspace and names it in the
   assignment; the agents work in it. A reviewer reads the diff in the worktree; the host never
   creates, switches, merges or deletes a ticket's branch. The git writes here are these, and
   each is the Supervisor's or the organisation's own: committing the organisation's own files
   under `.orchestra/`, only when the checkout is on the base branch and otherwise clean; **every
   change the Supervisor makes to the repository as one commit on its own branch
   `orchestra/supervisor`, never on the base branch, and the checkout never touched** (since
   2026-11-02, the owner's self-improvement 5; `side_branch.py`: git's plumbing on a private index;
   the checkout is not switched, written or staged), that branch pushed and proposed as a pull
   request through orchestra-scm's provider, or an Outbox draft of one, when the repository has a
   remote and `[scm]` is configured (`supervisor_pr.py`; the provider never pushes main, master or
   the base, and nothing here merges); what is live until the merge is the private layer
   (`live_layers.py`): `orchestra.toml` (`config_change.py`, a change that targets the repository),
   live through the same change in the owner's overlay; the Supervisor's machine spec (`spec_git.py`,
   D20), live from its copy in the state directory; a scaffolded collector's files
   (`integrate_branch.py`), live from the checkout's copy, which only `.git/info/exclude` ignores; an override file under `.orchestra/overrides/`
   and a graduation (below), live only once merged; a repair to the project's code, written and committed
   in a scratch worktree in
   the state directory on its own branch `supervisor/patch-<name>`, which is removed after and
   whose branch only a ticket takes onward (`patching.py`). A change to the
   owner's private overlay is never committed. Nor is anything the Supervisor's design tools write
   (since 2026-11-01, proposal 4, `machine-design.md` §2.1: at work the base branch is protected):
   a confirmed Universal Machine's spec and its role and agent records are written to the state
   directory (`machines/<name>/`, `state_machines.py`), never to `.orchestra/`, and read beside it. (`restart_machine` takes one entry out in the
   overlay and puts the overlay back byte for byte; it commits nothing.) A workflow definition
   (`declare_workflow`, 2026-11-01) goes to the state directory, and so does a shipped program's
   `machines/<id>/machine.json` (`declare_program`, 2026-11-03), never committed; one the owner asks for in the
   repository is written to `.orchestra/workflows/<name>/` and left uncommitted, for the
   Coordinator to land through a ticket. A graduation (`graduate_machine`, 2026-11-02, the owner: a
   program proven in `.orchestra/machines/<name>/` moves into the project's own tree) is a `git mv`
   of that one directory (a plain move when git does not track it), the one manifest entry its
   language needs, and its `[[machines]]`/`[[collect]]` commands through `config_edit`, on the
   owner's quoted yes after a dry run: one commit on `orchestra/supervisor` with its pull request,
   for the owner or the merge gate to merge; the checkout and the running command change only then.
3. **The contract is `protocol.py`.** The event names the task system and the agents share live
   there, once; the task system implements the same names (`tasking/assignments.py`,
   `tasking/requests.py`). Change them deliberately, on both sides, and say so in the commit.
   The Workflows floor's names are its machine contract, copied by hand into `floor.py` the same
   way (orchestra-workflows' `docs/machine-contract.md`); nothing is imported from that plugin for
   that contract. The exceptions, each a lazy import of an optional dependency that says the plugin
   is not installed when it is not: the Supervisor's workflow tools (`workflow_tools.py`, 2026-11-01,
   W7; that plugin's SPEC §10) import its validated writer, loader and record API, and its start match and settings (`adopt.py`, 2026-11-03), and `program_tools.py`
   its programs reader (`programs.machines`, 2026-11-03, for `declare_program`); and
   `integrate_system` (`integrate_kits.py`, I3) imports `orchestra_bridges.kits` (its scaffold,
   templates and settings, and its `machine.toml` reader); and `propose_people` (`people_tools.py`,
   2026-11-04, B10) imports its identity map and the table that proposes joins
   (`orchestra_bridges.identity`, `orchestra_bridges.people_table`); and `supervisor_pr.py` (2026-11-02)
   imports orchestra-scm's provider and the Outbox's client, and `scm_settings.py` (2026-11-03, S3)
   orchestra-scm's `[scm]` schema.
4. **Every crossing is on the bus**, as CloudEvents through `orchestra_agents.events.Events`
   (over `orchestra.events` and `orchestra.bus`): assignments, heartbeats, reports, requests,
   wake-ups from attention, the agents' own turn events, the announcements. The ticket is the
   subject and the agent's instance (`supervisor`, `worker/1`) the source; the one exception
   (2026-11-01, W9) is the Supervisor's `request_work` on the owner's quoted words, which is the
   owner's request and goes out as `owner` (`via: supervisor`), as Orchestra's page requests do
   (`request_work.py`); without their words, under `[agents] ask = "outside"`, it is the
   Supervisor's own, as `supervisor`. **The host is no
   machine** (Orchestra's rule for a plugin's infrastructure; the owner, 2026-10-27): it never
   announces itself, never publishes as `agents` or with no source (`events.py` refuses both),
   and writes no metrics (the plugin's instrument `agents.turns` reads them from the record). It
   acts on the bus only as the agents it runs, and each agent's announcement lists what the host
   watches on its behalf (`listening.py`). `tests/unit/test_host_is_no_machine.py` holds it.
5. **Nothing outside `harness/` knows which product runs an agent.** The host, the CLI, the web
   plugin and the store use `AgentSession`, the normalized events and `read_transcript`. A
   harness-specific branch anywhere else is a bug. The tools are defined once, in
   `harness/tools.py`; the Pi extension and the MCP server both read that catalogue; never add a
   tool to one harness only, and never a tool with no operation behind it.
6. **The harness's session file is the only transcript.** No parallel log of agent messages;
   the host's events (turn starts, cut-offs, resets) are overlaid on it by the view.
7. **The role is the session's, as the host issued it**, never the agent's: the host starts each
   session with `ORCHESTRA_ROLE` and a token, `ORCHESTRA_SESSION_TOKEN`, whose digest it records
   with the role (`access.py`); the MCP server and `orchestra agents tool` take the role from the
   token, and refuse a call whose `ORCHESTRA_ROLE` disagrees. `tooling.handle` checks every call
   against that role's tool groups (`access.authorize`) before it runs.
8. **Every harness has a fake** in `tests/e2e/` that speaks its real protocol, and the e2e tests
   run once per harness. Real Claude runs only in the demo, with Haiku, and the demo honours
   `CLAUDE_BINARY` so a test can stand a fake in; and in the three opt-in live tests under
   `tests/live/`, outside the default test paths, each skipped unless its variable is set:
   `test_design_live.py` (`ORCHESTRA_LIVE_DESIGN=1`), `test_integrate_live.py` and
   `test_modeling_live.py` (`ORCHESTRA_LIVE_HAIKU=1`), on Haiku only. The design test's free
   rehearsal on the fake Claude (`ORCHESTRA_LIVE_DESIGN=rehearse`) also runs in the default paths,
   as `tests/e2e/test_design_rehearsal_e2e.py`. The dummy harness is the exception: it is a
   product feature with no product behind it, so it has no fake; the e2e tests run on it as it
   ships, with a script for each fake's knob. The Supervisor runs on its machine spec's model,
Sonnet by default (D20), where the harness takes the name, and on `[agents] model` once the owner gives one (over the spec's shipped default, D5, 2026-11-04); the other agents on `[agents] model`. A test of anything around the agents (another goal's
   acceptance, the task system's flow, the web) uses the dummy harness, never a model.
9. **State lives in `~/.orchestrator/<key>/agents/`**: `runtime.json` is written only by
   `store.py` under its lock; the rest of the state directory is listed in SPEC §7, file by file
   (with what `integrate_system` writes beside it, in Orchestra's `<state>/machines/`), and written
   by the host and the harnesses, and `PROJECT.md` is seeded by the first read and written by the
   Supervisor's context tool. `machines/<name>/` holds a confirmed Universal Machine's spec when it
   is not in the repository (machine-design.md §2.1): its `machine.toml`, `role.md`, `role.json`
   and `agent.json`, written by the Supervisor's confirm (`state_machines.py`) and read through
   `machine_spec.load_all`, the one spec loader. Since 2026-11-01 (proposal 4) the Supervisor's
   design tools also write `instruments/` (its measurement scripts), `trials/` (a script's trial
   round) and `drafts/` (a drafted machine's implementation, not official); the declarations
   themselves are Orchestra's declared layer, reached only through `intended_api.py`. The
   organisation's custom records are in the repository under `.orchestra/`.
   Nothing else touches a backend or those files directly.
10. **Humans talk only to the Supervisor** (Orchestra's rule 9). The pages and CLI read; the one
    write a person has is the Supervisor chat (a page's confirm, discard or kind switch changes
    nothing: Orchestra publishes it as `orchestra.intent.requested` from `owner`, and the Supervisor
    answers that request, and only one from `owner`, with its design tools). Pausing, resuming, restarting, resetting or
    deleting an agent, role or team is the Supervisor's, through its tools; so is the project's
    setup in `orchestra.toml` (`setup_tools.py`, `restart.py`), which no other role is granted. The
    one exception (2026-11-04, T55, the owner's standstill report) is this plugin's own operator
    control, which Orchestra's rule 9 lets a plugin own: the Agents page's "Pause the Supervisor's
    own initiative until tomorrow" (and its resume), which holds its review rounds, explore rounds
    and notices until the day ends (`notices.pause`, read by `budget.over`), recorded with who, when
    and from where, and changes no configuration, no machine and no agent's paused state; chat and
    the owner's page requests go on;
    operator controls on tickets are the task system's. The Supervisor changes configuration and
    never edits a file in the checkout; new code is a worker's, through a ticket, a review and the
    gate, or, without the task system, through a Workflows item for the worker role on its own branch
    in a worktree, for the owner's review (2026-11-04, T54; `request_work` with `role`). One amendment (2026-11-01, the owner's proposal 4, `machine-design.md` §2.1): a
    measurement script of at most 100 lines is configuration the Supervisor writes, to the state
    directory (`instruments/<name>.sh`, `add_instrument`), with its entry in the owner's overlay, as
    one act, after a trial round; refused over 100 lines or with a line that looks like a secret.
    One the owner wants in the repository still goes through the Coordinator. A poller scaffolded
    from the bridges plugin's kit (`integrate_system`, since 2026-11-01, task I3) is the kit's code,
    not the Supervisor's: written to the owner's repository (`.orchestra/machines/<name>/`, live from
    there, its own files one commit on `orchestra/supervisor` for that branch's pull request and the
    live copy ignored in this checkout only, `.git/info/exclude`, so `git status` stays the owner's and
    the merged copy replaces it on pull, `integrate_branch.py`, since 2026-11-03, bootstrap run 3; the
    state directory only when the owner keeps the repository clean), adapted by
    a coding tool or the Coordinator's worker (a failed test or a system with no template is handed
    to the Coordinator, moved out of the checkout first; the ticket's review and gate land it), never by the Supervisor, which changes only its `machine.toml` (the owner's
    settings; on the owner's quoted yes, or for a read-only one on its own reason under `[agents] ask =
    "outside"` (2026-11-04, `ask.py`), `approve_integration`, the clearance and the credentials by
    reference: a variable's name or a file's path; the scaffolded test reads them from `machine.toml`)
    and the overlay entries that run it, and (since 2026-11-04, T54, the owner) the files of Orchestra's own
    places with `edit_machine_file` (`machine_files.py`: `.orchestra/machines|workflows|resources/` on its
    branch, the state directory's `machines/`, `workflows/<name>/workflow.json` and `resources/` in place),
    which are setup, not the project's code. A graduation (`graduate_machine`, invariant 2) moves a
    program's files unchanged and writes only the wiring its language needs (a `__main__.py` that
    runs the entry, a script in `package.json`, a `[[bin]]` or a workspace member in `Cargo.toml`):
    no new code, on the owner's quoted yes. So the Supervisor
    writes code of its own in exactly two cases: that measurement script, and a repair (the owner, 2026-10-28, D20; SPEC §12), only through its
    `patch` tools: a patch to the project's code lands through a ticket, a review and the gate; a
    patch to an installed plugin is a project-local override pinned to the installed version,
    never of Orchestra or this package, with its upstream issue an outbox draft the owner approves.
    Its own spec (`.orchestra/machines/supervisor/`) it changes only with its duty tools, each a
    commit with its reason, capped per day; the fixed core of its prompt ships here and no spec
    removes it. Nor does it make
    tickets: it hands the human's requirements to the Coordinator (`work.requested`,
    `handoff.py`), which plans them into milestones and tickets (the `planning` group) and reports
    back (`work.planned`); the Supervisor keeps only the human's decisions on existing tickets
    (priority, cancel, unblock, a note). Its and the Coordinator's sessions have no file-editing
    tools (`Role.read_only`). `orchestra agents tool` runs only inside an agent's session
    (with the session's token); without a role the tool interface reads and changes nothing. The
    pages and the CLI read the organisation through `api_org.read_org`, never an agent's tool.
11. **One package, no path hacks.** Everything importable is under `orchestra_agents/`; `demo/`
    is a template project and its runner, not a package. No `sys.path` manipulation; the one
    exception is pytest's `pythonpath` in `pyproject.toml`, so the tests can import `conftest`,
    the fakes and `demo.run`. Orchestra comes from GitHub (`[tool.uv] override-dependencies`, `main`)
    and the task system too (`orchestra-tasking @ git+ssh://…`), pinned by `uv.lock`; the task
    system is a dev dependency, for the tests and the demo only. To work against local checkouts,
    `uv run --with-editable ~/orchestra_2 --with-editable ~/orchestra-tasking …` (README).
12. **Configuration only through Orchestra's objects** (Orchestra's AGENTS.md rule 10). The
    agents read `project.orchestra.config.raw` (orchestra.toml with the owner's private overlay
    merged), follow it with `reload_config()`, and change it only with
    `project.orchestra.config_edit`; nothing here opens, parses or edits `orchestra.toml` or the
    overlay itself (`tests/unit/test_config_via_orchestra.py` fails otherwise).

## Harness facts the code depends on

- Pi: no MCP, no inbound HTTP, no scheduler; tools via the extension. `pi --mode rpc` reads one
  JSON command per line on stdin and writes events on stdout; `agent_settled` is the completion
  signal; `message_end` with `role: assistant` is the final message. Sessions are JSONL files
  placed by `--session-dir`/`--session-id`. Docs:
  `~/.pi/agent/install/releases/<version>/node_modules/@earendil-works/pi-coding-agent/docs/`.
- Claude Code: `claude -p` is one turn per process; `--output-format stream-json` gives
  `system/init`, `stream_event`, `assistant`, `user` (tool results), `result`. `--session-id`
  names a new session, `--resume <uuid>` continues it from the same cwd. Transcripts live under
  `$CLAUDE_CONFIG_DIR` (default `~/.claude`) at `projects/<cwd with every character but letters
  and digits as ->/<uuid>.jsonl`. Tools come from MCP servers in `--mcp-config`; MCP tools are
  deferred behind `ToolSearch`, which the model calls itself. Its Bash tool passes the `claude` process's environment
  on to the commands it runs, and `--strict-mcp-config` with `{"mcpServers": {}}` loads no server
  at all (both checked with Haiku, 2026-10-28): what `tools_via = "bash"` depends on. `--autocompact` bounds its own
  context; there is no command to compact between turns.
- Codex: `codex exec --json` is one turn per process; the prompt is `-` on stdin (a positional
  prompt with an open stdin hangs on "Reading additional input from stdin"). Events:
  `thread.started` (carries the thread id), `turn.started`, `item.started`/`item.completed`
  (whole items, no token stream), `turn.completed`, `turn.failed`, `error`. Global flags go
  before `resume <thread_id>`. Config comes from `-c key=<TOML value>`; MCP servers are
  `mcp_servers.<name>={command, args, env, required}`; the role prompt is
  `developer_instructions`. Rollouts live under `$CODEX_HOME/sessions/` (`response_item` lines
  hold the messages and function calls, `event_msg` `McpToolCall` items hold MCP calls). MCP
  tools reach the model as a `namespace` tool, which only OpenAI's own API understands.

- Dummy: no product. A turn is `python -m orchestra_agents.harness.dummy_agent <transcript>` with
  the prompt on stdin; it prints the normalized events itself (plus `error` on a failed turn) and
  writes the transcript already in `read_transcript`'s shape. Its tools are `mcp.Server.call`
  in-process, so it gets exactly what an MCP client of the same session would.

## Conventions

- Python 3.12+, `uv` for everything, `pytest` (`uv run python -m pytest -q`), `ruff` for lint
  (`uv run ruff check orchestra_agents tests demo`, never `ruff format`).
- `tests/unit` is fast and in-process (a fake task system answers requests on the bus,
  `tests/fake_tasks.py`). `tests/e2e/test_agents_e2e.py` runs the real task system (`orchestra
  tasks run`) as a separate process and talks to it only on the bus, with the host beside it, once per
  harness fake; `tests/e2e/test_demo_e2e.py` runs the demo as processes through a real
  `orchestra run` and `orchestra web`, with the fake Claude Code. Run the tests before every
  commit.
- Keep modules small and single-purpose. If a file needs a section divider comment, it should
  be two files. Comments and docs in plain language, honest about limits.
