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
   recorded as removed (the journal) and drawn gone on the map (`/api/machines`, E2): a drop
   missing from either fails the turn;
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
`.orchestra/roles/supervisor.json`, which wins over the built-in on its name) on Haiku, with no duties of its own (its review round once a day), so the only turns it takes are the
owner's. The confirm turn carries the owner's expectation (notes files rise, the commit rate up 1.2
times), so the prediction has something to say.

Each turn's message is sent once and judged as it was answered: a failed turn is recorded and the
run goes on to the next (re-sending a failed turn confused the model into declaring an extra
machine, A3's first runs). The run stops at the cost cap.

It costs money and needs a logged-in ``claude``, so it runs only when asked:

    ORCHESTRA_LIVE_DESIGN=1 uv run python -m pytest -q -s tests/live/test_design_live.py
    #   ORCHESTRA_LIVE_TOOLS_VIA=bash      only one way of calling the tools (default: mcp,bash)
    #   ORCHESTRA_LIVE_CAP_USD=10          stop past this spend (default 10, the goal's cap)
    ORCHESTRA_LIVE_DESIGN=rehearse ...     the same run with the fake Claude, scripted: free

The rehearsal also runs in every suite (tests/e2e/test_design_rehearsal_e2e.py). The scenario
itself, its checks and what it keeps are in ``live_helpers.py``.

Claude Code's stream for every session (a wrapper tees it) and the Claude Code transcripts of the
sessions in the project are kept in the project's state directory (``live/``), with the API's
answers after each turn; a live run copies them, every token redacted, to
``tests/live/transcripts/<date>/<tools_via>/``. The cost is Claude Code's own (``total_cost_usd``
of each ``result`` event)."""

from __future__ import annotations

import os
import shutil

import pytest
from live.live_helpers import VIAS, scenario

LIVE = os.environ.get("ORCHESTRA_LIVE_DESIGN", "")
pytestmark = pytest.mark.skipif(LIVE not in ("1", "rehearse") or (LIVE == "1" and not shutil.which("claude")),
                                reason="a live run on Haiku: set ORCHESTRA_LIVE_DESIGN=1 with a logged-in claude on the "
                                       "PATH (or =rehearse for the fake)")


@pytest.mark.parametrize("via", VIAS)
def test_the_owner_designs_notes_files_and_the_notes_improver_live(via, tmp_path, monkeypatch):
    scenario(via, tmp_path, monkeypatch, rehearse=LIVE == "rehearse")
