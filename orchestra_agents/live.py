"""The Supervisor beside a live example, started from a function: what the live delivery test
(``tests/live/delivery_live_run.py``) does to the target the Workflows plugin's delivery example
makes, and what that example's own ``--supervisor`` option calls, with nothing patched.

``start_supervisor`` sets the project's ``[agents]`` through Orchestra's configuration edit (never
by writing ``orchestra.toml`` itself; the example's clone keeps it out of git), writes the
Supervisor's role and spec into the target's ``.orchestra/`` and records its setup as having run,
so the host wakes nobody for a first run. Starting the processes stays the example's
(``orchestra run`` launches the agents' host once ``[agents]`` is set). ``cap_supervisor`` keeps a
standing example's Supervisor within a spend a day: paused at the cap with the reason the Agents
page shows, resumed the next day. Nothing here runs a model."""

from __future__ import annotations

import json
from pathlib import Path

SLACK_TOKEN = Path("~/.config/orchestra-bridges/slack-token")   # the bridges' Slack bot token, by path only
SLACK_MODES = ("auto", "off")
TOOLS = ("design", "observe", "model", "events", "workflows")    # the narrowed role: the design tools and the reads they need


def slack_on(mode: str = "auto", token_file: Path | str = SLACK_TOKEN) -> bool:
    """Whether the example posts to Slack: ``off`` never; ``auto`` when the token file is there and
    not empty. The file is only looked at, never read into anything or printed."""
    if mode not in SLACK_MODES:
        raise ValueError(f"slack is {' or '.join(SLACK_MODES)}, not {mode!r}")
    if mode == "off":
        return False
    p = Path(token_file).expanduser()
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def start_supervisor(project_dir: Path | str, *, role: list[str] | tuple[str, ...] = TOOLS, model: str = "haiku",
                     via: str = "mcp", slack: str = "auto") -> dict:
    """Put the Supervisor beside the example in ``project_dir``, a target the example made (a git
    checkout with its ``orchestra.toml``).

    - ``role``: the tool groups of the Supervisor's narrowed role (``.orchestra/roles/supervisor.json``);
      by default the design tools and the reads they need (``TOOLS``).
    - ``model``: the model its spec runs on (``haiku``, ``sonnet``, ...), also ``[agents] model``.
    - ``via``: how Claude Code reaches the tools, ``[agents] tools_via`` (``mcp`` or ``bash``).
    - ``slack``: ``auto`` (the default: on when ``~/.config/orchestra-bridges/slack-token`` is there
      and not empty) or ``off``. The Supervisor posts nothing itself; the decision is returned for
      the example's own Slack setting, which the caller sets when it writes its configuration.

    ``[agents]`` is set to Claude Code on ``model``, serving no role (the example's workers are its
    own programs). The spec keeps only the built-in duties, with the review round once a day.
    Returns ``{"project": <dir>, "slack": <bool>, "tools": [...], "agents": {...}}``."""
    from . import supervisor_spec
    from .config import load_project
    from .store import AgentStore

    repo = Path(project_dir).expanduser().resolve()
    tools = [t for t in role if t]
    agents = {"agents.harness": "claude_code", "agents.model": model, "agents.serves": [], "agents.tools_via": via}
    on = slack_on(slack)
    project = load_project(repo)
    project.orchestra.config_edit.set_many(agents, target="repo")
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
    return {"project": str(repo), "slack": on, "tools": tools, "agents": {k.split(".", 1)[1]: v for k, v in agents.items()}}


SUPERVISOR = "supervisor"
CAPPED_BY = "the daily spend cap"        # ``paused_by`` of a pause this module made, and only undoes


def cap_supervisor(project_dir: Path | str, usd_per_day: float, t: float | None = None) -> dict:
    """Hold the Supervisor of the example in ``project_dir`` to ``usd_per_day`` dollars a day, every
    turn counted (the agents' spend record, ``budget.spent``: what Claude Code reported per turn).

    At the cap it is **paused, not stopped**: its state says ``paused`` with ``paused_by`` (this
    cap) and ``paused_why`` (what was spent, the cap, when it resumes), which the Agents page shows
    on its chip, and ``agent.paused`` is published with the reason; its wake-ups wait queued and a
    chat message is refused with the reason. The next day (the spend record's day) it is resumed,
    ``agent.resumed`` said, if this cap paused it; a pause by anyone else is never undone here. A
    turn already running when the cap is crossed finishes: the cap can be passed by one turn.
    ``usd_per_day`` of 0 or less: no cap (a pause this cap made is undone).
    Returns ``{"paused", "why", "usd", "changed"}``."""
    from . import budget
    from .config import load_project
    from .store import AgentStore

    store = AgentStore(load_project(Path(project_dir).expanduser().resolve()))
    st = store.agent_state(SUPERVISOR)
    usd = float(budget.spent(store, t).get("usd", 0.0))
    ours = st.get("paused") and st.get("paused_by") == CAPPED_BY
    if usd_per_day > 0 and usd >= usd_per_day:
        if st.get("paused"):
            return {"paused": True, "why": st.get("paused_why") or st.get("crash_loop") or "paused", "usd": usd,
                    "changed": False}
        why = (f"the day's spend cap: ${usd:.2f} spent on {budget.day(t)}, the cap is ${usd_per_day:.2f} a day; "
               "it resumes tomorrow")
        store.set_agent_state(SUPERVISOR, paused=True, paused_by=CAPPED_BY, paused_why=why)
        store.events.emit("agent.paused", src=SUPERVISOR, agent=SUPERVISOR, by=CAPPED_BY, why=why)
        return {"paused": True, "why": why, "usd": usd, "changed": True}
    if ours:
        store.set_agent_state(SUPERVISOR, paused=False, paused_by=None, paused_why=None)
        store.events.emit("agent.resumed", src=SUPERVISOR, agent=SUPERVISOR, by=CAPPED_BY,
                          why=f"a new day: ${usd:.2f} spent so far, the cap is ${usd_per_day:.2f}")
        return {"paused": False, "why": None, "usd": usd, "changed": True}
    return {"paused": bool(st.get("paused")), "why": st.get("paused_why") if st.get("paused") else None, "usd": usd,
            "changed": False}
