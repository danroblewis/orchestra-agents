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
   under `.orchestra/`, only when the checkout is on the base branch and otherwise clean; the
   Supervisor's setup tools committing `orchestra.toml` alone (`config_change.py`), when a change
   targets the repository, only when the checkout is on the base branch and that file has no
   uncommitted change; and, since 2026-10-28 (the owner's decision D20), the Supervisor's machine
   spec, committed alone to the base branch on the same terms or else to the side branch
   `orchestra/supervisor` with git's plumbing on a private index, never touching the working tree
   (`spec_git.py`); a repair to the project's code, written and committed in a scratch worktree in
   the state directory on its own branch `supervisor/patch-<name>`, which is removed after and
   whose branch only a ticket takes onward (`patching.py`); and an override file under
   `.orchestra/overrides/`, committed alone on the same terms as `orchestra.toml`. A change to the
   owner's private overlay is never committed. (`restart_machine` takes one entry out in the
   overlay and puts the overlay back byte for byte; it commits nothing.)
3. **The contract is `protocol.py`.** The event names the task system and the agents share live
   there, once; the task system implements the same names (`tasking/assignments.py`,
   `tasking/requests.py`). Change them deliberately, on both sides, and say so in the commit.
4. **Every crossing is on the bus**, as CloudEvents through `orchestra_agents.events.Events`
   (over `orchestra.events` and `orchestra.bus`): assignments, heartbeats, reports, requests,
   wake-ups from attention, the agents' own turn events, the announcements. The ticket is the
   subject and the agent's instance (`supervisor`, `worker/1`) the source. **The host is no
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
   with the role (`access.py`); the MCP server and `orchestra agents api` take the role from the
   token, and refuse a call whose `ORCHESTRA_ROLE` disagrees. `tooling.handle` checks every call
   against that role's tool groups (`access.authorize`) before it runs.
8. **Every harness has a fake** in `tests/e2e/` that speaks its real protocol, and the e2e tests
   run once per harness. Real Claude runs only in the demo, with Haiku, and the demo honours
   `CLAUDE_BINARY` so a test can stand a fake in. The dummy harness is the exception: it is a
   product feature with no product behind it, so it has no fake; the e2e tests run on it as it
   ships, with a script for each fake's knob. The Supervisor runs on its machine spec's model,
Sonnet by default (D20), where the harness takes the name; the other agents on `[agents] model`. A test of anything around the agents (another goal's
   acceptance, the task system's flow, the web) uses the dummy harness, never a model.
9. **State lives in `~/.orchestrator/<key>/agents/`**: `runtime.json` is written only by
   `store.py` under its lock; the rest of the state directory (`logs/`, `sessions/`, the prompt
   each session was started with, `PROJECT.md`) is named in SPEC §7 and written by the host and
   the harnesses, and `PROJECT.md` is seeded by the first read and written by the Supervisor's
   context tool. The organisation's custom records are in the repository under `.orchestra/`.
   Nothing else touches a backend or those files directly.
10. **Humans talk only to the Supervisor** (Orchestra's rule 9). The pages and CLI read; the one
    write a person has is the Supervisor chat. Pausing, resuming, restarting, resetting or
    deleting an agent, role or team is the Supervisor's, through its tools; so is the project's
    setup in `orchestra.toml` (`setup_tools.py`, `restart.py`), which no other role is granted;
    operator controls on tickets are the task system's. The Supervisor changes configuration and
    never edits a file in the checkout; new code is a worker's, through a ticket, a review and the
    gate. Its one exception is a repair (the owner, 2026-10-28, D20; SPEC §12), only through its
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
    tools (`Role.read_only`). `orchestra agents api` runs only inside an agent's session
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
  deferred behind `ToolSearch`, which the model calls itself. `--autocompact` bounds its own
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
