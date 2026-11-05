"""Sub-sessions for bounded jobs (the owner, 2026-11-04: cut the per-call context and use subagents
to reduce cost). ``delegate <job> <brief>`` starts a one-shot session of the Supervisor's own harness
with a small system prompt (the job's skill, ``prompts/skills/<job>.md``, and the tools it holds),
on ``[agents] delegate_model`` (default the Supervisor's), runs one turn on the brief and returns its
short report. The Supervisor's long session never carries the job's reads: a survey's 20 KB of
families stays in the sub-session, which ends with the job.

The sub-session acts as the Supervisor (its tool calls are the Supervisor's, with its bounds), with
only the job's tool groups and never ``delegate`` itself. Its spend is the Supervisor's: refused when
the day's cap for the turn it runs in is spent (the owner's chat, or its own initiative), and added to
that cap when it ends (``budget.record``, ``delegated``).

The host runs it (``delegate_host.py``, the owner, 2026-11-04: a delegated job "got a 401 error from my
LLM service" while the Supervisor itself was signed in): the tool command runs inside the Supervisor's
shell, often in Claude Code's sandbox, where a nested session may not reach the owner's sign-in (the
keychain, a gateway's variables). So the tool command asks the host's control endpoint, and the
sub-session starts from the host's process and environment, as the Supervisor's own session does. A
turn the provider refused to sign in is reported plainly (``sign_in.py``)."""

from __future__ import annotations

import os
import time

from . import access, budget, push_guard, sign_in, skills
from .config import Project
from .harness.base import TOOL_CALL_S
from .store import AgentStore, StoreError

# job -> (its skill, the tool groups it holds)
JOBS: dict[str, tuple[str, tuple[str, ...]]] = {
    "survey": ("survey", ("observe", "flows", "guides")),
    "scaffold_poller": ("scaffold-poller", ("design", "observe", "guides")),
    "judge_experiment": ("judge-experiment", ("observe", "setup", "model")),
}
REPORT_CHARS = 4_000          # the most of the sub-session's report the Supervisor reads
TURN_S = TOOL_CALL_S - 60       # the most one delegated turn may take: inside the call that waits for it
DELEGATED_ENV = "ORCHESTRA_DELEGATED"     # set in a sub-session: it never delegates again
MARK = "[Delegated job: {job}]"

PREAMBLE = """# You are a sub-session of the Orchestra Supervisor

The Supervisor gave you one bounded job; do it with your tools and end with a short report to it.
You never talk to the owner and never ask a question: what you cannot do, say in the report. You
never edit a file, never run the project's code or tests, never read outside the project's tree and
Orchestra's state directory, never read, print or ask for a token, and never read a store beyond the
bound the owner gave it. Every rule of the job below binds you."""


def prompt(project: Project, job: str) -> str:
    """The sub-session's whole system prompt: the preamble, the job's skill and the tools it holds."""
    from .prompting import with_tools
    skill, groups = JOBS[job]
    return with_tools(f"{PREAMBLE}\n\n{skills.text(project, skill).strip()}", groups)


def model(project: Project, harness) -> str | None:
    from .machine_spec import model_of
    return project.config.delegate_model or model_of(project, harness, "supervisor")


def _chat(store: AgentStore) -> bool:
    """Whether the Supervisor's turn that delegates is the owner's chat (``design.begin_turn``: a turn
    with the owner's message); a wake-up's is its initiative."""
    return bool((store.agent_state(budget.MACHINE).get("turn") or {}).get("message"))


def _refused(project: Project, store: AgentStore, harness, model_: str | None, chat: bool) -> str | None:
    cfg = project.config
    return budget.unpriced(harness, model_, cfg, chat=chat) or (budget.chat_over(store, cfg) if chat else budget.over(store, cfg))


AGAIN = "a delegated job never delegates again: do the job with your own tools"


def check(a: dict, role: str, env) -> tuple[str, str]:
    """The job and the brief of a ``delegate`` call, or a StoreError saying why it is refused."""
    if role != "supervisor":
        raise StoreError("only the Supervisor delegates")
    if env.get(DELEGATED_ENV):
        raise StoreError(AGAIN)
    job, brief = (a.get("job") or "").strip(), (a.get("brief") or "").strip()
    if job not in JOBS:
        raise StoreError(f"no job {job!r}; the jobs are {', '.join(JOBS)}")
    if not brief:
        raise StoreError("a brief is needed: what the job is, with every value it needs (the store, the system, the experiment)")
    return job, brief


