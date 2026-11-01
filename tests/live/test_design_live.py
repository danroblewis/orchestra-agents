"""The owner's scenario live (goal task A3; machine-design.md §7 test 19): the Supervisor on real
Claude Code with Haiku, in the agents' demo, designing a measured stock and a Universal Machine
from the owner's words, asserted on Orchestra's declared layer through the core's web API.

The narrative is the owner's (design-backwards-notes.md §13.1-13.2) with the easier target the
owner asked for mid-run (goal log 2026-11-01T15:30Z): the demo's own measurement of the repository
(`repo_stats`, DEMO-4's collector: `repo_files`, `repo_commits`) stands in for Features, so the
stock is "Notes Files" and the machine the "Notes Improver". The turns (§2.6's 1, 3, 4, 5, then
confirm) go to the Supervisor's chat endpoint (`POST /api/plugins/agents/supervisor`), one at a
time, each waited for; after each, the core's API is read:

1. the stock declared, its options naming the existing `repo_stats` series, and a series linked
   or recording for it (`/api/intended`, D1's recording in `/api/dynamics/model`);
3. the machine proposed, Universal, with its spec drafted;
4. its receives `changes.requested`, `gate.rejected`, `gate.landed`; every receive it dropped
   recorded as removed (the journal) and drawn gone on the map (`/api/machines`, E2);
5. engagement scheduled daily at 00:00;
6. "Confirm it": the machine declared, its prediction published as the Supervisor's, about the
   change id confirm allocated (`/api/dynamics/predictions`);

and no reply asks the owner to edit configuration (P4's forbidden phrases).

The demo is started by demo/run.py's own functions, with what DEMO-4 lands already on main (the
collector, every 15 s). A planner then asks for a notes file every 20 s as a command ticket, which
programs work, review and land (the shell worker, the task system's check reviewer, the gate), so
notes files, commits and the ticket pipeline move, the events the owner's words in turn 4 name are
on the bus, and discovery has a model before the turns (``ORCHESTRA_LIVE_WARM_S``, 7 minutes). No
AI agent takes a ticket. The Supervisor runs with the narrowed tool set of test 19 (the `design`,
`observe`, `model` and `events` groups, through a project role record
`.orchestra/roles/supervisor.json`, which wins over the built-in on its name) on the model named,
with no duties of its own (its review round once a day), so the only turns it takes are the
owner's. The confirm turn carries the owner's expectation (notes files rise, the commit rate up 1.2
times), so the prediction has something to say.

Each turn's message is sent once and judged as it was answered: a failed turn is recorded and the
run goes on to the next (re-sending a failed turn confused the model into declaring an extra
machine, A3's first runs). The run stops at the cost cap.

It costs money and needs a logged-in ``claude``, so it runs only when asked:

    ORCHESTRA_LIVE_DESIGN=1 uv run python -m pytest -q -s tests/live/test_design_live.py
    #   ORCHESTRA_LIVE_TOOLS_VIA=bash      only one way of calling the tools (default: mcp,bash)
    #   ORCHESTRA_LIVE_MODEL=sonnet        the Supervisor on another model (default haiku)
    #   ORCHESTRA_LIVE_CAP_USD=10          stop past this spend (default 10, the goal's cap)
    ORCHESTRA_LIVE_DESIGN=rehearse ...     the same run with the fake Claude, scripted: free

Claude Code's stream for every session (a wrapper tees it) and the Claude Code transcripts of the
sessions in the project are kept in the project's state directory (``live/``), with the API's
answers after each turn; a live run copies them, every token redacted, to
``tests/live/transcripts/<date>/<tools_via>/``. The cost is Claude Code's own (``total_cost_usd``
of each ``result`` event)."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import free_port

from demo.run import Web, make_project, start, stop, wait_for, wait_until, web_token

LIVE = os.environ.get("ORCHESTRA_LIVE_DESIGN", "")
REHEARSE = LIVE == "rehearse"
pytestmark = pytest.mark.skipif(LIVE not in ("1", "rehearse") or (LIVE == "1" and not shutil.which("claude")),
                                reason="a live run on Haiku: set ORCHESTRA_LIVE_DESIGN=1 with a logged-in claude on the "
                                       "PATH (or =rehearse for the fake)")
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FAKE = REPO / "tests" / "e2e" / "fake_claude.py"
MODEL = os.environ.get("ORCHESTRA_LIVE_MODEL", "haiku")
CAP_USD = float(os.environ.get("ORCHESTRA_LIVE_CAP_USD", "10"))
VIAS = [v for v in os.environ.get("ORCHESTRA_LIVE_TOOLS_VIA", "mcp,bash").split(",") if v]
AGENTS = "/api/plugins/agents"
NEVER_PORTS = {8000, 8001, 8002, 8003, 8040, 8050, 8060, 8062, 8070, 8074, 8096, 8428, 9090}   # COORDINATOR.md §6
TURN_S = 900
WARM_S = float(os.environ.get("ORCHESTRA_LIVE_WARM_S", "420"))   # notes landing before the first turn

TURN1 = ("I want a feedback loop where once per day an agent runs and tries to improve how many notes files get added "
         "per day. I think we need to start tracking notes files. Create a Notes Files stock.")
TURN3 = "Create a new Universal Machine called Notes Improver."
TURN4 = ("I think the Notes Improver would watch the ticket queue for DONE and REJECTED tickets and the merge gate, "
         "figure out why tickets got rejected or failed the gate, and send ideas to the Coordinator to schedule work.")
TURN5 = "This has a lot of high-throughput event triggers, make it a scheduled task for 12am every day."
CONFIRM = "Confirm it. I expect notes files to rise, and the commit rate to go up 1.2 times."
RECEIVES = {"changes.requested", "gate.rejected", "gate.landed"}
FORBIDDEN = ("edit orchestra.toml", "add this to", "paste", "put this in", "run this command", "```toml")   # P4 §7 test 17
NARROWED = ["design", "observe", "model", "events"]                        # §7 test 19: design + the reads it needs

# DEMO-4 as it lands (demo/dummy.toml's collector), on a shorter interval so discovery has points sooner
REPO_STATS = ("#!/bin/sh\n# The repository's size, as Prometheus text: the files git tracks and the commits on main.\n"
              "set -eu\necho \"repo_files $(git ls-files | wc -l | tr -d ' ')\"\n"
              "echo \"repo_commits $(git rev-list --count main)\"\n")
REVIEWER = ('\n[[machines]]\nname = "check_reviewer"\n'
            'command = "python -m tasking.machines.check_reviewer program"   # the reviewer: a program, no model\n')
COLLECT = '\n[[collect]]\nname = "repo_stats"\ncommand = "machines/repo_stats.sh"\nevery = "15s"\n'


# ---- the demo, prepared ----

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=demo", "-c", "user.email=demo@example.invalid", *args], cwd=repo,
                          capture_output=True, text=True, check=True).stdout


def wrapper(bin_dir: Path, binary: str, live_dir: Path) -> Path:
    """A `claude` that runs ``binary`` and tees its stream to live/stream-<pid>.jsonl (the cost and
    the transcript), keeping each session's token in live/.secrets for the redaction (never printed)."""
    w = bin_dir / "claude"
    w.write_text("#!/bin/bash\n"
                 f'[ -n "$ORCHESTRA_SESSION_TOKEN" ] && printf "%s\\n" "$ORCHESTRA_SESSION_TOKEN" >> "{live_dir}/.secrets"\n'
                 f'"{binary}" "$@" | tee -a "{live_dir}/stream-$$.jsonl"\nexit ${{PIPESTATUS[0]}}\n')
    w.chmod(w.stat().st_mode | stat.S_IEXEC)
    return w


