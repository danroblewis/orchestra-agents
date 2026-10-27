"""The agents' demo, in one command, from this repository's environment:

    uv run demo/run.py [target] [--port 8072]
    # target: a new directory, default ~/orchestra-agents-demo

It copies the template beside it (orchestra.toml, README.md, .gitignore), a project context for
the Supervisor (CONTEXT, as .orchestra/PROJECT.md) and the task system's own shell worker (orchestra-tasking's demo/machines/shell_worker.py, from the checkout the dev
extra installs) to the target, makes it a git repository (Orchestra wants orchestra.toml at a
repository root; the task system lands each ticket's branch on its main), removes any state a
previous run left for it, and starts Orchestra: its host (`orchestra run`) and its web UI. The
host starts the rest: the task system's scheduler and the agents' host (each plugin declares
its own) and the shell worker (`[[machines]]` in orchestra.toml). Then it creates a milestone
and its tickets as a planner would (requests over the bus, protocol.py), prints the URL and
stops everything on Ctrl-C. Orchestra's output goes to run/*.log in the target; what the host
starts logs to ~/.orchestrator/<key>/logs/machines/.

The tickets show the two kinds of worker on one queue. Two are shell commands, which the
program takes (it answers only tickets that give `sh` blocks) and an AI worker could do as
well; two are small code changes only an AI worker can make, one of them a machine: a program
that measures something into the project's metrics store, declared in `[[machines]]` so that
Orchestra starts it once its branch lands. Every ticket is a branch reviewed by an AI reviewer
and landed on main through the merge gate.

The AI agents run on Claude Code with Haiku, which costs money (README.md). The demo checks
that ``claude`` (or CLAUDE_BINARY) is on the PATH before it starts anything. CLAUDE_BINARY names
another ``claude`` to run instead; the e2e tests set it to tests/e2e/fake_claude.py and use the
same functions against a temporary directory and a free port (tests/e2e/test_demo_e2e.py)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from orchestra.config import home, project_key

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable
STOP_WAIT_S = 60.0     # longer than the host's worst-case shutdown: the longest stop grace it waits out, and its own stop
# A command ticket is written for the task system's shell worker (its README, "Writing tickets for
# the demo's worker"): `sh` blocks run in the ticket's branch, and the acceptance names a `file`
# they leave, which a program (and the fake reviewer) can check; `sleep 10` stands for work, so
# the ticket is seen IN_PROGRESS across two 5 s discovery steps. A code ticket says what is wanted
# and how it will be judged, never how to write it: an AI worker decides that.
WORK = "sleep 10"
PLAN = {"milestone": {"name": "Demo", "description": "Two kinds of worker on one queue: a program for commands, an AI agent for code."},
        "tickets": [
            {"title": "Record the toolchain", "objective": "A command ticket: the program worker takes it.",
             "description": "Note which git and which operating system the project is built with.\n\n"
                            f"```sh\n{WORK}\nmkdir -p notes\n{{ git --version; uname -s; }} > notes/toolchain.txt\ncat notes/toolchain.txt\n```",
             "acceptance": "- `notes/toolchain.txt` exists, naming the git version and the kernel"},
            {"title": "Record the day the project started", "objective": "A second command ticket.",
             "description": f"```sh\n{WORK}\nmkdir -p notes\ndate +%F > notes/started.txt\n```",
             "acceptance": "- `notes/started.txt` exists and holds a date of the form YYYY-MM-DD"},
            {"title": "Count the lines of the Python files",
             "objective": "A code ticket: something only an AI worker can do, landed through the merge gate.",
             "description": "Add a small command-line tool that prints, for each Python file git tracks in the repository, its "
                            "path and how many lines it has, longest first. Standard library only. Add a test for it under "
                            "tests/ that the merge gate runs with pytest.",
             "acceptance": "- `tools/line_counts.py` exists and runs with python, from the repository root\n"
                           "- `tests/test_line_counts.py` exists and passes\n- nothing outside tools/ and tests/ changes"},
            {"title": "Measure the repository", "objective": "A machine: a program Orchestra keeps running that writes the project's metrics.",
             "description": "Add a machine that measures the repository every 30 seconds: how many files git tracks and how many "
                            "commits main has. Its series land in this project's metrics store, so they appear on the Core page and "
                            "discovery is offered them, and Orchestra starts it and keeps it running once it is merged. Follow "
                            "docs/machines.md in Orchestra's repository.",
             "acceptance": "- `machines/repo_stats.py` exists\n"
                           "- `orchestra.toml` declares it under [[machines]] with restart always\n"
                           "- it writes through orchestra.metrics.for_repo, with plain series names that do not start with orchestra_ "
                           "and a machine label\n- it announces itself with orchestra.machine.started"},
        ]}


# The project context the Supervisor starts from (orchestra_agents.prompting.read_context seeds
# the agents' copy from .orchestra/PROJECT.md in the repository): without one, a Supervisor would
# first ask the human what the project is for.
CONTEXT = """# Project context

