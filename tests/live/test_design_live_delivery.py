"""The owner's design scenario on the live delivery example (goal task L3, milestone S5): the
Supervisor on real Claude Code with Haiku beside orchestra-workflows' ``examples/delivery_live``
(the owner's real SCRUM board on Jira Cloud, the private repository
danroblewis/orchestra-live-delivery, real GitHub Actions, the fake SAIL), instead of the notes files
stand-in of ``test_design_live.py``.

The example is started as its ``demo.sh`` starts it (``demo.py``, with 2 test tickets and the fake
SAIL's ``--seed 1``, whose first check fails), by ``delivery_live_run.py``, which adds the
Supervisor beside it (an ``[agents]`` section serving no role, the narrowed role of test 19 with
the ``workflows`` reads added, built-in duties only), all in files the clone's git ignores. When the
SAIL has answered on each ticket (write → PR → CI → review → SAIL: about 20 minutes at the
pollers' 5 minutes; the first check fails, so one ticket is then in rework), discovery runs and the owner's day is played in the design
tools' terms, each message sent once and judged as it was answered (on the declared layer,
``/api/machines``, ``/api/dynamics/predictions`` and the chat's answer):

1. the owner declares a stock the example measures, "PRs Waiting for SAIL": declared, measured by
   the piles instrument's existing series (``workflow_pile_items``), linked or chosen, and nothing
   new recorded (no script, no instrument added);
2. the owner declares the machine that would move it, the Review Nudger: declared from the owner's
   words, its receives real event types on the bus (a ``github.pr.*`` and a ``sail.check.*``),
   scheduled at 09:00, and confirmed with the owner's expectation ("SAILED per day ×1.2"), so a
   prediction is published as the Supervisor's;
3. the owner asks what needs them, what is stuck and why: the answer names the run's open ticket
   keys and a pull request number of the run, and none that is not the run's.

    ORCHESTRA_LIVE_DESIGN=1 uv run --no-sync python -m pytest -q -s tests/live/test_design_live_delivery.py
    #   ORCHESTRA_WORKFLOWS=~/orchestra-workflows  the plugin's checkout (its examples/delivery_live)
    #   ORCHESTRA_LIVE_CAP_USD=5                   stop past this spend (default 5, L3's cap)
    #   ORCHESTRA_LIVE_WAIT_S=2700                 the longest wait for the first SAIL result
    #   ORCHESTRA_LIVE_RUN=live-delivery           the transcripts' directory (<date>/<name>)
    ORCHESTRA_LIVE_DESIGN=rehearse ...             on the example's fakes (--dry-run) and the fake Claude: free

It needs orchestra-workflows and the bridges' kits installed into this environment
(``uv pip install -e ~/orchestra-workflows`` and ``uv pip install --no-deps -e ~/orchestra-bridges``),
a logged-in ``claude`` and ``gh``, and the token files of the example's README. It writes only the
example's own: two "[orchestra live test]" tickets on the SCRUM board and their branches, pull
requests and labels on the test repository; at the end ``demo.py --cleanup`` closes what is left
open. Slack stays off. The transcripts are kept redacted as ``test_design_live.py`` keeps them,
with the run's bus (``demo.py --transcript``) and the tickets' pull requests."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_design_live import FAKE, FORBIDDEN, costs, find, keep, layer, rediscover, say, wrapper

from demo.run import Web, wait_until

LIVE = os.environ.get("ORCHESTRA_LIVE_DESIGN", "")
REHEARSE = LIVE == "rehearse"
WORKFLOWS = Path(os.environ.get("ORCHESTRA_WORKFLOWS", "~/orchestra-workflows")).expanduser()
EXAMPLE = WORKFLOWS / "examples" / "delivery_live"


def _kits() -> bool:
    try:
        import orchestra_bridges.kits  # noqa: F401
        import orchestra_workflows  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    LIVE not in ("1", "rehearse") or (LIVE == "1" and not (shutil.which("claude") and shutil.which("gh")))
    or not EXAMPLE.is_dir() or not _kits(),
    reason="a live run on the delivery example: ORCHESTRA_LIVE_DESIGN=1 (or rehearse), orchestra-workflows' "
           "examples/delivery_live and the bridges' kits installed here, a logged-in claude and gh")
HERE = Path(__file__).resolve().parent
LAUNCH = HERE / "delivery_live_run.py"
MODEL = os.environ.get("ORCHESTRA_LIVE_MODEL", "haiku")
VIA = os.environ.get("ORCHESTRA_LIVE_TOOLS_VIA", "mcp")
CAP_USD = float(os.environ.get("ORCHESTRA_LIVE_CAP_USD", "5"))
WAIT_S = float(os.environ.get("ORCHESTRA_LIVE_WAIT_S", "2700"))
PORTS = range(8300, 8320)                                     # L3's ports: none on the never-list
PILE = "workflow_pile_items"

TURN1 = "I want to see how many pull requests are waiting on SAIL at any moment. Create a stock called PRs Waiting for SAIL."
TURN2 = ("Create a machine called Review Nudger: when a pull request has waited too long for its review or for SAIL, it "
         "reminds the reviewer. It should listen for the GitHub pull request events and the SAIL check results on the bus. "
         "Make it a scheduled task for 9am every day. Then confirm it. I expect SAILED per day ×1.2.")
TURN3 = "What needs me right now, what is stuck, and why?"
KEY = re.compile(r"\bSCRUM-\d+\b")
PR = re.compile(r"(?:#|\bpull/|\bPRs?\s+#?|\bpull requests?\s+#?)(\d+)\b", re.IGNORECASE)


def free_port() -> int:
    for p in PORTS:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
            return p
    raise RuntimeError(f"no free port in {PORTS.start}-{PORTS.stop - 1}")


# ---- the example, running ----

class Run:
    """The example's run directory, its bus and what its launcher printed."""

    def __init__(self, where: Path, out: Path):
        self.where, self.out, self.repo = where, out, where / "repo"
        self.tickets: list[str] = []
        self.token = ""

    def started(self) -> bool:
        text = self.out.read_text(errors="replace") if self.out.exists() else ""
        if m := re.search(r"created and assigned: (.+)", text):
            self.tickets = KEY.findall(m.group(1))
        if m := re.search(r"#token=(\S+)", text):
            self.token = m.group(1)
        return bool(self.token)

    def bus(self) -> list[dict]:
        from orchestra.config import load_project
        from orchestra.events import Events

        ev, evs, offset = Events.for_project(load_project(self.repo)), [], 0
        while True:
            page, offset = ev.after(offset)
            if not page:
                return evs
            evs += page

    def ours(self, kind: str) -> list[dict]:
        """This run's events of ``kind``: about its own tickets (the GitHub poller's first read relays
        the repository's earlier pull requests too, an earlier run's tickets)."""
        return [e for e in self.bus() if e.get("type") == kind and e.get("subject") in self.tickets]

    def prs(self) -> dict[str, int]:
        """Each of the run's tickets' pull request, as github.pr.opened says it."""
        return {e["subject"]: int(e["data"]["pull"]) for e in self.ours("github.pr.opened") if (e.get("data") or {}).get("pull")}

    def merged(self) -> set[str]:
        return {e["subject"] for e in self.ours("github.pr.merged")}

    def sail_results(self) -> set[str]:
        return {e["subject"] for e in self.ours("sail.check.completed")}

    def types(self) -> set[str]:
        return {e.get("type") for e in self.bus()}