def prepare(target: Path, via: str, model: str) -> None:
    """What DEMO-4 lands, already on main; the tickets worked and reviewed by programs (the shell
    worker, the task system's check reviewer), so no AI agent but the Supervisor takes a turn; its role narrowed (test 19) and its spec on ``model``, with no
    duties beyond the built-in ones and its review round once a day."""
    from orchestra_agents import supervisor_spec

    (target / "machines").mkdir(exist_ok=True)
    (target / "machines" / "repo_stats.sh").write_text(REPO_STATS)
    (target / "machines" / "repo_stats.sh").chmod(0o755)
    toml = target / "orchestra.toml"
    text = toml.read_text()
    assert 'serves = ["worker", "reviewer"]' in text, "demo/orchestra.toml's [agents] changed; update prepare"
    toml.write_text(text.replace('serves = ["worker", "reviewer"]', "serves = []") + COLLECT + REVIEWER)
    roles = target / ".orchestra" / "roles"
    roles.mkdir(parents=True, exist_ok=True)
    (roles / "supervisor.json").write_text(json.dumps({
        "name": "supervisor", "tools": NARROWED, "created_by": "the live design test (A3)",
        "description": "The Supervisor with the design tools and the reads they need only (machine-design.md §7, test 19)."},
        indent=1) + "\n")
    spec = supervisor_spec.starting()
    spec.model, spec.review_every = model, "24h"
    spec.duties = [d for d in spec.duties if d.builtin]
    for rel, body in supervisor_spec.files(spec).items():
        p = target / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    git(target, "add", "-A")
    git(target, "commit", "-qm", f"DEMO-4 landed (the collector), the Supervisor on {model} with the design tools ({via})")


