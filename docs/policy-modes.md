# How the agents reach Orchestra: three modes, for a policy reviewer

Orchestra runs AI agents (Claude Code, Codex, Pi). An agent tells Orchestra what it did in one of
three ways, set per project in `orchestra.toml`:

```toml
[agents]
tools_via = "mcp"    # or "bash", or "file"
```

(`auto`, the default, is `mcp` until the MCP server is found blocked, then `bash`.)

## Side by side

| | `mcp` | `bash` | `file` |
|---|---|---|---|
| How the agent acts on Orchestra | Calls tools on Orchestra's MCP server | Runs `orchestra agents tool <name> '<json>'` from its shell | Writes one file at the end of its turn; Orchestra acts on it after the turn has ended |
| What the agent's environment needs | An MCP server (a local process, started for each turn) that the product's policy allows | The `orchestra` command (or Orchestra's Python), runnable from the agent's shell | A file it can write: `$ORCHESTRA_OUTPUT`, under Orchestra's state directory |
| MCP servers loaded | Orchestra's only | None | None |
| Orchestra credential in the agent's environment | A session token, in the server's environment | A session token, in the shell's environment | None |
| Can read Orchestra while working (tickets, the bus, the model, settings) | Yes, the tools its role holds | Yes, the same tools | No |
| Can change Orchestra while working | Yes, within its role's tools | Yes, the same tools | No |
| Can report a result, publish events, make a prediction | Yes | Yes | Yes, after the turn, from the file |

In every mode a call is checked the same way: the tool's schema, the role the session was started
as, and that role's tools. The token identifies the session; it is not a sandbox (the agent runs as
Orchestra's OS user).

## The file mode in detail

The agent gets no Orchestra tools, no MCP server, no Orchestra command and no token: nothing it
runs during its turn can act on Orchestra. Its prompt names the file and what it may write.

The format is GitHub Actions' `$GITHUB_OUTPUT`: `name=value` lines, or `name<<END` ... `END`
blocks; a value that parses as JSON is JSON. Orchestra acts on these names only:

- `events`: a list of events to publish, each `{type, subject, data}`, only of the types the agent's
  declaration allows it to emit;
- `prediction`: one prediction of a change's effect;
- `outcome` and `summary`: the turn's result;
- `fields`: facts to record on the work item with the result.

Any other name is kept with the run's record and does nothing. Each line is checked exactly as the
matching tool would check it; a line that fails is refused, its reason recorded with the run, and
the rest still go. Nothing is acted on for a turn that timed out or failed.

It cannot read anything of Orchestra's, change settings, tickets or other agents, or act more than
once a turn: it works from what it was handed. That suits workers and narrow machines; the
Supervisor and Coordinator, whose job is to read and change Orchestra, cannot do it in this mode.

## What every mode keeps

The agent's product keeps its own tools (shell, file edits) in its working directory, under the
product's policy. Host credentials are removed from every
session's environment, and `git push` and `gh` writes are refused unless a role is allowed them
(`[agents] allow_push`; the Supervisor's own session is exempt). Choosing a mode changes how the agent reaches Orchestra, nothing else.
