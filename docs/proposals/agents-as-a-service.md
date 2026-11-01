# Agents as a service: asking for an agent on the bus

Status: proposal, not built, for review (moved here from Orchestra's `docs/proposals/` on
2026-11-01: it proposes a change to this plugin, not to the core). Written 2026-10-27, after the owner settled what the
Agents plugin is:

> The Agents plugin extends the Machine, it gives us an implementation of a Machine. Normally,
> the target repo would be responsible for defining its own Machines, and the author of the
> target repo does this by writing code and programs that publish messages and subscribe to
> messages on the event bus. That can happen either by writing to our event bus with the
> appropriate `source` name for the machine, or it can come implicitly by setting up an
> orchestra-bridges thing for it. The orchestra-agents plugin provides a common resource for
> doing this. It also provides the UI modifications.

Nothing in the code or the other docs is changed by this file.

## 1. The question

Today an agent exists only because someone configured it: the built-in roles, an `[agents]
serves` entry, or a custom agent the Supervisor created in `.orchestra/`. The agents' host then
runs it, and it wakes on its `wake_on` events, its schedule, or an assignment the task system
offers to a role it serves.

Nothing outside the Agents plugin can *ask* for an agent. Another plugin, or a program in the
target repository, that wants "an AI agent to look at this" has two indirect ways:

- publish an event some configured agent has in its `wake_on`, and hope that agent is the right
  one; or
- go through the task system: create a ticket and let the `worker` role pick it up. That brings a
  milestone, a review and, in code mode, a branch and the merge gate.

"Agents as a service" would be a published contract for the first-class version: a request on
the bus that names what kind of agent is wanted and what it should do, and an answer.

## 2. A possible contract

Two events, in the agents' own namespace, documented in orchestra-agents' `protocol.py` beside
the task system's names:

| Event | Published by | Data (owned by the agents package) |
|---|---|---|
| `agent.requested` | the asking machine, under its own source | `request` (an id), `role` or `agent` (which configuration: a role name, or a configured agent's name), `task` (the brief, plain text), optional `subject` (what it is about), `workspace` (a path the asker prepared, or none), `reply_to` (event types the asker listens for, default `agent.answered`), `deadline_s` |
| `agent.answered` | the agent that did the work, under its own source (`analyst`, `analyst/2`) | `request`, `result` (text), `status` (`done`, `failed`, `refused`, `timed_out`), and the request as `causationid` |

How it would run:

1. The host sees `agent.requested`. If `agent` names a configured agent, that agent takes it. If
   `role` names a role, the host runs an ad hoc instance of an agent with that role (a fresh
   ephemeral session, `<role>/<n>`), with the role's prompt, tools and permissions; a role with
   no agent configured for it is refused (`status: refused`, with why).
2. The agent's turn is the request's `task`, with the asker named. Its final message is the
   `result`. The agent may use its tools as usual, so its acts are on the bus under its own
   source, each naming the request as `causationid`.
3. The agent answers with `agent.answered`. The host never answers as itself: it is no machine
   (Orchestra's AGENTS.md rule 2).

What it would not do: no queue of its own, no retries, no claim protocol. A request is for one
agent; if two hosts could serve it, the existing claim rule (`task.accepted`, first valid claim
on the bus holds it) would be reused rather than a second one invented.

## 3. Who the machine is

The machine is **the configured agent that does the work**, never the plugin or the host:

- The asker publishes `agent.requested` under its own source. It announces `agent.answered` in its
  `listens_for`.
- The agent that takes the request publishes under its own source, announces itself as any agent
  does, and lists `agent.requested` in its `listens_for` (`listening.py`), so the event map draws
  asker → `agent.requested` → agent → `agent.answered` → asker. The hand-off inference (subject
  to subject) works when the request carries a `subject`.
- For a request by `role`, each ad hoc instance is `<role>/<n>`: one machine per role, many
  instances, as `worker/1`, `worker/2` are today.

So the service adds no machine. It adds a way to engage an existing kind of machine.

## 4. How it relates to the task system's roles and to wake-ups

| Today | With the service |
|---|---|
| A task-system role (`worker`, `reviewer`) is filled by the agents when `[agents] serves` names it. The task system offers work (`task.assigned`), an agent claims it and passes the work on. Tickets carry the record, the review and the gate. | Unchanged. The task protocol stays the way to do tracked work: anything that must be reviewed, landed or counted as a ticket. `agent.requested` is for untracked, one-off asks that need no review and change no ticket. |
| A configured agent wakes on its `wake_on` events: it decides by itself that an event concerns it. | Unchanged. A wake-up is the agent's own subscription; a request is someone else's ask addressed to it. Both are listed in its `listens_for`. |
| The Supervisor and the agents with `message_agent` / `run_agent` tools brief other agents inside the plugin. | These could become `agent.requested` on the bus, so the briefing is visible as an edge between machines rather than a direct write to the wake-up queue. A smaller first step than the service. |

The boundary to keep: the task system is the record of work that must be accounted for; the
service is a call. If a request starts needing a review, a branch or a retry budget, it should
have been a ticket.

## 5. Open questions for review

1. **Configuration by name or by shape.** Should a request name only roles and agents already
   configured (the owner's configuration stays the one place agents are defined), or may it carry
   a harness, model or tool list? The first keeps permissions where they are; the second turns
   the bus into a way to escalate an agent's tools. Recommendation: names only.
2. **Who may ask.** Any machine on the bus, or only sources listed in the agents' configuration?
   Every request spends money.
3. **Cost and concurrency limits.** A per-role cap on concurrent ad hoc instances, and a refusal
   when it is reached, like the task system's capacity.
4. **Workspace.** A request without a workspace runs in the role's team space. One with a
   workspace runs there, read-write: who checks that the asker may hand it out?
5. **Whether `message_agent` / `run_agent` should move onto the bus first** (section 4, last row),
   as the smallest change that makes the agents' internal briefings visible to Orchestra.

## 6. What it would take

- orchestra-agents: the two event names in `protocol.py`; the host's runner taking
  `agent.requested` (a third kind of offer beside the task system's); `listening.py` adding
  `agent.requested` to every agent that may be asked; the answer published as the agent; a test
  that the host publishes nothing as itself (the existing `test_host_is_no_machine.py` covers it).
- Orchestra: nothing. The contract is the agents', on the one bus; Orchestra reads only the
  envelope and the announcements.
- The task system: nothing.