class NotesPlanner(threading.Thread):
    """A planner asking for one notes file every ``every`` seconds, as a command ticket (requests
    over the bus, as demo/run.py's planner makes its own): the program worker writes it, the
    program reviewer checks it, the gate lands it. What moves repo_files and repo_commits, and the
    ticket pipeline, so discovery has something to find."""

    def __init__(self, repo: Path, every: float = 20.0):
        super().__init__(daemon=True, name="notes-planner")
        self.repo, self.every, self.done, self.n = repo, every, threading.Event(), 0

    def run(self) -> None:
        from orchestra_agents.config import load_project
        from orchestra_agents.events import Events
        from orchestra_agents.protocol import TaskClient

        ask = TaskClient(Events.for_project(load_project(self.repo), source="planner"), "planner")
        milestone = ask("create_milestone", {"name": "Notes", "description": "A notes file every few seconds."})["milestone"]["id"]
        while not self.done.wait(self.every):
            self.n += 1
            f = f"notes/note-{self.n:03d}.txt"
            try:
                ask("create_ticket", {"title": f"Add note {self.n}", "objective": "A notes file, added by a command ticket.",
                                      "milestone_id": milestone, "verification": "The reviewer checks the file; the gate lands it.",
                                      "description": f"```sh\nmkdir -p notes\necho 'note {self.n}' > {f}\n```",
                                      "acceptance": f"- `{f}` exists"})
            except Exception as e:  # noqa: BLE001 - a refused request: the next one asks again
                print(f"notes planner: create_ticket {self.n}: {e}", flush=True)


# ---- talking to it ----

def say(web: Web, message: str) -> dict:
    """One chat turn, waited for: the reply's text, the tools it called, and any error."""
    req = urllib.request.Request(web.url + f"{AGENTS}/supervisor", data=json.dumps({"message": message}).encode(),
                                 headers={"Authorization": f"Bearer {web.token}", "Content-Type": "application/json"},
                                 method="POST")
    tools, done = [], {}
    with urllib.request.urlopen(req, timeout=TURN_S) as r:
        for line in r:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "tool_start":
                tools.append(str(ev.get("name") or "").removeprefix("mcp__orchestra__"))
            elif ev.get("type") == "done":
                done = ev
    return {"message": message, "text": done.get("text") or "", "error": done.get("error"),
            "timed_out": done.get("timed_out"), "tools": tools}


def layer(web: Web) -> dict:
    return web.get("/api/intended")["layer"]


def find(items: dict, pattern: str) -> tuple[str | None, dict]:
    """A declared element by its id or name (Haiku names it; the slug is the tools')."""
    for k, v in (items or {}).items():
        if re.search(pattern, f"{k} {v.get('name') or ''} {v.get('title') or ''}", re.IGNORECASE):
            return k, v
    return None, {}