def launcher(*demo_args: str) -> list[str]:
    return [sys.executable, str(LAUNCH), "--workflows", str(WORKFLOWS), "--model", MODEL, "--via", VIA, "--", *demo_args]


# ---- the checks, each (ok, why, evidence) ----

def check_stock(web: Web, tools: list[str]) -> tuple[bool, str, dict]:
    sid, s = find(layer(web).get("stocks"), r"sail")
    measure = s.get("measure") or {}
    options = measure.get("options") or []
    ev = {"stock": {k: s.get(k) for k in ("id", "name", "state", "found_as", "linked")}, "chosen": measure.get("chosen"),
          "options": [{k: o.get(k) for k in ("key", "kind", "title", "series", "instrument", "first_points")} for o in options]}
    if not sid:
        return False, "no declared stock named like PRs Waiting for SAIL", ev
    if s.get("state") != "declared":
        return False, f"the stock is {s.get('state')}, not declared", ev
    piles = [o for o in options if o.get("kind") == "existing_series" and str(o.get("series") or "").startswith(PILE)]
    if not piles:
        return False, f"no option names the existing {PILE} series", ev
    if not (s.get("found_as") or measure.get("chosen") in {o.get("key") for o in piles}):
        return False, "the existing series is neither linked nor chosen", ev
    if new := [o["key"] for o in options if o.get("instrument")] + [t for t in tools if t == "add_instrument"]:
        return False, f"something new was recorded for it ({new}): the existing series measures it", ev
    return True, "", ev


