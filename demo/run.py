"""The agents' demo, in one command, from this repository's environment:

    uv run demo/run.py [target] [--port 8072] [--tickets 12] [--every 90]
    # target: a new directory, default ~/orchestra-agents-demo

It copies the template beside it (orchestra.toml, README.md) and the task system's own shell
worker (orchestra-tasking's demo/machines/shell_worker.py, from the checkout the dev extra
installs) to the target, makes it a git repository (Orchestra wants orchestra.toml at a repository
root), removes any state a previous run left for it, starts Orchestra's host and web UI, the task
system's scheduler, the agents' host and the shell worker, creates a milestone and its tickets as
a planner would (requests over the bus, protocol.py), one every ``--every`` seconds, prints the
URL and stops everything on Ctrl-C. The processes' output goes to run/*.log in the target.

Twelve tickets ninety seconds apart keep the queue moving for a quarter of an hour: long enough
for Orchestra to discover a model from the metrics (README.md, "Dynamics"). ``--tickets 3`` is a
two-minute look at the machines; more than twelve repeats the list.

The reviewer is an AI agent on Claude Code with Haiku, which costs money: about ten cents for
the twelve reviews and the planners' turns. CLAUDE_BINARY names another ``claude`` to
run instead; the e2e tests set it to tests/e2e/fake_claude.py and use the same functions against
a temporary directory and a free port (tests/e2e/test_demo_e2e.py)."""

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
PLAN = {"milestone": {"name": "Demo", "description": "Small tasks done by a program and reviewed by an AI agent."},
        "tickets": [
            {"title": "Say hello", "objective": "Show a task going through a program worker and an AI reviewer.",
             "description": "Write a greeting into a file and show it.\n\n```sh\nsleep 3\necho 'hello from the agents demo' > hello.txt\ncat hello.txt\n```",
             "acceptance": "- `hello.txt` exists\n- RESULT.md shows \"hello from the agents demo\""},
            {"title": "Count the words in the README", "objective": "A second task for the same pair of machines.", "depends_on": [0],
             "description": "The commands run in the ticket's own empty folder; a task about the repository goes there first.\n\n"
                            "```sh\ncd \"$ORCHESTRA_REPO\" && wc -w README.md\n```",
             "acceptance": "- RESULT.md shows \"README.md\""},
            {"title": "List the project's files", "objective": "A task with nothing to judge but the listing.",
             "description": "```sh\ncd \"$ORCHESTRA_REPO\" && ls -1\n```", "acceptance": "- RESULT.md shows \"orchestra.toml\""},
            {"title": "Record the SHA-256 of the README",
             "objective": "A task the reviewer rejects the first time: the command computes the wrong digest. The worker cannot act "
                          "on the feedback, so it is blocked until a planner changes the command.",
             "description": "Compute the digest of the project's README.\n\n```sh\ncd \"$ORCHESTRA_REPO\" && cksum README.md\n```",
             "acceptance": "- RESULT.md shows a SHA-256 digest of README.md: 64 hex characters, labelled SHA-256\n"
                           "- a CRC or an MD5 digest does not satisfy this"},
            {"title": "Describe the event bus in prose",
             "objective": "A task this worker cannot do: it has no commands, and a program cannot write prose. It is reported BLOCKED "
                          "and waits for a decision.",
             "description": "Write two sentences in RESULT.md about what Orchestra's event bus is for. There are no commands to run.",
             "acceptance": "- RESULT.md holds two sentences about the event bus"},
            {"title": "Add the numbers one to ten", "objective": "A small shell task with one right answer.",
             "description": "```sh\nseq 1 10 | paste -sd+ - | bc\n```", "acceptance": "- RESULT.md shows 55"},
            {"title": "Make a folder with two files and list it", "objective": "A small shell task with a file-system effect.",
             "description": "```sh\nmkdir -p out && touch out/a.txt out/b.txt && ls out\n```", "acceptance": "- `out/a.txt` exists\n- RESULT.md shows \"b.txt\""},
            {"title": "Show the last commit", "objective": "A small shell task about the repository.",
             "description": "```sh\ncd \"$ORCHESTRA_REPO\" && git log --oneline -1\n```", "acceptance": "- RESULT.md shows \"the agents' demo\""},
            {"title": "Name the operating system", "objective": "A small shell task.",
             "description": "```sh\nuname -s\n```", "acceptance": "- RESULT.md shows the kernel's name (Darwin or Linux)"},
            {"title": "Write today's date", "objective": "A small shell task that leaves a file.",
             "description": "```sh\ndate +%F > today.txt && cat today.txt\n```", "acceptance": "- `today.txt` exists\n- RESULT.md shows a date of the form YYYY-MM-DD"},
            {"title": "Count the lines in orchestra.toml", "objective": "A small shell task about the repository.",
             "description": "```sh\ncd \"$ORCHESTRA_REPO\" && wc -l orchestra.toml\n```", "acceptance": "- RESULT.md shows a number followed by \"orchestra.toml\""},
            {"title": "Sort three words", "objective": "A small shell task with one right order.",
             "description": "```sh\nprintf 'pear\\napple\\nfig\\n' | sort\n```", "acceptance": "- RESULT.md shows apple, fig and pear in that order"},
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


def plan(target: Path, count: int = len(PLAN["tickets"]), every_s: float = 0.0, go=lambda: True) -> list[str]:
    """A planner's requests to the task system: the milestone, then ``count`` tickets from PLAN
    (repeated past its end), one every ``every_s`` seconds while ``go()``. Returns the ticket ids."""
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
    for i in range(count):
        if i and not pause(every_s, go):
            break
        t = PLAN["tickets"][i % len(PLAN["tickets"])]
        args = {**t, "milestone_id": milestone, "verification": "The reviewer reads RESULT.md.",
                "depends_on": [ids[j] for j in t.get("depends_on", [])]}
        ids.append(request("create_ticket", args)["ticket"]["id"])
        print(f"  {time.strftime('%H:%M:%S')}  {ids[-1]}  {t['title']}", flush=True)
    return ids


def pause(seconds: float, go) -> bool:
    """Waits ``seconds``, a second at a time, while ``go()``; False if it stopped early."""
    end = time.time() + seconds
    while time.time() < end:
        if not go():
            return False
        time.sleep(1)
    return True


def interrupt(*_) -> None:
    """A signal stops everything, once: a second one while stopping changes nothing."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise KeyboardInterrupt


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", nargs="?", default=Path.home() / "orchestra-agents-demo", type=Path)
    ap.add_argument("--port", type=int, default=8072)
    ap.add_argument("--tickets", type=int, default=len(PLAN["tickets"]), help="how many tickets to create (default %(default)s)")
    ap.add_argument("--every", type=float, default=90.0, help="seconds from one ticket to the next (default %(default)s)")
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, interrupt)          # `kill` stops everything, like Ctrl-C
    signal.signal(signal.SIGINT, interrupt)
    target, url = make_project(a.target.expanduser().resolve()), f"http://127.0.0.1:{a.port}"
    procs = start(target, a.port)
    running = lambda: all(p.poll() is None for p in procs.values())
    try:
        wait_for(url, procs)
        print(f"\nThe demo runs at {url}\n  project:  {target}\n  tickets:  {url}/tickets\n  agents:   {url}/agents\n"
              f"  machines: {url}/machines\n  logs:     {target / 'run'}/*.log\nCtrl-C stops everything.\n"
              f"{a.tickets} tickets, one every {a.every:g} s:", flush=True)
        plan(target, a.tickets, a.every, go=running)
        while running():
            time.sleep(1)
        print("a process exited; see run/*.log", flush=True)
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
    finally:
        stop(procs)


if __name__ == "__main__":
    main()