_Maintained by the Supervisor. Every Supervisor turn starts with this document._

## Objectives
1. Show two kinds of worker on one queue: a program that runs shell commands, and AI agents that
   write code; every ticket a branch, reviewed and landed on main through the merge gate.
2. Measure the project and its work: the task system's and the agents' own series, and programs
   of this repository (`machines/`, declared under `[[machines]]`) that measure whatever the owner
   asks for, into this project's metrics store, for the Dynamics page.

## Value propositions
- The owner sees how work flows through the system and what it costs, from the metrics, without
  reading logs.
- A new measurement is one ticket: written, reviewed, landed, then started by `orchestra run`.

## Non-goals
- No product: what the tickets build is small and exists to be measured.
- No servers or stores of its own: series go to the project's store (`orchestra.metrics.for_repo`).

## Definition of done
A ticket is DONE when its review is approved and its branch has landed on main through the merge
gate.

## Verification
The merge gate compiles every Python file and runs `pytest` on `tests/` once there are tests; a
new tool or machine comes with a test there.
"""


def make_project(target: Path) -> Path:
    """The template as a fresh git repository at ``target`` (which must not exist yet), with no
    state: Orchestra names a project's state directory after the target's path, so a previous
    run of the demo at the same path left tickets, sessions and events there that would show up
    again."""
    if target.exists():
        sys.exit(f"{target} exists; remove it or name another directory")
    shutil.copytree(HERE, target, ignore=shutil.ignore_patterns("run.py", "__pycache__"))
    (target / "machines").mkdir()
    shutil.copy(shell_worker(), target / "machines" / "shell_worker.py")
    (target / ".orchestra").mkdir()
    (target / ".orchestra" / "PROJECT.md").write_text(CONTEXT, encoding="utf-8")
    git = ["git", "-c", "user.name=demo", "-c", "user.email=demo@example.invalid"]
    subprocess.run([*git, "init", "-q", "-b", "main"], cwd=target, check=True)
    subprocess.run([*git, "add", "-A"], cwd=target, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "the agents' demo"], cwd=target, check=True)
    state = home() / project_key(target)
    if state.exists():
        shutil.rmtree(state)
        print(f"removed the state a previous run left in {state}", flush=True)
    return target


def shell_worker() -> Path:
    """The task system's own program worker, from its checkout: the wheel does not ship the demo."""
    import tasking
    src = Path(tasking.__file__).resolve().parents[1] / "demo" / "machines" / "shell_worker.py"
    if not src.exists():
        sys.exit(f"the demo needs the orchestra-tasking checkout (uv sync --extra dev): no shell worker at {src}")
    return src


def start(target: Path, port: int, env: dict[str, str] | None = None) -> dict[str, subprocess.Popen]:
    """Orchestra's host and web UI, each with its log in run/. The host starts what the plugins and
    orchestra.toml declare. The interpreter's directory goes first on the PATH, so `python` in a
    declared command, a gate command or an agent's shell is the one Orchestra and the plugins are
    installed in."""
    env = {**os.environ, **(env or {}), "ORCHESTRA_REPO": str(target), "PYTHONUNBUFFERED": "1",
           "PATH": os.pathsep.join([str(Path(PYTHON).parent), os.environ.get("PATH", "")])}
    (target / "run").mkdir(exist_ok=True)
    commands = {"host": [PYTHON, "-m", "orchestra", "run"], "web": [PYTHON, "-m", "orchestra", "web", "--port", str(port)]}
    procs = {}
    for name, cmd in commands.items():
        with (target / "run" / f"{name}.log").open("ab") as log:
            procs[name] = subprocess.Popen(cmd, cwd=target, env=env, stdout=log, stderr=subprocess.STDOUT)
    return procs