def check_nudger(web: Web, run: Run, before: set[str]) -> tuple[bool, str, dict]:
    mid, m = find(layer(web).get("machines"), r"nudger")
    heard = run.types()
    receives = list(m.get("receives") or [])
    pid = (m.get("expected_effect") or {}).get("prediction")
    preds = web.get("/api/dynamics/predictions")
    mine = [p for k in ("predictions", "waiting", "judged") for p in (preds.get(k) or [])
            if isinstance(p, dict) and p.get("subject") == f"machine:{mid}"]
    on_map = [x for x in web.get("/api/machines").get("machines") or [] if isinstance(x, dict) and x.get("id") == mid] \
        if mid else []
    ev = {k: m.get(k) for k in ("id", "title", "kind", "state", "engagement", "receives", "emits", "expected_effect")} | {
        "not_heard": sorted(set(receives) - heard), "on_machines_page": [{k: x.get(k) for k in ("id", "state", "declared")}
                                                                          for x in on_map],
        "predictions": [{k: p.get(k) for k in ("id", "source", "subject", "change", "expect", "status")} for p in mine]}
    if not mid:
        return False, "no machine named like Review Nudger", ev
    if others := [k for k in layer(web).get("machines") or {} if k != mid and k not in before]:
        return False, f"declared more than the one asked for: {others}", ev
    if not receives or set(receives) - heard:
        return False, f"receives {receives}: {sorted(set(receives) - heard)} not heard on the bus", ev
    if not any(t.startswith("github.pr.") for t in receives) or not any(t.startswith("sail.check.") for t in receives):
        return False, f"receives {receives}: no github.pr.* or no sail.check.*", ev
    e = m.get("engagement") or {}
    if e.get("mode") != "scheduled" or e.get("at") not in ("09:00", "9:00"):
        return False, f"engagement is {e}", ev
    edits = (m.get("expected_effect") or {}).get("edits") or []
    if not any(abs(float(x.get("factor") or 0) - 1.2) < 1e-9 for x in edits if isinstance(x, dict)):
        return False, f"no ×1.2 factor among the expected edits {edits}", ev
    if m.get("state") != "declared":
        return False, f"the machine is {m.get('state')}, not confirmed", ev
    if not pid or not mine:
        return False, "no prediction published for it", ev
    p = next((p for p in mine if p.get("id") == pid), mine[-1])
    if p.get("source") != "supervisor" or not re.fullmatch(r"[0-9a-f]{32}", str(p.get("change") or "")):
        return False, f"the prediction is {p.get('source')}'s, about change {p.get('change')!r}", ev
    return True, "", ev


def check_answer(text: str, run: Run) -> tuple[bool, str, dict]:
    prs, done = run.prs(), run.merged()
    open_keys = [k for k in run.tickets if k not in done]
    named_keys, named_prs = set(KEY.findall(text)), {int(n) for n in PR.findall(text)}
    ev = {"tickets": run.tickets, "prs": prs, "merged": sorted(done), "open": open_keys, "named_keys": sorted(named_keys),
          "named_prs": sorted(named_prs)}
    if invented := sorted(named_keys - set(run.tickets)):
        return False, f"names tickets that are not the run's: {invented}", ev
    if invented := sorted(named_prs - set(prs.values())):
        return False, f"names pull requests that are not the run's: {invented}", ev
    if missing := [k for k in open_keys if k not in named_keys]:
        return False, f"leaves out the open tickets {missing}", ev
    if prs and not named_prs:
        return False, "names no pull request number", ev
    if not re.search(r"\b(?:because|waiting|waits|rejected|failed|review|SAIL|CI|check)", text, re.IGNORECASE):
        return False, "gives no reason", ev
    return True, "", ev


# ---- the rehearsal (the fakes, the fake Claude) ----

