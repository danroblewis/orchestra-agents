# Skill: gave-up

Use when: a program Orchestra gave up on (`orchestra.process.gave_up`), a process to restart, or a workflow step to retry.

## A program Orchestra gave up on

You are woken by `orchestra.process.gave_up` when a program Orchestra's launcher runs (one of the
project's `[[machines]]`, or a plugin's) failed several times in a row, each soon after it started,
and is no longer restarted. The wake-up names the process, its last exit code and its log file
(`read_machine_log` with its name). Read the end of that log to see why it failed: a missing
package, an import error, a request that fails, a typo. A cause in its `[[machines]]` entry (a
wrong command or path) is yours: fix it with `set_machine`, which makes Orchestra try it again. A
cause in the program is a **bug** for a worker: [[tasking]]hand it to the Coordinator (`hand_to_coordinator`)
with the outcome[[/tasking]][[no-tasking]]in one of Orchestra's own machines (`.orchestra/machines/`, the state directory's `machines/`) yours, with `edit_machine_file`; in the project's code [[/no-tasking]][[workflows]]a worker's, as a Workflows item on a branch (skill `code-work`): its brief gives[[/workflows]][[no-workflows]]not handed on here (no task system or Workflows plugin: say so, and that installing Workflows is the one step), so say what the owner's fix must do: give[[/no-workflows]] the outcome (the program running and writing again), the error quoted from the log and the
log's name, and the acceptance (the error is gone; the program runs once with `--once` without it,
when it has that flag). A merged change to the program or its entry makes Orchestra try it again.
A DONE ticket is never reopened: the fix is always new work, naming the ticket that added the
program. When the cause was
outside the program and is gone now (a service it needs is back), `restart_machine` starts it
again. A process the launcher stopped retrying (a program, or a plugin's such as the Outbox's sender,
`outbox.sender`) is restarted with `restart_process` and its name **after its cause is fixed, never
before**: read its log, fix what made it crash (a setting you or anyone changed, a service it needs),
then restart it with the reason saying the cause and the fix. Restarted before the fix, it only
crashes again and is stopped again. The tool records the request; Orchestra's launcher carries it
out at its next tick, so check with `list_processes` that it is running. Never run the program yourself, in your session or anywhere.
A workflow's step whose turn finished blocked or error is tried again by the floor after 1, 5, 15
minutes, then hourly, and moved to `stalled` when its tries are spent. Once you fixed what failed it
(a refused push, a missing credential, a service down), `retry_step` with the item and the step asks
the floor to offer it again now, **only after the cause is fixed, never before**: the reason says what
failed and what you fixed, and your reply tells the owner what you fixed. Retried before the fix, it
only fails again and spends a try. When you cannot fix the cause, say what it is and what fixes it.
