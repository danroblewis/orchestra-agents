"""The live demo's task loop, in one command, from this repository's environment without the task system:

    uv run --no-sync demo/run_task_loop.py [target] [--port 8072] [--dummy] [--batch 5] [--every 300] [--items 0]

The Workflows plugin's first example (write → review → gate on scripted machines: two workers, a
reviewer and the merge gate; examples/task_loop/ in orchestra-workflows, its target copied to
task_loop/ beside this file) run against a fresh project the way run.py makes one, with the agents'
dummy Supervisor answering the Agents page, and no task system: it refuses to start when the task
system (orchestra-tasking, module ``tasking``) is installed, since its plugin would add the Tickets
page and the TASKS panel (README-task-loop.md says how to sync an environment without it).

It copies task_loop/ to the target, makes it a git repository whose ``origin`` is a bare repository
inside the target's .git (the merge gate pushes each landed item there), writes the workflow
through the plugin's validated writer, removes any state a previous run left for the target, and
starts Orchestra's host and web UI (run.py's ``start``). Then it is the coordinator
(``coordinator/1``): it asks for ``--batch`` items every ``--every`` seconds, for ``--items`` items
in all (0: until stopped), as ``item.requested``, so the piles keep filling and draining for days.
Ctrl-C (or SIGTERM) stops everything."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

try:
    from demo import run as base
except ImportError:  # run as a script: this folder is sys.path[0]
    import run as base

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "task_loop"
WORKFLOW = "task_loop"
COORDINATOR = "coordinator/1"
MACHINES = ("worker/1", "worker/2", "reviewer/1", "merge_gate/1")
READY = "The work:"        # the line restart-task-loop.sh waits for
CONTEXT = """# Project context

_Maintained by the Supervisor. Every Supervisor turn starts with this document._

## Objectives
1. Show the Workflows plugin's task loop: pieces of work written, reviewed and landed on main
   through a merge gate, by scripted machines (two workers, a reviewer, the gate), handed over on
   the floor by `offer.*` events.
2. Measure the work: the piles of each step and the commits on main, for the Dynamics page.

## Non-goals
- No product: each item adds one file under `work/`, and exists to be measured.
- No task system: work arrives as items (`item.requested` from the coordinator), never tickets.

