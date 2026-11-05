"""An agent's own event bindings (bindings.py; the owner, 2026-11-04: "I really do want agents to be
able to remove their own event bindings and add more event bindings"): bind and unbind for the
Supervisor and for another agent, the Supervisor changing another's, a built-in unbound, the owner's
channels and the crash-loop guard refused, a busy event allowed with a warning, offer.alive refused
unfiltered, the cap of its own, revert, the announcement made again, and the host waking, or not,
after the change; and a designed machine's, kept in the state directory."""

from __future__ import annotations

import json
import subprocess

import pytest
from conftest import FakeHarness, agents_project

from orchestra_agents import notices, protocol, state_machines, tooling
from orchestra_agents.events import Events
from orchestra_agents.host import AgentHost
from orchestra_agents.machine_spec import load
from orchestra_agents.org_store import all_agents
from orchestra_agents.side_branch import SIDE
from orchestra_agents.store import AgentStore, StoreError

WHY = "the owner asked for it in the chat today"


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False).stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(protocol, "WAIT_S", 0.3)
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    return p


@pytest.fixture
def host(project):
    store = AgentStore(project, Events.for_project(project, source="supervisor"))
    h = AgentHost(project, store, store.events)                  # writes the Supervisor's spec
    h.harness = FakeHarness(reply="ok")
    h._refresh_org(store.runtime()["agent_state"])
    return h


def call(h, tool, role="supervisor", **args):
    tooling.check_args(tool, args)
    return tooling.handle(h.project, h.store, tool, args, role=role)


def tick(h):
    h._refresh_org(h.store.runtime()["agent_state"])


def wakes(h, name) -> list[str]:
    return all_agents(h.project)[name].wake_on


def test_the_supervisor_binds_and_unbinds_its_own_and_lists_them(host):
    got = call(host, "bind_event", event="ci.check.failed", reason=WHY)
    assert got["changed"] and got["commit"] and got["change"] == "bind" and "ci.check.failed" in wakes(host, "supervisor")
    assert "bind event ci.check.failed" in git(host.project.repo_root, "log", "-1", "--format=%s%n%b", SIDE)
    spec = load(host.project, "supervisor")
    assert [b.event for b in spec.bindings] == ["ci.check.failed"] and WHY in spec.files["ledger.md"]
    rows = {r["event"]: r for r in call(host, "list_bindings")["bindings"]}
    assert rows["ci.check.failed"]["from"] == "its own binding" and "gathered" in rows["ci.check.failed"]["wakes"]
    assert rows["attention.needed:supervisor"]["from"] == "built in (orchestra-agents)"
    assert rows["orchestra.intent.requested"]["wakes"].startswith("at once")
    assert call(host, "unbind_event", event="ci.check.failed", reason=WHY)["changed"]
    assert "ci.check.failed" not in wakes(host, "supervisor") and not load(host.project, "supervisor").bindings
    assert call(host, "bind_event", event="gate.rejected", reason=WHY)["changed"]
    assert call(host, "bind_event", event="gate.rejected", reason=WHY)["changed"] is False     # already


def test_it_unbinds_a_built_in_and_binds_it_again(host):
    got = call(host, "unbind_event", event="attention.needed", reason=WHY)
    assert got["changed"] and "attention.needed:supervisor" not in wakes(host, "supervisor")
    assert [b.event for b in load(host.project, "supervisor").unbound] == ["attention.needed"]
    rows = {r["event"]: r for r in call(host, "list_bindings")["bindings"]}
    assert rows["attention.needed:supervisor"]["wakes"] == "unbound"
    for t in ("orchestra.process.gave_up", "work.planned"):
        assert call(host, "unbind_event", event=t, reason=WHY)["changed"]
    assert call(host, "bind_event", event="attention.needed:supervisor", reason=WHY)["changed"]   # a filtered one back
    assert "attention.needed:supervisor" in wakes(host, "supervisor")


