"""The Workflows plugin's live delivery example (orchestra-workflows ``examples/delivery_live``, what
its ``demo.sh`` runs) with the Supervisor beside it, for the live design test's second scenario
(``test_design_live_delivery.py``, goal task L3).

Run as a program in this repository's environment (orchestra-workflows and the bridges' kits
installed into it, so the host loads the agents and the Workflows plugins together):

    python tests/live/delivery_live_run.py --workflows ~/orchestra-workflows --model haiku --via mcp \\
        -- --tickets 2 --port 8301 --dir RUN [--dry-run]

Everything after ``--`` is ``demo.py``'s own. Two things are added to the target it makes, both in
files git ignores there (``.orchestra/`` and ``orchestra.toml`` are in the clone's
``.git/info/exclude``, so nothing reaches the real repository): an ``[agents]`` section (Claude Code
on ``--model``, serving no role: the example's workers are its own programs), and the Supervisor's
spec with only its built-in duties and its review round once a day, under a narrowed role
(``--tools``). Slack stays off whatever token file exists (this task posts nothing). The
Supervisor's setup is recorded as having run, so the host wakes nobody for a first run."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

TOOLS = ["design", "observe", "model", "events", "workflows"]


def load_demo(workflows: Path):
    path = workflows / "examples" / "delivery_live" / "demo.py"
    spec = importlib.util.spec_from_file_location("delivery_live_demo", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["delivery_live_demo"] = mod
    spec.loader.exec_module(mod)
    return mod


def agents_section(model: str, via: str) -> str:
    return (f'\n# the Supervisor beside the example (orchestra-agents tests/live, L3)\n[agents]\nharness = "claude_code"\n'
            f'model = "{model}"\nserves = []\ntools_via = "{via}"\n')


def supervisor_files(repo: Path, model: str, tools: list[str]) -> None:
    from orchestra_agents import supervisor_spec
    from orchestra_agents.config import load_project
    from orchestra_agents.store import AgentStore

    roles = repo / ".orchestra" / "roles"
    roles.mkdir(parents=True, exist_ok=True)
    (roles / "supervisor.json").write_text(json.dumps({
        "name": "supervisor", "tools": tools, "created_by": "the live design test (L3)",
        "description": "The Supervisor with the design tools and the reads they need only (machine-design.md §7, test 19)."},
        indent=1) + "\n")
    spec = supervisor_spec.starting()
    spec.model, spec.review_every = model, "24h"
    spec.duties = [d for d in spec.duties if d.builtin]
    for rel, body in supervisor_spec.files(spec).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    project = load_project(repo)
    AgentStore(project).set_agent_state("supervisor", setup={"ran_at": 1.0, "harness": project.config.harness})


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ours, demo_args = (argv[:argv.index("--")], argv[argv.index("--") + 1:]) if "--" in argv else (argv, [])
    p = argparse.ArgumentParser()
    p.add_argument("--workflows", type=Path, required=True)
    p.add_argument("--model", default="haiku")
    p.add_argument("--via", default="mcp")
    p.add_argument("--tools", default=",".join(TOOLS))
    ns = p.parse_args(ours)
    demo = load_demo(ns.workflows.expanduser().resolve())
    config, finish = demo.config, demo.finish_target

    def with_agents(dns, urls):
        dns.slack = False                                   # no bot token: nothing is posted to Slack
        return config(dns, urls) + agents_section(ns.model, ns.via)

    def finish_with_supervisor(repo, dns, urls):
        out = finish(repo, dns, urls)
        supervisor_files(out, ns.model, [t for t in ns.tools.split(",") if t])
        return out

    demo.config, demo.finish_target = with_agents, finish_with_supervisor
    return demo.main(demo_args)


if __name__ == "__main__":
    sys.exit(main())
