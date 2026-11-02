"""The declared layer for the live task-loop demo (docs/proposals/intended-model.md, frames D1, E1/E2,
E4), ~/orchestra-live-demo/seed.py without what names tickets: the owner's Features stock with the
Supervisor's measurement options (no series yet; drawn near the commits on main), the Supervisor's
proposed feature_count_improver (unconfirmed; it reads the floor's hand-offs, not tickets), and the
external machine sail, declared and attached to an instrument, never heard. Idempotent: skips when
the Features stock is already declared.
    uv run --no-sync --project ~/orchestra-agents-wt-demo python demo/seed_task_loop.py <project>"""
import sys
from pathlib import Path

from orchestra.config import load_project
from orchestra.overlay import OVERLAY

project = load_project(Path(sys.argv[1]).expanduser().resolve())
store = project.intended
# the Workflows plugin declares its definitions' machines and events first (d1 on a fresh project):
# seeded means the owner's Features stock is there
if "features" in store.read()["layer"]["stocks"]:
    print(f"already seeded (d{store.current_version()})")
    sys.exit(0)

OWNER = {"author": "owner", "source": "supervisor", "turn": "sessions/supervisor/41", "message": "msg_0193",
         "quote": "I think we need to start tracking features, then, because we currently aren't. I'm going to create a Features stock."}
store.apply([
    {"op": "declare_stock", "id": "features", "name": "Features", "near": 'git_branch_commits{branch="main"}'},
    {"op": "record_measurement", "part": "stock", "id": "features", "options": [
        {"key": "a", "title": "passing acceptance tests on main", "how": "pytest tests/acceptance --junitxml, counted",
         "kind": "script collector", "instrument": "features.acceptance", "simulated": False, "cost": "a CI run per commit",
         "caveat": "counts tests, not features", "clearance": "none", "provenance": "free text"},
        {"key": "b", "title": "features listed in CHANGELOG.md", "how": "count the '### Added' entries on main",
         "kind": "script collector", "instrument": "features.changelog", "simulated": False, "cost": "none",
         "caveat": "lags the work; needs a changelog kept", "clearance": "none", "provenance": "free text"},
        {"key": "c", "title": "a simulated ramp", "how": "a made-up series until a real one records", "kind": "simulated",
         "instrument": "features.sim", "simulated": True, "cost": "none", "caveat": "not the system: drawn as simulated"}]}],
    OWNER)

SUPERVISOR = {"author": "supervisor", "source": "supervisor", "reason": "the owner asked for a feedback loop",
              "quote": "create a new Universal Machine called Feature Count Improver"}
EMIT = "work.improvement_proposed"
store.apply([
    {"op": "propose_machine", "id": "feature_count_improver", "title": "Feature Count Improver",
     "description": "Finds why items are rejected in review or fail the merge gate and proposes work to remove those causes",
     "tags": ["features", "feedback loop", "delivery"], "kind": "universal", "managed": "orchestra",
     "engagement": {"mode": "continuous"}, "receives": ["offer.finished", "attention.needed"], "emits": [EMIT],
     "reads": ["features"],
     "spec": {"invariants": ["never changes code itself; it proposes work", "reads the Features series, never writes it",
                             "one proposal per cause, with evidence"],
              "steps": ["On each finished offer or attention, read the item's history: its reviews and its gate runs.",
                        "Say why it fell short, if it did: rejected in review, failed the gate, sent back for rework.",
                        "Group the causes across items; keep the evidence (items, gate logs).",
                        "For a cause seen at least twice, write one proposal that would remove it.",
                        f"Publish it as {EMIT}, with its evidence."],
              "uses": ["each item's history on the Items page · reads", "review outcomes and the merge gate's logs · reads",
                       "the coordinator · sends it proposals, through what it emits"]},
     "guessed": ["receives[0]", "receives[1]"]},
    {"op": "declare_event", "type": EMIT, "description": "one idea to remove a cause of rejected work, with its evidence"},
    {"op": "set_expected_effect", "machine": "feature_count_improver", "horizon_s": 604800,
     "edits": [{"stock": "features", "rises": True}]}],
    SUPERVISOR)

# SAIL's stand-in for the company's Prometheus: an instrument printing sail_* series per runner
# (runner-3 down, queue depths varying), added to the private overlay through config_edit.
SAIL_SH = ('q1=$((RANDOM % 6)); q2=$((RANDOM % 4)); '
           'for r in "runner-1 1 $q1" "runner-2 1 $q2" "runner-3 0 0"; do set -- $r; '
           'echo "sail_up{job=\\"sail\\",runner=\\"$1\\"} $2"; echo "sail_queue_depth{job=\\"sail\\",runner=\\"$1\\"} $3"; done')
project.config_edit.add_entry("collect", {"name": "company_prom", "command": ["bash", "-c", SAIL_SH], "every": "15s"}, target=OVERLAY)
project.reload_config()

WHO = {"author": "owner", "source": "supervisor", "turn": "sessions/supervisor/42", "message": "msg_0201",
       "quote": "we merge through SAIL, the company's landing queue; it runs three runners"}
store.apply([
    {"op": "propose_machine", "id": "sail", "title": "SAIL", "description": "the company's landing queue", "kind": "programmatic",
     "managed": "external", "engagement": {"mode": "continuous"}, "spec": {"source": "sail"}},
    {"op": "confirm", "part": "machine", "id": "sail"},
    {"op": "propose_attachment", "machine": "sail", "instrument": "company_prom",
     "match": {"prefix": "sail_", "labels": {"job": "sail"}}, "instance_label": "runner"}], WHO)
store.apply([{"op": "confirm", "part": "attachment", "id": "sail_company_prom"}], WHO)
print(f"seeded: d{store.current_version()}")