@pytest.mark.parametrize("event", ["orchestra.intent.requested", "orchestra.intent.requested:machine:x", "agent.crash_loop", "chat"])
def test_the_owner_s_channels_and_the_crash_loop_guard_are_never_unbound(host, event):
    with pytest.raises(StoreError, match="No agent unbinds the owner's own channels .* or the crash-loop guard"):
        call(host, "unbind_event", event=event, reason=WHY)
    assert "orchestra.intent.requested" in wakes(host, "supervisor")


def test_a_busy_event_is_allowed_with_a_warning_and_a_heartbeat_unfiltered_is_refused(host):
    for i in range(40):
        host.store.events.publish("ci.check.completed", {"n": i}, source="ci")
    got = call(host, "bind_event", event="ci.check.completed", reason=WHY)
    [w] = got["warnings"]
    assert got["changed"] and "40 times in the last hour" in w and "every 1h" in w and "attention_every" in w
    assert "offer.withdrawn" in call(host, "bind_event", event="offer.claimed", reason=WHY)["warnings"][0]
    with pytest.raises(StoreError, match="heartbeat.*refused unfiltered"):
        call(host, "bind_event", event="offer.alive", reason=WHY)
    assert call(host, "bind_event", event="offer.alive:item-1", reason=WHY)["warnings"]      # one lease: allowed


def test_another_agent_changes_its_own_and_the_supervisor_changes_another_s(host):
    got = call(host, "bind_event", role="coordinator", event="gate.rejected", reason=WHY)
    assert got["changed"] and got["agent"] == "coordinator" and "gate.rejected" in wakes(host, "coordinator")
    assert "at once" in call(host, "list_bindings", role="coordinator")["bindings"][-1]["wakes"]
    with pytest.raises(StoreError, match="you change your own bindings"):
        call(host, "bind_event", role="coordinator", agent="supervisor", event="gate.landed", reason=WHY)
    assert call(host, "unbind_event", agent="coordinator", event="attention.needed", reason=WHY)["changed"]
    assert "attention.needed:coordinator" not in wakes(host, "coordinator")
    assert "attention.needed:supervisor" in wakes(host, "supervisor")                 # the Supervisor's untouched
    spec = load(host.project, "coordinator")
    assert spec.root == ".orchestra/machines"
    assert [b.by for b in spec.unbound] == ["supervisor"] and [b.by for b in spec.bindings] == ["coordinator"]


def test_binding_changes_have_a_cap_of_their_own(host, monkeypatch):
    toml = host.project.repo_root / "orchestra.toml"
    toml.write_text(toml.read_text().rstrip() + "\nbinding_changes_per_day = 2\nspec_changes_per_day = 1\n")
    host.project.reload_config()
    for t in ("gate.rejected", "gate.landed"):
        call(host, "bind_event", event=t, reason=WHY)
    with pytest.raises(StoreError, match="binding_changes_per_day"):
        call(host, "bind_event", event="gate.conflict", reason=WHY)
    assert host.store.agent_state("supervisor").get("spec_changes") in (None, [])        # not the spec's cap
    assert call(host, "bind_event", role="coordinator", event="gate.landed", reason=WHY)["changed"]   # per agent


def test_revert_change_undoes_a_binding_commit_mine_or_another_s(host):
    sha = call(host, "unbind_event", event="attention.needed", reason=WHY)["commit"]
    other = call(host, "bind_event", agent="coordinator", event="gate.rejected", reason=WHY)["commit"]
    assert call(host, "revert_change", commit=sha, reason="the owner wants it back after all")["changed"]
    assert "attention.needed:supervisor" in wakes(host, "supervisor") and not load(host.project, "supervisor").unbound
    assert call(host, "revert_change", commit=other, reason="the owner wants it back after all")["changed"]
    assert "gate.rejected" not in wakes(host, "coordinator")