def stop(procs: dict[str, subprocess.Popen]) -> None:
    """SIGTERM to Orchestra's host and web UI, then SIGKILL to what is still up after STOP_WAIT_S.
    The host stops what it launched first, waiting up to each process's stop grace; killed before
    that, it would leave them running."""
    for p in procs.values():
        if p.poll() is None:
            p.terminate()
    deadline = time.time() + STOP_WAIT_S
    for p in procs.values():
        try:
            p.wait(timeout=max(0.0, deadline - time.time()))
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def wait_until(what: str, check, timeout_s: float = 60.0) -> None:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            up = check()
        except Exception:  # noqa: BLE001 - not up yet
            up = False
        if up:
            return
        time.sleep(0.5)
    raise TimeoutError(f"{what} did not come up within {timeout_s:.0f} s")


def wait_for(url: str, procs: dict[str, subprocess.Popen]) -> None:
    """The web UI answers, and the processes the host started are up: the scheduler (its desk then
    hears requests) and the agents' host (it serves the Supervisor)."""
    def alive():
        dead = [n for n, p in procs.items() if p.poll() is not None]
        if dead:
            raise SystemExit(f"{', '.join(dead)} exited; see run/*.log")
        return True
    wait_until("the web UI", lambda: alive() and get(f"{url}/api/plugins")["ok"])
    wait_until("the scheduler", lambda: alive() and get(f"{url}/api/plugins/tasks/board")["scheduler"])
    wait_until("the agents' host", lambda: alive() and get(f"{url}/api/plugins/agents/agents")["host"]["alive"])


def plan(target: Path, count: int = len(PLAN["tickets"])) -> list[str]:
    """A planner's requests to the task system: the milestone, then the first ``count`` tickets of
    PLAN, all at once. Returns the ticket ids."""
    from orchestra_agents.config import load_project
    from orchestra_agents.events import Events
    from orchestra_agents.protocol import TaskClient

    events = Events.for_project(load_project(target), source="planner")
    # the planner is a machine of the demo too: it says what it is before its first request
    events.publish("orchestra.machine.started", {"mode": "ad hoc", "kind": "program", "listens_for": ["request.done", "request.refused"]})
    ask = TaskClient(events, "planner")

    def request(op: str, args: dict) -> dict:
        out = ask(op, args)
        if not out.get("ok"):
            sys.exit(f"{op} refused: {out.get('error')}")
        return out

    milestone = request("create_milestone", PLAN["milestone"])["milestone"]["id"]
    ids: list[str] = []
    for t in PLAN["tickets"][:count]:
        args = {**t, "milestone_id": milestone, "verification": "The reviewer reads the diff; the merge gate compiles and tests it."}
        ids.append(request("create_ticket", args)["ticket"]["id"])
        print(f"  {ids[-1]}  {t['title']}", flush=True)
    return ids


def interrupt(*_) -> None:
    """A signal stops everything, once: a second one while stopping changes nothing."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise KeyboardInterrupt


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", nargs="?", default=Path.home() / "orchestra-agents-demo", type=Path)
    ap.add_argument("--port", type=int, default=8072)
    a = ap.parse_args()
    binary = os.environ.get("CLAUDE_BINARY", "claude")
    if not shutil.which(binary):
        sys.exit(f"{binary} not found: install Claude Code and sign in, set CLAUDE_BINARY to another `claude`, "
                 "or use tests/e2e/fake_claude.py (README.md)")
    signal.signal(signal.SIGTERM, interrupt)          # `kill` stops everything, like Ctrl-C
    signal.signal(signal.SIGINT, interrupt)
    target, url = make_project(a.target.expanduser().resolve()), f"http://127.0.0.1:{a.port}"
    procs = start(target, a.port)
    running = lambda: all(p.poll() is None for p in procs.values())
    try:
        wait_for(url, procs)
        print(f"\nThe demo runs at {url}\n  project:  {target}\n  tickets:  {url}/tickets\n  agents:   {url}/agents\n"
              f"  machines: {url}/machines\n  core:     {url}/core (what orchestra run keeps running, with their logs)\n"
              f"  logs:     {target / 'run'}/*.log, {home() / project_key(target) / 'logs' / 'machines'}/*.log\n"
              "Ctrl-C stops everything.\nThe tickets:", flush=True)
        plan(target)
        while running():
            time.sleep(1)
        print("a process exited; see run/*.log", flush=True)
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
    finally:
        stop(procs)


if __name__ == "__main__":
    main()
