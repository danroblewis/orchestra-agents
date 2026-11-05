# Skill: cannot-do

Use when: a ticket is BLOCKED or rejected twice for one reason, a request is not met (a measurement missing, a program not running, work that never lands), or before work of a new kind.

## When the system cannot do what is asked

Whenever a ticket is BLOCKED, a ticket is rejected twice for the same reason (by a reviewer or by
the gate), a request of the human's is not met (a measurement missing from `list_metrics`, a
program not running, work that never lands), or before you file work of a kind this project has
not done yet, first check whether the system can do it at all:

1. **Who fills the worker role?** A program does only what it was written for (a shell worker runs
   the commands a ticket gives, and nothing else); only an AI agent can write code or a script
   from an outcome. If the work needs an AI worker and no AI agent fills the role, make the AI
   agents fill it: `set_agent_roles`. A program seen filling a role beside the AI agents takes
   tickets it was not written for and blocks them (a shell worker reports BLOCKED "the ticket
   gives no commands"): give it a filter in its `[[machines]]` command (`set_machine`; the shell
   worker's is `--only-commands`), or take it out of the role. If it is not a `[[machines]]`
   entry (`check_setup` lists it under `not_launched`), you cannot change it: tell the human
   which process to stop, then unblock the tickets it blocked (`unblock_ticket`).
2. **Must the result end up in the repository** (a collector, a program, a fix to either)? Only
   `work = "code"` lands anything: each ticket gets a branch, a review of its diff and the merge
   gate. With `tasks` nothing a worker makes is kept. Set it with `set_work_mode`.
3. **Can a branch land?** In code work, the gate runs each `[[gate.unit]]` test on the branch. A
   test that cannot pass blocks every landing; one that is missing lands untested work. Fix it
   with `set_gate_unit`. Never set a gate test you have not seen pass on the base branch: a
   command that finds no tests (`pytest` in a repository without tests) fails, and blocks every
   landing. When you do not know which test a branch must pass, ask the human; do not guess one.
4. **Is what is needed running?** `list_processes`. A program Orchestra gave up on, or a collector
   whose rounds fail: read its log. A cause in its entry (a wrong command or path, a timeout too
   short) is yours: `set_machine` or `set_collector`. A cause in its code is a bug for a worker:
   [[tasking]]hand it to the Coordinator[[/tasking]][[no-tasking]]code work is not handed on here; say so[[/no-tasking]]. Once the cause is gone, `restart_machine` starts a program again, and `restart_process` any process
   the launcher stopped retrying (a plugin's too): never before the cause is fixed. A workflow item
   whose step failed (blocked or error: in retry, or `stalled`) is offered again now with
   `retry_step` once you fixed its cause; the reason, and your reply, say what you fixed.
5. **Is anything offered at all?** A capacity limit of 0 offers nothing: `set_capacity_limit`.

Fix the setup with your tools, then tell the human in your reply what you changed, why, and the
commit each tool answered with. Then carry on with the work: unblock a ticket that failed for a
reason now fixed (`unblock_ticket`), or hand it on again. Never keep handing on work the system
cannot complete: a ticket that failed because of the setup fails the same way until the setup
changes.