def test_the_agent_is_announced_again_with_its_new_bindings(host):
    host._announce("coordinator", "coordinator")
    call(host, "bind_event", role="coordinator", event="gate.rejected", reason=WHY)
    tick(host)
    ann = [e for e in host.store.events.read(0, 2000)[0] if e["type"] == "orchestra.machine.started" and e["source"] == "coordinator"]
    assert "gate.rejected" not in ann[0]["listens_for"] and "gate.rejected" in ann[-1]["listens_for"]
    assert host.store.agent_state("coordinator").get("bindings_changed") is False
    n = len(ann)
    tick(host)                                                    # nothing changed since: no announcement
    assert len([e for e in host.store.events.read(0, 2000)[0] if e["type"] == "orchestra.machine.started"
                and e["source"] == "coordinator"]) == n


def test_the_host_wakes_or_not_after_the_change(host):
    call(host, "unbind_event", event="attention.needed", reason=WHY)
    call(host, "bind_event", event="ci.check.failed", reason=WHY)
    call(host, "bind_event", role="coordinator", event="gate.rejected", reason=WHY)
    tick(host)
    ev = host.store.events
    ev.publish("attention.needed", {"to": "supervisor", "reason": "x"}, source="workflows/dispatch", subject="item-1")
    ev.publish("ci.check.failed", {"n": 1}, source="ci", subject="main")
    ev.publish("gate.rejected", {"ticket": "t-1"}, source="gate")
    host._wake_on_events()
    queued = host.store.runtime()["wakeups"]
    sup = [n["type"] for n in notices.held(host.store)] + [w["reason"] for w in queued if w["target"] == "supervisor"]
    assert len(sup) == 1 and "ci.check.failed" in sup[0]          # the unbound built-in woke nothing; the new one did
    assert [w["reason"].split()[1] for w in queued if w["target"] == "coordinator"] == ["gate.rejected"]


def _designed(project, mid="triage_bot"):
    m = {"id": mid, "title": "Triage bot", "description": "Triages failed checks.", "kind": "universal",
         "engagement": {"mode": "continuous"}, "receives": ["ci.check.failed"], "emits": [], "reads": [], "spec": {}}
    state_machines.write(project, m)
    return mid


def test_a_designed_machine_changes_its_own_kept_in_the_state_directory(host):
    mid = _designed(host.project)
    tick(host)
    assert wakes(host, mid) == ["ci.check.failed"]
    rows = call(host, "list_bindings", role=mid)["bindings"]
    assert rows[0]["from"].startswith("its declared receives")
    got = call(host, "bind_event", role=mid, event="gate.rejected", reason=WHY)
    assert got["changed"] and got["commit"] is None and got["revert"].startswith("bind-") and "declared receives are unchanged" in got["note"]
    assert call(host, "unbind_event", role=mid, event="ci.check.failed", reason=WHY)["changed"]
    assert wakes(host, mid) == ["gate.rejected"]
    kept = json.loads((host.project.state_dir / "bindings" / f"{mid}.json").read_text())
    assert [b["event"] for b in kept["bindings"]] == ["gate.rejected"] and len(kept["history"]) == 2
    assert git(host.project.repo_root, "log", "--all", "--format=%s").count("bind event") == 0     # never committed
    state_machines.write(host.project, {"id": mid, "title": "Triage bot", "description": "Triages failed checks, again.",
                                        "kind": "universal", "engagement": {"mode": "continuous"},
                                        "receives": ["ci.check.failed"], "emits": [], "reads": [], "spec": {}})
    assert wakes(host, mid) == ["gate.rejected"]                   # a confirm that writes it again keeps them
    last = kept["history"][-1]["id"]
    assert call(host, "revert_change", commit=last, reason="the owner wants the failed checks back")["changed"]
    assert wakes(host, mid) == ["ci.check.failed", "gate.rejected"]
    state_machines.remove(host.project, mid)
    assert not (host.project.state_dir / "bindings" / f"{mid}.json").exists()


def test_the_prompt_says_the_agent_owns_its_bindings(host):
    from orchestra_agents import prompting
    call(host, "unbind_event", event="orchestra.process.gave_up", reason=WHY)
    text = prompting.agent_prompt(host.project, all_agents(host.project)["supervisor"])
    assert "What wakes you is yours" in text and "No longer woken by `orchestra.process.gave_up`" in text
