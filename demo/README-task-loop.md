# The live demo as the task loop

This file is meant to live at `~/orchestra-live-demo/README-task-loop.md`, beside `restart.sh`.
It says how to switch orchestra.dansproject.space from the agents' ticket demo (`restart.sh`:
`demo/run.py --dummy`, then `tickets.py` and `seed.py`) to the Workflows plugin's task loop, with
no task system: no Tickets entry in the navbar, no TASKS panel.

## What runs

`demo/run_task_loop.py` (in the agents repository, branch `demo-task-loop`):

- makes a fresh project from `demo/task_loop/` (the Workflows plugin's `examples/task_loop/target`
  at fb2b107a, with slower turns: 20 s writing, 8 s reviewing, and `[agents]` on the dummy
  harness serving no floor role), a git repository whose `origin` is a bare repository at
  `<project>/.git/origin.git`, and removes the state a previous run at that path left;
- starts `orchestra run` and `orchestra web` (run.py's `start`); the host starts the floor, the
  program host (which runs the merge gate), the two scripted workers, the scripted reviewer, the
  instruments and the agents' host (the dummy Supervisor answers the Agents page);
- is the coordinator (`coordinator/1`): asks for `--batch` items every `--every` seconds
  (`item.requested`), without end by default, so the piles keep filling and draining for days.
  The first batch shows the script: `task-03` rejected three times (attention to the coordinator
  once), `task-06` failing the gate once; every later item goes straight through.
- refuses to start when the task system is importable (the distribution `orchestra-tasking`, the
  module `tasking`, or the `tasks` plugin entry point).

`demo/seed_task_loop.py` is `seed.py` without what names tickets: the Features stock (drawn near
`git_branch_commits{branch="main"}`; the "tickets with a feature label" option dropped), the
proposed Feature Count Improver reading the floor's hand-offs (`offer.finished`,
`attention.needed`) instead of tickets, and SAIL with its `company_prom` instrument, unchanged. The
Workflows plugin declares its own machines first (d1), so it skips only when Features is declared.

`demo/restart-task-loop.sh` is `restart.sh` with three changes: the agents' worktree is synced
without the task system, every `uv run` is `--no-sync` (a syncing `uv run` would not put
orchestra-tasking back, as it is only in the dev extra, but `--no-sync` makes that certain), and
there is no `tickets.py` step (it still stops a running one, from `tickets.pid`). Stop, the core
worktree's sync, `rm -rf` of the project, the start, the seed and `LOGIN.txt` (the token never
printed, the file mode 600) are as before. `BATCH` (5) and `EVERY` (300) set the pace; `AG`
(`~/orchestra-agents-wt-demo`) the agents' worktree.

## The environment without the task system

The agents' dev extra installs orchestra-tasking with workflows and bridges. In the demo's
worktree:

```bash
cd ~/orchestra-agents-wt-demo
uv sync --extra dev --no-install-package orchestra-tasking   # exact: removes it if it was installed
uv run --no-sync python -c "import tasking"                  # must fail: ModuleNotFoundError
```

An environment already synced with it can instead drop it in place
(`uv pip uninstall orchestra-tasking`), but the next plain `uv sync --extra dev` puts it back;
`--no-install-package` is the step to keep. Run everything after it with `uv run --no-sync`.

## Switching the live demo over (the coordinator)

`demo-live` is main plus one demo commit, so it takes this branch by a merge (or by a rebase
onto main once `demo-task-loop` is merged there):

```bash
git -C ~/orchestra-agents-wt-demo merge --no-edit demo-task-loop   # demo-live now holds demo/run_task_loop.py
cp ~/orchestra-agents-wt-demo/demo/restart-task-loop.sh ~/orchestra-agents-wt-demo/demo/README-task-loop.md ~/orchestra-live-demo/
~/orchestra-live-demo/restart-task-loop.sh                          # stops the ticket demo, starts the task loop on :8096
```

To go back, run `restart.sh`: its `uv sync --extra dev` reinstalls the task system.

The pages to check: `/machines` (coordinator, worker ×2, reviewer, merge_gate and the `offer.*`
hand-offs), `/items`, `/dynamics` (a model after the first `[discovery] every = "5m"`), `/agents`.
