"""The Workflows plugin's live delivery example (orchestra-workflows ``examples/delivery_live``, what
its ``demo.sh`` runs) with the Supervisor beside it, for the live design test's second scenario
(``test_design_live_delivery.py``, goal task L3).

Run as a program in this repository's environment (orchestra-workflows and the bridges' kits
installed into it, so the host loads the agents and the Workflows plugins together):

    python tests/live/delivery_live_run.py --workflows ~/orchestra-workflows --model haiku --via mcp \\
        -- --tickets 2 --port 8301 --dir RUN [--dry-run]

Everything after ``--`` is ``demo.py``'s own. The Supervisor is put beside the target the example
makes by ``orchestra_agents.live.start_supervisor`` (the function the example's own ``--supervisor``
option calls): ``[agents]`` through Orchestra's configuration edit (Claude Code on ``--model``,
serving no role: the example's workers are its own programs) and the Supervisor's spec with only its
built-in duties and its review round once a day, under a narrowed role (``--tools``), in files git
ignores there (``.orchestra/`` and ``orchestra.toml`` are in the clone's ``.git/info/exclude``, so
nothing reaches the real repository). ``--slack auto`` (the default) posts to Slack when the bridges'
token file (``~/.config/orchestra-bridges/slack-token``) is there and not empty; ``--slack off``
never. The Supervisor's setup is recorded as having run, so the host wakes nobody for a first run."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from orchestra_agents import live


def load_demo(workflows: Path):
    path = workflows / "examples" / "delivery_live" / "demo.py"
    spec = importlib.util.spec_from_file_location("delivery_live_demo", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["delivery_live_demo"] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ours, demo_args = (argv[:argv.index("--")], argv[argv.index("--") + 1:]) if "--" in argv else (argv, [])
    p = argparse.ArgumentParser()
    p.add_argument("--workflows", type=Path, required=True)
    p.add_argument("--model", default="haiku")
    p.add_argument("--via", default="mcp")
    p.add_argument("--tools", default=",".join(live.TOOLS))
    p.add_argument("--slack", choices=live.SLACK_MODES, default="auto",
                   help="auto: post to Slack when the bridges' token file is there; off: never")
    ns = p.parse_args(ours)
    demo = load_demo(ns.workflows.expanduser().resolve())
    config, finish = demo.config, demo.finish_target

    def with_slack(dns, urls):
        dns.slack = live.slack_on(ns.slack)
        return config(dns, urls)

    def finish_with_supervisor(repo, dns, urls):
        out = finish(repo, dns, urls)
        live.start_supervisor(out, role=[t for t in ns.tools.split(",") if t], model=ns.model, via=ns.via, slack=ns.slack)
        return out

    demo.config, demo.finish_target = with_slack, finish_with_supervisor
    return demo.main(demo_args)


if __name__ == "__main__":
    sys.exit(main())