def rehearsal(turn: int, run: Run) -> list[dict]:
    why = "the owner's words this turn"
    pile = f'{PILE}{{pile="sail_check.waiting"}}'
    types = run.types()
    receives = sorted(t for t in types if t.startswith(("github.pr.", "sail.check.")))
    prs = run.prs()
    return {
        1: [{"tool": "declare_stock", "args": {"name": "PRs Waiting for SAIL", "quote": "Create a stock called PRs Waiting for "
                                                                                         "SAIL.", "reason": why}},
            {"tool": "list_metrics", "args": {}},
            {"tool": "propose_measurements", "args": {"element": "prs_waiting_for_sail", "reason": why, "options": [
                {"label": "items at the sail_check pile", "kind": "existing_series", "series": pile,
                 "how": "the Workflows plugin's piles instrument", "cost": "nothing new"}]}}],
        2: [{"tool": "declare_machine", "args": {
            "name": "Review Nudger", "kind": "universal", "description": "Reminds stale reviewers", "mode": "scheduled",
            "at": "09:00", "receives": receives, "emits": [], "steps": ["Read the PRs waiting", "Remind the reviewer"],
            "invariants": ["never merges"], "uses": ["the bus, read only"], "quote": "Create a machine called Review Nudger",
            "reason": why}},
            {"tool": "read_model", "args": {}},
            {"tool": "declare_expected_effect", "args": {"machine": "review_nudger", "horizon": "7d", "reason": why,
                                                         "expect": [{"stock": "prs_waiting_for_sail", "falls": True}]}},
            {"tool": "confirm", "args": {"element": "machine:review_nudger", "quote": "Then confirm it.", "reason": why}}],
        3: [{"tool": "list_items", "args": {}},
            {"reply": "Needs you: " + ", ".join(f"{k} (#{prs[k]})" if k in prs else k for k in run.tickets)
                      + " are waiting on review or SAIL."}],
    }[turn]


# ---- the run ----

