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
   creates, switches, merges or deletes a branch. The one git write here is committing the
   organisation's own files under `.orchestra/`, and only when the checkout is on the base
   branch and otherwise clean.
3. **The contract is `protocol.py`.** The event names the task system and the agents share live
   there, once; the task system implements the same names (`tasking/assignments.py`,
   `tasking/requests.py`). Change them deliberately, on both sides, and say so in the commit.
4. **Every crossing is on the bus**, as CloudEvents through `orchestra_agents.events.Events`
   (over `orchestra.events` and `orchestra.bus`): assignments, heartbeats, reports, requests,
   wake-ups from attention, the agents' own turn events, the announcements. The ticket is the
   subject and the agent's instance (`supervisor`, `worker/1`) the source.
5. **Nothing outside `harness/` knows which product runs an agent.** The host, the CLI, the web
   plugin and the store use `AgentSession`, the normalized events and `read_transcript`. A
   harness-specific branch anywhere else is a bug. The tools are defined once, in
   `harness/tools.py`; the Pi extension and the MCP server both read that catalogue; never add a
   tool to one harness only, and never a tool with no operation behind it.
6. **The harness's session file is the only transcript.** No parallel log of agent messages;
   the host's events (turn starts, cut-offs, resets) are overlaid on it by the view.
7. **The role comes from the environment** (`ORCHESTRA_ROLE`, set by the host when it starts the
   session), never from the agent; `tooling.handle` checks it for every call.
8. **Every harness has a fake** in `tests/e2e/` that speaks its real protocol, and the e2e tests
   run once per harness. Real Claude runs only in the demo, with Haiku, and the demo honours
   `CLAUDE_BINARY` so a test can stand a fake in.
9. **State lives in `~/.orchestrator/<key>/agents/`**: `runtime.json` is written only by
   `store.py` under its lock; the rest of the state directory (`logs/`, `sessions/`, the prompt
   each session was started with, `PROJECT.md`) is named in SPEC §7 and written by the host and
   the harnesses, and `PROJECT.md` is seeded by the first read and written by the Supervisor's
   context tool. The organisation's custom records are in the repository under `.orchestra/`.
   Nothing else touches a backend or those files directly.
10. **Humans talk only to the Supervisor** (Orchestra's rule 9). The pages and CLI read; the one
    write a person has is the Supervisor chat. Pausing, resuming, restarting, resetting or
    deleting an agent, role or team is the Supervisor's, through its tools; operator controls on
    tickets are the task system's. `orchestra-agents api` runs only inside an agent's session
    (`ORCHESTRA_ROLE`); without a role the tool interface reads and changes nothing.
11. **One package, no path hacks.** Everything importable is under `orchestra_agents/`; `demo/`
    is a template project and its runner, not a package. No `sys.path` manipulation; the one
    exception is pytest's `pythonpath` in `pyproject.toml`, so the tests can import `conftest`,
    the fakes and `demo.run`. Orchestra comes from `../orchestra_2` and the task system from
    `../orchestra-tasking` (both editable); the task system is a dev dependency, for the tests
    and the demo only.

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

## Conventions

- Python 3.12+, `uv` for everything, `pytest` (`uv run python -m pytest -q`), `ruff` for lint
  (`uv run ruff check orchestra_agents tests demo`, never `ruff format`).
- `tests/unit` is fast and in-process (a fake task system answers requests on the bus,
  `tests/fake_tasks.py`). `tests/e2e/test_agents_e2e.py` runs the real task system (`tasking
  run`) as a separate process and talks to it only on the bus, with the host beside it, once per
  harness fake; `tests/e2e/test_demo_e2e.py` runs the demo as processes through a real
  `orchestra run` and `orchestra web`, with the fake Claude Code. Run the tests before every
  commit.
- Keep modules small and single-purpose. If a file needs a section divider comment, it should
  be two files. Comments and docs in plain language, honest about limits.
