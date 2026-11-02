"""A scaffold handed to the Coordinator lands (integrate_handoff.py), end to end on the agents' demo
with the dummy harness and the real task system and gate: the owner asks the Supervisor to connect
Jenkins, which no kit has a template for, so integrate_system scaffolds the generic http kit's
poller, moves it out of the owner's checkout and hands it to the Coordinator; the Coordinator's
built-in rule plans one ticket from the requirements; a scripted worker does what they say (copies
the scaffold into the same relative path in its worktree and commits it), the reviewer approves, and
the gate lands it on main. Then the owner's yes approves it, and what runs is the landed script."""

import os
import stat
import subprocess
import tomllib

import pytest
from conftest import free_port
from orchestra.config import load_project
from test_demo_e2e import chat, git, settled, states
from test_dummy_demo_e2e import GUARD, KEYS

from demo.run import Web, make_project, start, stop, wait_for, wait_until, web_token

RULES = r"""# (this test's rules)
[[rule]]
role = "supervisor"
prompt = "Connect Jenkins"
steps = [{ tool = "integrate_system", args = { system = "jenkins", wants = "builds started and finished", base_url = "https://ci.acme.test", quote = "Connect Jenkins so I can see builds", reason = "the owner asked to connect Jenkins" }, keep = "i" }]
reply = "Scaffolded {kept.i.integration} at {kept.i.dir}; handed to the Coordinator as {kept.i.request}."

[[rule]]
role = "supervisor"
prompt = "Yes, you may read Jenkins"
steps = [{ tool = "approve_integration", args = { name = "jenkins_poller", token_env = "JENKINS_TOKEN", quote = "Yes, you may read Jenkins", reason = "the owner approved reading Jenkins" }, keep = "a" }]
reply = "Approved: clearance {kept.a.clearance}."

[[rule]]
role = "worker"
prompt = 'cp -R (?P<src>\S+) (?P<rel>\.orchestra/machines/[a-z0-9_]+)'
checkout = true
steps = [
  { run = "mkdir -p .orchestra/machines && cp -R {match.src} {match.rel}" },
  { commit = "{ticket}: the collector, adapted" },
  { tool = "report_ticket", args = { state = "READY_FOR_REVIEW", summary = "Copied the scaffold into {match.rel} and committed it." } },
]
reply = "Ready for review."

"""


@pytest.fixture
def demo(tmp_path, monkeypatch):
    home, logs, bins = tmp_path / "home", tmp_path / "logs", tmp_path / "bin"
    logs.mkdir()
    bins.mkdir()
    for product in ("claude", "pi", "codex"):
        b = bins / product
        b.write_text(f'#!/bin/sh\necho "{product} $*" >> {logs / "products.log"}\nexit 1\n')
        b.chmod(b.stat().st_mode | stat.S_IEXEC)
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    env = {"ORCHESTRATOR_HOME": str(home), "NO_NETWORK_LOG": str(logs / "network.log"),
           "PYTHONPATH": os.pathsep.join(filter(None, [str(GUARD), os.environ.get("PYTHONPATH")])),
           "PATH": os.pathsep.join([str(bins), os.environ.get("PATH", "")]),
           "CLAUDE_BINARY": str(bins / "claude"), "PI_BINARY": str(bins / "pi"), "CODEX_BINARY": str(bins / "codex")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    target = make_project(tmp_path / "project", dummy=True)
    script = target / "dummy.toml"
    script.write_text(RULES + script.read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-qam", "the test's rules"],
                   cwd=target, check=True)
    port = free_port()
    procs = start(target, port, env=env)
    try:
        web = Web(f"http://127.0.0.1:{port}", web_token(target, env))
        wait_for(web, procs)
        yield web, target
    finally:
        stop(procs)


def said(lines: list[dict]) -> str:
    assert lines[-1]["type"] == "done" and not lines[-1].get("error"), lines
    return " ".join(ln["text"] for ln in lines if ln["type"] == "assistant_end")


def test_a_scaffold_handed_to_the_coordinator_lands_on_main_and_is_approved_there(demo):
    web, target = demo
    rel = ".orchestra/machines/jenkins_poller"
    draft = load_project(target).state_dir / "machines" / "jenkins_poller"
    reply = said(chat(web, "Connect Jenkins so I can see builds"))
    assert f"at {draft};" in reply and "handed to the Coordinator as r-" in reply, reply
    assert not (target / rel).exists() and (draft / "jenkins_poller.py").is_file()     # out of the owner's checkout
    assert not git(target, "status", "--porcelain", "--untracked-files=all").strip()

    wait_until("the Coordinator's ticket", lambda: settled(web), timeout_s=240)
    assert set(states(web).values()) == {"DONE"}, states(web)
    landed = git(target, "ls-tree", "-r", "--name-only", "main")
    assert f"{rel}/jenkins_poller.py" in landed and f"{rel}/poller.toml" in landed
    assert (target / rel / "jenkins_poller.py").is_file()                               # the checkout followed main

    reply = said(chat(web, "Yes, you may read Jenkins, metadata only, the token is in JENKINS_TOKEN"))
    assert "clearance approved" in reply, reply
    assert tomllib.loads((target / rel / "poller.toml").read_text())["clearance"] == "approved"
    assert not draft.exists()