def turn_s(project: Project) -> float:
    """The most one delegated turn may take: ``[agents] turn_timeout`` when it is shorter."""
    t = project.config.turn_timeout
    return min(t or TURN_S, TURN_S)


def delegate(project: Project, store: AgentStore, a: dict, role: str, env: dict | None = None, *,
             by_host: bool = False, on_event=None) -> dict:
    """``delegate``: one sub-session's turn on the brief; its report, its cost and its transcript.
    ``by_host``: the agents host runs it (delegate_host.py), from its own environment. ``on_event``
    hears ``{"type": "started", ...}`` once the session is open and each tool the sub-session calls."""
    from .harness import get_harness
    from .harness.base import SessionSpec
    env = os.environ if env is None else env
    job, brief = check(a, role, env)
    cfg = project.config
    if cfg.tools_via == "file":
        raise StoreError("no job can be delegated with [agents] tools_via = \"file\": a sub-session would have no tools")
    harness = get_harness(cfg.harness)
    model_ = model(project, harness)
    chat = _chat(store)
    if why := _refused(project, store, harness, model_, chat):
        raise StoreError(f"not delegated: {why}")
    sid = f"supervisor-{job.replace('_', '-')}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    token = access.issue(store, sid, "supervisor", delegated=job)
    _, groups = JOBS[job]
    senv = {"ORCHESTRA_ROLE": "supervisor", "ORCHESTRA_REPO": str(project.repo_root), "ORCHESTRA_TICKET": "",
            "ORCHESTRA_SOURCE": "supervisor", DELEGATED_ENV: job, access.TOKEN_ENV: token}
    senv |= push_guard.env("supervisor", cfg.allow_push, project.state_dir / "no-push", {**os.environ, **senv})
    system = prompt(project, job)
    logs = project.logs_dir / "agents"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{sid}.prompt.md").write_text(system, encoding="utf-8")
    bounds = tuple(dict.fromkeys(str(p) for p in (project.repo_root, project.orchestra.state_dir)))
    spec = SessionSpec(session_id=sid, session_dir=project.sessions_dir, cwd=project.repo_root, system_prompt=system,
                       role="supervisor", tool_groups=groups, env=senv, log_path=logs / f"{sid}.stderr.log", model=model_,
                       provider=cfg.provider, thinking=cfg.thinking, compact_tokens=None, tools_via=cfg.tools_via, sandbox_escape=cfg.sandbox_escape,
                       pass_env=cfg.pass_env, read_only=True, bounds=bounds)
    tell = on_event or (lambda ev: None)

    def heard(ev: dict) -> None:
        if ev.get("type") == "tool_start":
            tell({"type": "tool", "name": ev.get("name")})
    session = harness.start(spec)
    try:
        session.open()
        tell({"type": "started", "job": job, "model": model_, "session": sid, "transcript": session.session_file})
        res = session.prompt(MARK.format(job=job) + "\n\n" + brief, turn_s(project), on_event=heard,
                             idle_timeout=cfg.silence_timeout)
    finally:
        session.close()
        access.revoke(store, sid)
    budget.record(store, res.cost_usd, initiative=not chat, chat=chat, delegated=True)
    report = (res.text or "").strip()
    out = {"ok": not (res.error or res.timed_out), "job": job, "model": model_,
           "report": report if len(report) <= REPORT_CHARS else report[:REPORT_CHARS] + " … (the report was cut here)",
           "cost_usd": res.cost_usd, "transcript": session.session_file}
    if res.error or res.timed_out:
        out["error"] = res.error or f"the sub-session stopped ({res.stopped_by or 'timed out'}) before its report"
    said = [res.error, report, _tail(spec.log_path)] if not out["ok"] else []
    if (why := next((x for x in said if sign_in.failed(x)), None)) is not None:
        out["error"] = out["sign_in"] = sign_in.report(harness, why, by_host=by_host, pass_env=cfg.pass_env)
        out["report"] = ""
    out["started_by"] = "host" if by_host else "tool command"
    return out


def _tail(path, most: int = 2_000) -> str:
    """The end of the sub-session's stderr log (a harness that exits on a refused sign-in may say why only there)."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-most:]
    except OSError:
        return ""
