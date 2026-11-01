"""What the floor's tests share: a confirmed Universal Machine in a test project (its role, its
agent and its machine spec with ``role``), and a scripted floor that publishes offers on the
project's bus as ``coordinator/dispatch``, the way orchestra-workflows' dispatch does."""

from __future__ import annotations

import secrets
from pathlib import Path

from orchestra.events import Events as OrchestraEvents

from orchestra_agents.org import AgentDef
from orchestra_agents.org_store import RepoOrgStore
from orchestra_agents.roles import RepoRoleStore, RoleDef


def universal_machine(project, name: str = "triage", role: str = "triager", *, in_state_dir: bool = False) -> Path:
    """The machine as confirm leaves it (machine-design.md §2.4): a role, an agent of the
    machine's name, and its spec naming the floor role it serves."""
    RepoRoleStore(project).save(RoleDef(name=f"{name}_job", description="triages", tools=["escalate"]), "Triage what you are handed.")
    RepoOrgStore(project).save_agent(AgentDef(name=name, roles=[f"{name}_job"], kind="ad_hoc", session="ephemeral"))
    d = (project.state_dir / "machines" / name) if in_state_dir else (project.repo_root / ".orchestra" / "machines" / name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "machine.toml").write_text(f'name = "{name}"\nengagement = "ad_hoc"\nrole = "{role}"\n')
    (d / "role.md").write_text("Read the brief and say what it is.\n")
    return d


class ScriptedFloor:
    """Offers on the bus as the floor's dispatch makes them, and a reader of what came back."""

    def __init__(self, project):
        self.events = OrchestraEvents.for_project(project.orchestra, source="coordinator/dispatch")
        self.project = project

    def offer(self, role: str, item: str, brief: str = "do it", *, step: str = "work", outcomes=(), to=None,
              resources=(), heartbeat_s: float = 1.0, lease_s: float = 30.0) -> str:
        oid = f"{item}.{step}.{secrets.token_hex(4)}"
        self.events.publish("offer.made", {
            "offer": oid, "role": role, "item": item, "workflow": "wf", "step": step, "to": to, "lease_s": lease_s,
            "heartbeat_s": heartbeat_s, "brief": brief, "resources": list(resources), "outcomes": list(outcomes),
            "ends": None}, subject=item)
        return oid

    def withdraw(self, oid: str, item: str, reason: str = "a withdraw request") -> None:
        self.events.publish("offer.withdrawn", {"offer": oid, "reason": reason, "instance": None}, subject=item)

    def as_machine(self, source: str) -> OrchestraEvents:
        return OrchestraEvents.for_project(self.project.orchestra, source=source)

    def of(self, oid: str, type: str | None = None) -> list[dict]:
        evs, _ = self.events.after(0)
        return [e for e in evs if (e.get("data") or {}).get("offer") == oid and (type is None or e["type"] == type)]
