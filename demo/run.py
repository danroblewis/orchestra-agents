"""The agents' demo, in one command, from this repository's environment:

    uv run demo/run.py [target] [--port 8072]       # target: a new directory, default ~/orchestra-agents-demo

It copies the template beside it (orchestra.toml, README.md) and the task system's own shell
worker (orchestra-tasking's demo/machines/shell_worker.py, from the checkout the dev extra
installs) to the target, makes it a git repository (Orchestra wants orchestra.toml at a repository
root), removes any state a previous run left for it, starts Orchestra's host and web UI, the task
system's scheduler, the agents' host and the shell worker, creates a milestone and three tickets
as a planner would (requests over the bus, protocol.py), prints the URL and stops everything on
Ctrl-C. The processes' output goes to run/*.log in the target.

The reviewer is an AI agent on Claude Code with Haiku, which costs money: a few cents for the
three reviews. CLAUDE_BINARY names another ``claude`` to run instead; the e2e tests set it to
tests/e2e/fake_claude.py and use the same functions against a temporary directory and a free
port (tests/e2e/test_demo_e2e.py)."""

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
# The tickets are written for the task system's shell worker (its README, "Writing tickets for the
# demo's worker"): the commands run in the ticket's own empty folder, a task about the repository
# goes to $ORCHESTRA_REPO first, and the acceptance names a `file` in that folder or a "text" in
# RESULT.md, which a program (and the fake reviewer) can check.
PLAN = {"milestone": {"name": "Demo", "description": "Three tasks done by a program and reviewed by an AI agent."},
        "tickets": [
            {"title": "Say hello", "objective": "Show a task going through a program worker and an AI reviewer.",
             "description": "Write a greeting into a file and show it.\n\n```sh\nsleep 3\necho 'hello from the agents demo' > hello.txt\ncat hello.txt\n```",
             "acceptance": "- `hello.txt` exists\n- RESULT.md shows \"hello from the agents demo\"", "verification": "The reviewer reads RESULT.md."},
            {"title": "Count the words in the README", "objective": "A second task for the same pair of machines.", "depends_on": [0],
             "description": "The commands run in the ticket's own empty folder; a task about the repository goes there first.\n\n"
                            "```sh\ncd \"$ORCHESTRA_REPO\" && wc -w README.md\n```",
             "acceptance": "- RESULT.md shows \"README.md\"", "verification": "The reviewer reads RESULT.md."},
            {"title": "List the project's files", "objective": "A task with nothing to judge but the listing.",
             "description": "```sh\ncd \"$ORCHESTRA_REPO\" && ls -1\n```", "acceptance": "- RESULT.md shows \"orchestra.toml\"",
             "verification": "The reviewer reads RESULT.md."},
        ]}


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
    """Orchestra's host and web UI, the scheduler, the agents' host and the shell worker, each
    with its log in run/."""
    env = {**os.environ, **(env or {}), "ORCHESTRA_REPO": str(target), "PYTHONUNBUFFERED": "1"}
    (target / "run").mkdir(exist_ok=True)
    commands = {"host": [PYTHON, "-m", "orchestra", "run"], "web": [PYTHON, "-m", "orchestra", "web", "--port", str(port)],
                "scheduler": [PYTHON, "-m", "tasking", "run"], "agents": [PYTHON, "-m", "orchestra_agents", "run"],
                "worker": [PYTHON, str(target / "machines" / "shell_worker.py")]}
    procs = {}
    for name, cmd in commands.items():
        with (target / "run" / f"{name}.log").open("ab") as log:
            procs[name] = subprocess.Popen(cmd, cwd=target, env=env, stdout=log, stderr=subprocess.STDOUT)
    return procs


def stop(procs: dict[str, subprocess.Popen]) -> None:
    for p in procs.values():
        if p.poll() is None:
            p.terminate()
    for p in procs.values():
        try:
            p.wait(timeout=15)
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
    """The web UI answers, the scheduler is up (its desk then hears requests) and the agents'
    host serves the Supervisor."""
    def alive():
        dead = [n for n, p in procs.items() if p.poll() is not None]
        if dead:
            raise SystemExit(f"{', '.join(dead)} exited; see run/*.log")
        return True
    wait_until("the web UI", lambda: alive() and get(f"{url}/api/plugins")["ok"])
    wait_until("the scheduler", lambda: alive() and get(f"{url}/api/plugins/tasks/board")["scheduler"])
    wait_until("the agents' host", lambda: alive() and get(f"{url}/api/plugins/agents/agents")["host"]["alive"])


def plan(target: Path) -> list[str]:
    """A planner's requests to the task system: the milestone and the tickets. Returns the ticket ids."""
    from orchestra_agents.config import load_project
    from orchestra_agents.events import Events
    from orchestra_agents.protocol import TaskClient

    ask = TaskClient(Events.for_project(load_project(target), source="planner"), "planner")

    def request(op: str, args: dict) -> dict:
        out = ask(op, args)
        if not out.get("ok"):
            sys.exit(f"{op} refused: {out.get('error')}")
        return out

    milestone = request("create_milestone", PLAN["milestone"])["milestone"]["id"]
    ids: list[str] = []
    for t in PLAN["tickets"]:
        args = {**t, "milestone_id": milestone, "depends_on": [ids[i] for i in t.get("depends_on", [])]}
        ids.append(request("create_ticket", args)["ticket"]["id"])
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
    signal.signal(signal.SIGTERM, interrupt)          # `kill` stops everything, like Ctrl-C
    signal.signal(signal.SIGINT, interrupt)
    target, url = make_project(a.target.expanduser().resolve()), f"http://127.0.0.1:{a.port}"
    procs = start(target, a.port)
    try:
        wait_for(url, procs)
        ids = plan(target)
        print(f"\nThe demo runs at {url}\n  project:  {target}\n  tickets:  {', '.join(ids)}  ({url}/tickets)\n"
              f"  agents:   {url}/agents\n  machines: {url}/machines\n  logs:     {target / 'run'}/*.log\nCtrl-C stops everything.", flush=True)
        while all(p.poll() is None for p in procs.values()):
            time.sleep(1)
        print("a process exited; see run/*.log", flush=True)
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
    finally:
        stop(procs)


if __name__ == "__main__":
    main()