def test_the_owner_designs_on_the_live_delivery_example(tmp_path, monkeypatch):
    from orchestra.config import home, project_key

    port = free_port()
    where, out = tmp_path / "run", tmp_path / "launcher.out"
    live_dir, bin_dir = tmp_path / "claude-live", tmp_path / "bin"
    live_dir.mkdir()
    bin_dir.mkdir()
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    for k in ("ORCHESTRA_EVENTS_URL", "ORCHESTRA_METRICS_URL", "ORCHESTRA_SOURCE", "ORCHESTRA_ROLE", "ORCHESTRA_SESSION_TOKEN"):
        env.pop(k, None)
    if REHEARSE:
        fake, script = bin_dir / "fake_claude", tmp_path / "supervisor.json"
        shutil.copy(FAKE, fake)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        env |= {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-home"), "FAKE_CLAUDE_SUPERVISOR": str(script)}
        binary = str(fake)
    else:
        binary = shutil.which("claude")
    env["CLAUDE_BINARY"] = str(wrapper(bin_dir, binary, live_dir))
    demo_args = ["--tickets", "2", "--port", str(port), "--dir", str(where), "--seed", "1", *(["--dry-run"] if REHEARSE else [])]
    with out.open("ab") as f:
        proc = subprocess.Popen(launcher(*demo_args), stdout=f, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(where / "home"))
    run = Run(where, out)
    log: list[dict] = []
    failures: list[str] = []
    secrets: list[str] = []
    t0 = time.time()
    web = None

    def snapshot(name: str, what) -> None:
        (live_dir / f"{name}.json").write_text(json.dumps(what, indent=1, default=str))

    def turn(n: int, message: str, check) -> dict:
        if (spent := costs(live_dir)["usd"]) > CAP_USD:
            pytest.fail(f"the cost cap: ${spent:.2f} spent before turn {n} (cap ${CAP_USD:.2f}); stopped")
        if REHEARSE:
            script.write_text(json.dumps(rehearsal(n, run)))
        began = time.time()
        reply = say(web, message)
        ok, why, ev = check(reply)
        log.append({"turn": n, "model": MODEL, "seconds": round(time.time() - began), **reply, "ok": ok, "why": why,
                    "state": ev, "cost_so_far": costs(live_dir)})
        snapshot(f"turn{n}", log[-1])
        print(f"\n[turn {n} on {MODEL}, {log[-1]['seconds']} s] ok={ok} {why}\ntools: {reply['tools']}\n"
              f"reply: {reply['text'][:1500]}", flush=True)
        if not ok:
            failures.append(f"turn {n}: {why}")
        return ev

    try:
        wait_until("the example's machines and tickets", lambda: run.started() or proc.poll() is not None, timeout_s=600)
        assert run.started(), out.read_text(errors="replace")[-3000:].replace(run.token or "\0", "[redacted]")
        secrets.append(run.token)
        web = Web(f"http://127.0.0.1:{port}", run.token)
        print(f"\n[the example] tickets {run.tickets} on port {port}; waiting for a SAIL result on each", flush=True)
        try:                                   # each ticket through SAIL once: the piles have moved, one is reworked
            wait_until("a SAIL result on each ticket", lambda: run.sail_results() >= set(run.tickets), timeout_s=WAIT_S)
        except Exception as e:  # noqa: BLE001 - the turns go on with what there is, said in the summary
            print(f"\n[the example] {e}: SAIL results on {sorted(run.sail_results())}", flush=True)
        snapshot("before", {"tickets": run.tickets, "prs": run.prs(), "sail_results": sorted(run.sail_results()),
                            "types": sorted(run.types())})
        snapshot("rediscovered-before", found := rediscover(web))
        print(f"\n[discovery before the turns] {found.get('result') or found.get('error')}", flush=True)
        snapshot("model-before", web.get("/api/dynamics/model"))

        turn(1, TURN1, lambda r: check_stock(web, r["tools"]))
        snapshot("rediscovered-turn2", rediscover(web))
        before = set(layer(web).get("machines") or {})                  # the plugins' own machines, declared already
        turn(2, TURN2, lambda r: check_nudger(web, run, before))
        turn(3, TURN3, lambda r: check_answer(r["text"], run))
        snapshot("api-intended", web.get("/api/intended"))
        snapshot("api-dynamics-model", web.get("/api/dynamics/model"))
        snapshot("api-machines", web.get("/api/machines"))
        snapshot("api-predictions", web.get("/api/dynamics/predictions"))
        snapshot("after", {"tickets": run.tickets, "prs": run.prs(), "merged": sorted(run.merged())})
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)                           # the launcher stops the host, the web and the fakes
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
        state_dir = home() / project_key(run.repo) if run.repo.exists() else where / "home"
        secrets += (live_dir / ".secrets").read_text().split() if (live_dir / ".secrets").exists() else []
        secrets += [t.read_text().strip() for t in (state_dir / "agents" / "control.token", state_dir / "events.token",
                                                     state_dir / "web.token") if t.exists()]
        spent = costs(live_dir)
        summary = {"example": f"orchestra-workflows {_head(WORKFLOWS)} examples/delivery_live", "model": MODEL,
                   "tools_via": VIA, "sent_once": True, "cost": spent, "minutes": round((time.time() - t0) / 60, 1),
                   "tickets": run.tickets, "prs": {k: f"https://github.com/danroblewis/orchestra-live-delivery/pull/{n}"
                                                   for k, n in run.prs().items()} if run.repo.exists() else {},
                   "failures": failures,
                   "turns": [{k: e[k] for k in ("turn", "model", "seconds", "ok", "why", "tools", "text", "error")} for e in log]}
        snapshot("summary", summary)
        name = os.environ.get("ORCHESTRA_LIVE_RUN") or "live-delivery"
        dest = None if REHEARSE else HERE / "transcripts" / (name if "/" in name else f"{datetime.now(UTC):%Y-%m-%d}/{name}")
        if dest and run.repo.exists():                                    # the run's bus and its PRs, redacted by the demo
            subprocess.run(launcher("--transcript", str(live_dir / "bus"), "--dir", str(where)), check=False, env={
                **env, "ORCHESTRATOR_HOME": str(where / "home")}, capture_output=True, timeout=120)
            for f in (live_dir / "bus").glob("*"):
                shutil.move(f, live_dir / f"bus-{f.name}")
        kept = keep(run.repo, live_dir, state_dir, secrets, dest) if run.repo.exists() else []
        if not REHEARSE:                                                  # the example's own, still open: closed
            done = subprocess.run(launcher("--cleanup"), check=False, env=env, capture_output=True, text=True, timeout=300)
            print(f"\n[cleanup] {done.stdout.strip()[-800:]} {done.stderr.strip()[-400:]}", flush=True)
        print(f"\nCOST ${spent['usd']:.4f} over {spent['invocations']} Claude Code turns; usage {spent['usage']}\n"
              f"KEPT {live_dir}" + (f" and {len(kept)} files in {dest}" if dest else ""), flush=True)
    said = " ".join(e["text"] for e in log).lower()
    assert not [p for p in FORBIDDEN if p in said], [p for p in FORBIDDEN if p in said]
    if REHEARSE:                       # the fakes finish in a minute and discovery finds nothing: the plumbing only
        print(f"\n[rehearsal] {failures}", flush=True)
        return
    assert not failures, failures


def _head(repo: Path) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], check=False, cwd=repo, capture_output=True, text=True).stdout.strip()
    except OSError:
        return "?"
