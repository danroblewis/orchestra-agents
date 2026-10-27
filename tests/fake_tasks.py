"""A task system for the agents' tests: it answers requests on the bus as orchestra-tasking's
desk does (``request.done`` with the result, ``request.refused`` with why), for milestones and
tickets only, kept in memory. It speaks the protocol (orchestra_agents.protocol), nothing else."""

from __future__ import annotations

import threading

from orchestra_agents.events import Events
from orchestra_agents.protocol import DONE, QUERY, REFUSED, REQUESTS

OPS = {v: k for k, v in REQUESTS.items()}


class FakeTasks:
    def __init__(self, project):
        self.events = Events.for_project(project, source="task_scheduler")
        self.milestones: dict[str, dict] = {}
        self.tickets: dict[str, dict] = {}
        self.seen: list[dict] = []
        self._stop = threading.Event()
        self._offset = self.events.end()
        self._t = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=5)

    def _run(self):
        while not self._stop.wait(0.05):
            evs, self._offset = self.events.after(self._offset)
            for ev in evs:
                op = ev.get("op") if ev.get("type") == QUERY else OPS.get(ev.get("type"))
                if not op:
                    continue
                self.seen.append({"op": op, "by": ev.get("by"), "args": ev.get("args") or {}})
                try:
                    result = self.answer(op, ev.get("args") or {})
                    self.events.publish(DONE, {"request": ev.get("request"), "result": result})
                except ValueError as e:
                    self.events.publish(REFUSED, {"request": ev.get("request"), "error": str(e)})

    def answer(self, op: str, a: dict) -> dict:
        if op == "create_milestone":
            m = {"id": f"M{len(self.milestones) + 1}", "name": a.get("name", "")}
            self.milestones[m["id"]] = m
            return {"ok": True, "milestone": m}
        if op == "create_ticket":
            if a.get("milestone_id") not in self.milestones:
                raise ValueError(f"no milestone {a.get('milestone_id')}")
            t = {"id": f"T-{len(self.tickets) + 1}", "title": a.get("title", ""), "state": "OPEN"}
            self.tickets[t["id"]] = t
            return {"ok": True, "ticket": t}
        if op == "list_tickets":
            return {"ok": True, "tickets": list(self.tickets.values())}
        if op == "snapshot":
            counts: dict[str, int] = {}
            for t in self.tickets.values():
                counts[t["state"]] = counts.get(t["state"], 0) + 1
            return {"ok": True, "snapshot": {"counts": counts, "tickets": list(self.tickets.values()),
                                             "milestones": list(self.milestones.values()), "capacity": {},
                                             "pipeline": {"paused": False}, "assignments": []}}
        raise ValueError(f"the fake task system has no {op}")