## Definition of done
An item is done when the merge gate has landed its branch on main (`sh test.sh` passes).
"""


def tasking_present() -> str | None:
    """Why the task system is in this environment, or None: its distribution, its module, or its
    plugin entry point (``tasks``). Any of them puts the Tickets page and the TASKS panel back."""
    try:
        return f"orchestra-tasking {importlib.metadata.version('orchestra-tasking')} is installed"
    except importlib.metadata.PackageNotFoundError:
        pass
    for mod in ("tasking", "orchestra_tasking"):
        try:
            if importlib.util.find_spec(mod) is not None:
                return f"the module {mod} is importable"
        except (ImportError, ValueError):
            pass
    for ep in importlib.metadata.entry_points(group="orchestra.plugins"):
        if ep.name == "tasks" or ep.value.split(".")[0] in ("tasking", "orchestra_tasking"):
            return f"the plugin {ep.name} ({ep.value}) is installed"
    return None


def refuse_tasking() -> None:
    why = tasking_present()
    if why:
        sys.exit(f"the task loop runs without the task system, but {why}: sync this environment without it "
                 "(uv sync --extra dev --no-install-package orchestra-tasking, then uv run --no-sync; "
                 "demo/README-task-loop.md)")


def args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", nargs="?", default=Path.home() / "orchestra-task-loop-demo", type=Path)
    ap.add_argument("--port", type=int, default=8072)
    ap.add_argument("--dummy", action="store_true",
                    help="accepted for run.py's command line; the task loop's agents always run on the dummy harness")
    ap.add_argument("--batch", type=int, default=5, help="items asked for at once (5)")
    ap.add_argument("--every", type=float, default=300.0, help="seconds between batches (300)")
    ap.add_argument("--items", type=int, default=0, help="items in all (0: until stopped)")
    a = ap.parse_args(argv)
    if a.batch < 1:
        ap.error("--batch is at least 1")
    if a.every <= 0:
        ap.error("--every is a positive number of seconds")
    if a.items < 0:
        ap.error("--items is 0 (until stopped) or more")
    if not 1 <= a.port <= 65535:
        ap.error("--port is a TCP port")
    a.target = a.target.expanduser().resolve()
    return a


def git(*cmd: str, cwd: Path) -> None:
    subprocess.run(["git", "-c", "user.name=demo", "-c", "user.email=demo@example.invalid", *cmd],
                   cwd=cwd, check=True, capture_output=True)


def make_project(target: Path) -> Path:
    """task_loop/ as a fresh git repository at ``target`` (which must not exist yet), with the
    project context for the Supervisor, the workflow written through the plugin's writer, one
    commit on ``main`` pushed to ``origin`` (``.git/origin.git``, so removing the target removes
    it), and no state left from a previous run at the same path."""
    from orchestra.config import load_project
    from orchestra_workflows.define import write

    if target.exists():
        sys.exit(f"{target} exists; remove it or name another directory")
    shutil.copytree(TEMPLATE, target, ignore=shutil.ignore_patterns("workflow.json", "__pycache__"))
    for sh in [*target.glob("*.sh"), *target.glob(".orchestra/**/*.sh")]:
        sh.chmod(0o755)
    (target / ".orchestra" / "PROJECT.md").write_text(CONTEXT, encoding="utf-8")
    git("init", "-q", "-b", "main", cwd=target)
    project = load_project(target)
    write(project, json.loads((TEMPLATE / ".orchestra" / "workflows" / WORKFLOW / "workflow.json").read_text()), root="repository")
    git("add", "-A", cwd=target)
    git("commit", "-q", "-m", "the task loop's demo", cwd=target)
    origin = target / ".git" / "origin.git"
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=target)
    git("remote", "add", "origin", str(origin), cwd=target)
    git("push", "-q", "origin", "main", cwd=target)
    git("fetch", "-q", "origin", cwd=target)
    state = project.state_dir
    left = any(p.is_file() and p.name != "project.json" for p in state.rglob("*"))
    shutil.rmtree(state)
    if left:
        print(f"removed the state a previous run left in {state}", flush=True)
    return target


def item(n: int) -> str:
    return f"task-{n:02d}"


class Coordinator:
    """The planner of the workflow: asks for the work, ``batch`` items at a time."""

    def __init__(self, target: Path):
        from orchestra.config import load_project
        from orchestra.events import Events

        self.events = Events.for_project(load_project(target), source=COORDINATOR)
        self.asked = 0

    def announce(self) -> None:
        self.events.publish("orchestra.machine.started", {"mode": "scheduled", "kind": "program", "listens_for": ["attention.needed"]})

    def ask(self, count: int) -> list[str]:
        out = []
        for _ in range(count):
            self.asked += 1
            name = item(self.asked)
            self.events.publish("item.requested", {"workflow": WORKFLOW, "brief": f"add work/{name}.txt"}, subject=name)
            out.append(name)
        return out


def batches(batch: int, items: int):
    """The size of each batch to ask for: ``batch`` each, ``items`` in all (0: without end)."""
    left = items
    while items == 0 or left > 0:
        n = batch if items == 0 else min(batch, left)
        left -= n
        yield n


def announced(target: Path) -> set[str]:
    from orchestra.config import load_project
    from orchestra.events import Events

    evs, _ = Events.for_project(load_project(target)).after(0)
    return {e.get("source") for e in evs if e.get("type") == "orchestra.machine.started"}


def wait_for(web: base.Web, procs, target: Path) -> None:
    """The web UI answers, the agents' host is up (it serves the Supervisor) and the scripted
    machines have announced themselves."""
    def alive():
        dead = [n for n, p in procs.items() if p.poll() is not None]
        if dead:
            raise SystemExit(f"{', '.join(dead)} exited; see run/*.log")
        return True
    base.wait_until("the web UI", lambda: alive() and web.get("/api/plugins")["ok"])
    base.wait_until("the agents' host", lambda: alive() and web.get("/api/plugins/agents/agents")["host"]["alive"], 90)
    base.wait_until("the scripted machines", lambda: alive() and set(MACHINES) <= announced(target), 120)


def main(argv: list[str] | None = None) -> None:
    a = args(argv)
    refuse_tasking()
    signal.signal(signal.SIGTERM, base.interrupt)
    signal.signal(signal.SIGINT, base.interrupt)
    target, url = make_project(a.target), f"http://127.0.0.1:{a.port}"
    procs = base.start(target, a.port)
    running = lambda: all(p.poll() is None for p in procs.values())
    try:
        web = base.Web(url, base.web_token(target))
        wait_for(web, procs, target)
        coordinator = Coordinator(target)
        coordinator.announce()
        print(f"\nThe task loop runs at {url} (log in with the link `orchestra web token` makes; never printed here)\n"
              f"  project:  {target}\n  machines: {url}/machines\n  items:    {url}/items\n  dynamics: {url}/dynamics\n"
              f"  agents:   {url}/agents (the dummy Supervisor)\n  logs:     {target / 'run'}/*.log, "
              f"{base.load_orchestra_project(target).logs_dir / 'machines'}/*.log\n"
              f"Ctrl-C stops everything.\n{READY} {a.batch} items every {a.every:g} s, "
              f"{a.items or 'without end'}{' in all' if a.items else ''}", flush=True)
        for n in batches(a.batch, a.items):
            print(f"  asked for {', '.join(coordinator.ask(n))}", flush=True)
            end = time.time() + a.every
            while time.time() < end:
                if not running():
                    raise SystemExit("a process exited; see run/*.log")
                time.sleep(1)
        while running():
            time.sleep(1)
        print("a process exited; see run/*.log", flush=True)
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
    finally:
        stop_all(procs)


def stop_all(procs) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):   # a second signal never cuts the stop short
        signal.signal(sig, signal.SIG_IGN)
    base.stop(procs)


if __name__ == "__main__":
    main()