def costs(live_dir: Path) -> dict:
    """Claude Code's own cost and usage, summed over every `result` event of every session."""
    usd, usage, turns = 0.0, {}, 0
    for f in live_dir.glob("stream-*.jsonl"):
        for ln in f.read_text(errors="replace").splitlines():
            if not ln.startswith("{") or '"result"' not in ln:
                continue
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            if ev.get("type") != "result":
                continue
            turns += 1
            usd += float(ev.get("total_cost_usd") or 0)
            for k, v in (ev.get("usage") or {}).items():
                if isinstance(v, int | float):
                    usage[k] = usage.get(k, 0) + v
    return {"usd": round(usd, 4), "invocations": turns, "usage": usage}


def rediscover(web: Web) -> dict:
    """The Dynamics page's button (an observation), waited for."""
    req = urllib.request.Request(web.url + "/api/dynamics/rediscover", data=b"{}", method="POST",
                                 headers={"Authorization": f"Bearer {web.token}", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()
    wait_until("the rediscovery", lambda: not web.get("/api/dynamics/rediscover")["running"], timeout_s=300)
    return web.get("/api/dynamics/rediscover")


# ---- the checks, each on the core's API: (ok, why, evidence) ----

def check_stock(web: Web) -> tuple[bool, str, dict]:
    sid, s = find(layer(web).get("stocks"), r"notes")
    if not sid:
        return False, "no declared stock named like Notes Files", {}
    options = (s.get("measure") or {}).get("options") or []
    existing = [o for o in options if o.get("kind") == "existing_series" and str(o.get("series") or "").startswith("repo_")]
    d1 = next((e for e in (((web.get("/api/dynamics/model").get("model") or {}).get("intended") or {}).get("expected") or [])
               if e.get("id") == sid), {})
    rec = ((d1.get("measure") or {}).get("recording") or {})
    recording = [o for o in options if o.get("instrument") and o.get("first_points")]
    ev = {"stock": {k: s.get(k) for k in ("id", "name", "state", "found_as", "linked", "near")},
          "options": [{k: o.get(k) for k in ("key", "kind", "title", "series", "instrument", "first_points")} for o in options],
          "d1": {k: d1.get(k) for k in ("measured", "linked", "key", "why")} | {"recording": rec}}
    if s.get("state") != "declared":
        return False, f"the stock is {s.get('state')}, not declared (no quote of the owner's words?)", ev
    if not existing:
        return False, "no option names the existing repo_stats series (existing_series repo_files or repo_commits)", ev
    if not (s.get("found_as") or recording or rec.get("points")):
        return False, "no series linked to it and none recording for it", ev
    return True, "", ev


def check_machine(web: Web) -> tuple[bool, str, dict]:
    mid, m = find(layer(web).get("machines"), r"notes.?improver")
    ev = {k: m.get(k) for k in ("id", "title", "kind", "state", "engagement", "receives", "emits", "reads", "spec", "guessed",
                                "expected_effect")}
    if not mid:
        return False, "no machine named like Notes Improver", ev
    spec = m.get("spec") or {}
    if m.get("state") != "proposed" or m.get("kind") != "universal" or not spec.get("steps"):
        return False, f"the machine is {m.get('state')}, {m.get('kind')}, with {len(spec.get('steps') or [])} steps", ev
    return True, "", ev


def check_receives(web: Web, before: list[str]) -> tuple[bool, str, dict]:
    mid, m = find(layer(web).get("machines"), r"notes.?improver")
    now = list(m.get("receives") or [])
    dropped = [t for t in before if t not in now]
    removed = set()
    for e in web.get("/api/intended/journal").get("journal") or []:
        for op in e.get("sent") or e.get("ops") or []:
            if op.get("op") == "revise_machine" and op.get("id") == mid:
                removed |= set((op.get("remove") or {}).get("receives") or [])
    gone = {e["from"] for e in web.get("/api/machines").get("intended_edges") or [] if e.get("to") == mid and e.get("gone")}
    ev = {"before": before, "receives": now, "dropped": dropped, "removed_in_journal": sorted(removed),
          "gone_on_the_map": sorted(gone), "pending": m.get("pending"), "latest": m.get("latest")}
    if not RECEIVES <= set(now):
        return False, f"receives are {now}; missing {sorted(RECEIVES - set(now))}", ev
    if unmarked := [t for t in dropped if t not in removed]:
        return False, f"dropped {unmarked} without a recorded removal", ev
    return True, "", ev


def check_schedule(web: Web) -> tuple[bool, str, dict]:
    _, m = find(layer(web).get("machines"), r"notes.?improver")
    e = m.get("engagement") or {}
    ev = {"engagement": e, "pending": m.get("pending"), "latest": m.get("latest")}
    if e.get("mode") != "scheduled" or e.get("at") not in ("00:00", "0:00"):
        return False, f"engagement is {e}", ev
    return True, "", ev


def check_confirmed(web: Web) -> tuple[bool, str, dict]:
    mid, m = find(layer(web).get("machines"), r"notes.?improver")
    pid = (m.get("expected_effect") or {}).get("prediction")
    preds = web.get("/api/dynamics/predictions")
    allp = [p for k in ("predictions", "waiting", "judged") for p in (preds.get(k) or []) if isinstance(p, dict)]
    mine = [p for p in allp if p.get("subject") == f"machine:{mid}"]
    ev = {"state": m.get("state"), "expected_effect": m.get("expected_effect"),
          "predictions": [{k: p.get(k) for k in ("id", "source", "subject", "change", "expect", "status")} for p in mine],
          "predictions_keys": sorted(preds)}
    if m.get("state") != "declared":
        return False, f"the machine is {m.get('state')}, not confirmed", ev
    if not pid or not mine:
        return False, "no prediction published for it", ev
    p = next((p for p in mine if p.get("id") == pid), mine[-1])
    if p.get("source") != "supervisor" or not re.fullmatch(r"[0-9a-f]{32}", str(p.get("change") or "")):
        return False, f"the prediction is {p.get('source')}'s, about change {p.get('change')!r}", ev
    return True, "", ev


# ---- the rehearsal: the same run on the fake Claude, each turn's tool calls scripted ----

def rehearsal(turn: int, model: dict) -> list[dict]:
    why = "the owner's words this turn"
    keys = [s.get("id") for s in model.get("stocks") or []]
    files = next((k for k in keys if str(k).startswith("repo_files")), keys[0] if keys else "repo_files")
    commits = next((k for k in keys if str(k).startswith("repo_commits")), None)
    rate = sorted(model.get("flows") or [], key=lambda f: f.get("to") != commits and f.get("dst") != commits)
    flows = [{"kind": "factor", "flow": f["id"], "factor": 1.2} for f in rate[:1]]
    steps = ["At midnight, read the day's changes.requested, gate.rejected and gate.landed",
             "Find why each was rejected or failed the gate", "Publish one idea for the Coordinator"]
    return {
        1: [{"tool": "declare_stock", "args": {"name": "Notes Files", "quote": "Create a Notes Files stock.", "reason": why}},
            {"tool": "list_metrics", "args": {}},
            {"tool": "propose_measurements", "args": {"element": "notes_files", "reason": why, "options": [
                {"label": "files git tracks", "kind": "existing_series", "series": "repo_files", "how": "repo_stats",
                 "cost": "nothing new"},
                {"label": "notes files git tracks", "kind": "script", "how": "git ls-files notes", "cost": "under a second"}]}},
            {"tool": "link", "args": {"declared": "notes_files", "discovered": files, "reason": why}}],
        3: [{"tool": "declare_machine", "args": {
            "name": "Notes Improver", "kind": "universal", "description": "Improves how many notes files are added a day",
            "mode": "continuous", "receives": ["gate.landed"], "emits": ["work.requested"],
            "steps": ["Read task.reported", "Publish one idea"], "invariants": ["never changes code itself"],
            "uses": ["the ticket queue, read only"], "quote": "Create a new Universal Machine called Notes Improver.",
            "reason": why}}],
        4: [{"tool": "refine_machine", "args": {
            "name": "notes_improver", "quote": "watch the ticket queue for DONE and REJECTED tickets and the merge gate",
            "add_receives": sorted(RECEIVES - {"gate.landed"}), "reason": why}}],
        5: [{"tool": "refine_machine", "args": {
            "name": "notes_improver", "quote": "make it a scheduled task for 12am every day", "mode": "scheduled",
            "at": "00:00", "missed": "skip", "steps": steps, "reason": why}}],
        6: [{"tool": "declare_expected_effect", "args": {"machine": "notes_improver", "horizon": "3d", "reason": why,
                                                         "expect": [{"kind": "rises", "stock": "notes_files"}, *flows]}},
            {"tool": "confirm", "args": {"element": "machine:notes_improver", "quote": "I expect notes files to rise", "reason": why}}],
    }[turn]


# ---- keeping what happened ----

def redact(text: str, secrets: list[str]) -> str:
    for s in sorted({s for s in secrets if len(s) >= 12}, key=len, reverse=True):
        text = text.replace(s, "[redacted]")
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1[redacted]", text)
    text = re.sub(r"(#token=)[^\s\"']+", r"\1[redacted]", text)
    text = re.sub(r"[A-Za-z0-9._%+-]+@(?!example\.invalid)[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[email]", text)   # the account's
    return re.sub(r"sk-ant-[A-Za-z0-9_-]+", "[redacted]", text)


def keep(target: Path, live_dir: Path, state_dir: Path, secrets: list[str], dest: Path | None) -> list[Path]:
    """Claude Code's transcripts of the sessions run in the project, beside the streams, in
    live/; with ``dest``, every text file there copied, redacted."""
    from orchestra_agents.harness.claude_code import transcript_path

    projects = transcript_path(target, "x").parent
    for f in projects.glob("*.jsonl") if projects.is_dir() else []:
        shutil.copy(f, live_dir / f"transcript-{f.name}")
    for f in (state_dir / "logs").rglob("*.prompt.md"):
        shutil.copy(f, live_dir / f"prompt-{f.name}")
    if dest is None:
        return []
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(live_dir.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            (dest / f.name).write_text(redact(f.read_text(errors="replace"), secrets))
            out.append(dest / f.name)
    return out


# ---- the run ----

@pytest.mark.parametrize("via", VIAS)
def test_the_owner_designs_notes_files_and_the_notes_improver_live(via, tmp_path, monkeypatch):
    from orchestra import runtime
    from orchestra.config import home, load_project, project_key

    port = free_port()
    assert port > 8100 and port not in NEVER_PORTS, port
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = {"ORCHESTRATOR_HOME": str(tmp_path / "home")}
    if REHEARSE:
        fake, script = bin_dir / "fake_claude", tmp_path / "supervisor.json"
        shutil.copy(FAKE, fake)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        env |= {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-home"), "FAKE_CLAUDE_SUPERVISOR": str(script)}
        binary = str(fake)
    else:
        binary = shutil.which("claude")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in ("ORCHESTRA_EVENTS_URL", "ORCHESTRA_METRICS_URL", "ORCHESTRA_SOURCE", "ORCHESTRA_ROLE", "ORCHESTRA_SESSION_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    target = make_project(tmp_path / "demo", tools_via=via)
    model = MODEL                                       # the Supervisor's, for every turn: never switched
    prepare(target, via, model)
    state_dir = home() / project_key(target)
    live_dir = state_dir / "live"
    live_dir.mkdir(parents=True, exist_ok=True)
    env["CLAUDE_BINARY"] = str(wrapper(bin_dir, binary, live_dir))
    monkeypatch.setenv("CLAUDE_BINARY", env["CLAUDE_BINARY"])

    procs = start(target, port, env=env)
    writer = NotesPlanner(target)
    web = Web(f"http://127.0.0.1:{port}", web_token(target, env))
    log: list[dict] = []
    failures: list[str] = []
    secrets = [web.token]
    t0 = time.time()

    def snapshot(name: str, what) -> None:
        (live_dir / f"{name}.json").write_text(json.dumps(what, indent=1, default=str))

    def turn(n: int, message: str, check, *a) -> dict:
        """The owner's message, sent once, and judged as it was answered."""
        if (spent := costs(live_dir)["usd"]) > CAP_USD:
            pytest.fail(f"the cost cap: ${spent:.2f} spent before turn {n} (cap ${CAP_USD:.2f}); stopped")
        if REHEARSE:
            script.write_text(json.dumps(rehearsal(n, web.get("/api/dynamics/model").get("model") or {})))
        began = time.time()
        reply = say(web, message)
        ok, why, ev = check(web, *a)
        log.append({"turn": n, "model": model, "seconds": round(time.time() - began), **reply,
                    "ok": ok, "why": why, "state": ev, "cost_so_far": costs(live_dir)})
        snapshot(f"turn{n}", log[-1])
        print(f"\n[turn {n} on {model}, {log[-1]['seconds']} s] ok={ok} {why}\ntools: {reply['tools']}\n"
              f"reply: {reply['text'][:1200]}", flush=True)
        if not ok:
            failures.append(f"turn {n}: {why}")
        return ev

    try:
        wait_for(web, procs)
        writer.start()
        wait_until("three notes tickets landing", lambda: [t["state"] for t in web.get("/api/plugins/tasks/state")["tickets"]]
                   .count("DONE") >= 3, timeout_s=600)
        time.sleep(WARM_S)                                 # points for discovery: a level must change in 20 stretches
        snapshot("rediscovered-before", found := rediscover(web))
        print(f"\n[discovery before the turns] {found.get('result') or found.get('error')}", flush=True)
        snapshot("model-before", web.get("/api/dynamics/model"))

        turn(1, TURN1, check_stock)
        turn(3, TURN3, check_machine)
        _, m = find(layer(web).get("machines"), r"notes.?improver")
        turn(4, TURN4, check_receives, list(m.get("receives") or []))
        turn(5, TURN5, check_schedule)
        snapshot("rediscovered-confirm", found := rediscover(web))
        print(f"\n[discovery before confirm] {found.get('result') or found.get('error')}", flush=True)
        turn(6, CONFIRM, check_confirmed)
        snapshot("api-intended", web.get("/api/intended"))
        snapshot("api-dynamics-model", web.get("/api/dynamics/model"))          # D1, as the page reads it
        snapshot("api-machines", web.get("/api/machines"))                      # E2
        snapshot("api-predictions", web.get("/api/dynamics/predictions"))
    finally:
        writer.done.set()
        stop(procs)
        left = runtime.read(load_project(target)).get("processes") or {}
        alive = [n for n, r in left.items() if r.get("pid") and runtime.alive(r["pid"])]
        secrets += (live_dir / ".secrets").read_text().split() if (live_dir / ".secrets").exists() else []
        secrets += [t.read_text().strip() for t in (state_dir / "agents" / "control.token", state_dir / "events.token",
                                                     state_dir / "web.token") if t.exists()]
        spent = costs(live_dir)
        summary = {"tools_via": via, "model": MODEL, "sent_once": True, "cost": spent,
                   "minutes": round((time.time() - t0) / 60, 1), "failures": failures, "processes_left": alive,
                   "turns": [{k: e[k] for k in ("turn", "model", "seconds", "ok", "why", "tools", "text", "error")}
                             for e in log]}
        snapshot("summary", summary)
        run = f"{via}-{model}-once"
        dest = None if REHEARSE else HERE / "transcripts" / datetime.now(UTC).strftime("%Y-%m-%d") / run
        kept = keep(target, live_dir, state_dir, secrets, dest)
        print(f"\nCOST ${spent['usd']:.4f} over {spent['invocations']} Claude Code turns; usage {spent['usage']}\n"
              f"KEPT {live_dir}" + (f" and {len(kept)} files in {dest}" if dest else ""), flush=True)
    assert not alive, alive
    said = " ".join(e["text"] for e in log).lower()
    assert not [p for p in FORBIDDEN if p in said], [p for p in FORBIDDEN if p in said]
    assert not failures, failures
