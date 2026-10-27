"""Who fills the task system's roles, as observed on the bus: which machine instances accepted a
worker's or a reviewer's assignment, or announced themselves as one (a source ``worker/<n>`` that
listens for ``task.assigned``), and what each said it is when it started (its
``orchestra.machine.started`` ``kind``: an AI agent, a program, a person). The planners read it in
their wake-ups, so they write tickets for the machines that will take them and never assume the
worker is a program, or an AI agent, because it once was."""

from __future__ import annotations

import time

from orchestra.events import event_time, machine_and_instance

from .builtins import PER_TICKET_ROLES
from .protocol import ACCEPTED, ASSIGNED

STARTED = "orchestra.machine.started"
LOOK_BACK = 5000          # events read at start: the machines that announced before the host came up
KEEP_S = 6 * 3600.0       # an instance not seen taking an assignment for this long is no longer named


class Fillers:
    def __init__(self, events):
        self.kinds: dict[str, str] = {}                   # source -> the kind it announced
        self.seen: dict[tuple[str, str], float] = {}      # (role, instance) -> when it last accepted or announced
        end = events.end()
        evs, _ = events.read(max(0, end - LOOK_BACK), LOOK_BACK)
        self.see(evs)

    def see(self, evs: list[dict]) -> None:
        for ev in evs:
            at = event_time(ev) or time.time()
            if ev.get("type") == STARTED and ev.get("source"):
                self.kinds[ev["source"]] = str(ev.get("kind") or "program")
                role, _ = machine_and_instance(ev["source"])
                if role in PER_TICKET_ROLES and ASSIGNED in (ev.get("listens_for") or []):
                    self.seen[(role, ev["source"])] = at
            elif ev.get("type") == ACCEPTED and ev.get("role") and ev.get("instance"):
                self.seen[(ev["role"], ev["instance"])] = at

    def lines(self, serves: list[str]) -> list[str]:
        """Plain statements: the roles the AI agents here fill, and the instances seen taking each
        role, with what they are."""
        now = time.time()
        out = [f"The AI agents here fill: {', '.join(serves) or 'no task role'} (`[agents] serves`)."]
        roles: dict[str, list[str]] = {}
        for (role, inst), at in sorted(self.seen.items()):
            if now - at < KEEP_S:
                kind = self.kinds.get(inst)
                what = "an AI agent" if kind == "agent" else f"a {kind}" if kind else "not announced"
                roles.setdefault(role, []).append(f"{inst} ({what})")
        for role, who in roles.items():
            out.append(f"Seen filling the {role} role: {', '.join(who)}.")
        if not roles:
            out.append("No machine has been seen filling a role yet.")
        return out
