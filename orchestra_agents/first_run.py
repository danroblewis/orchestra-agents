"""The project's first run: when the host starts on a project that is not set up, it wakes the
Supervisor once to set it up (the owner, 2026-10-28: "I want the Supervisor to set up my
environment. That may be the first thing I want to do.").

Not set up means all of: the configuration (orchestra.toml with the owner's overlay) has no
``[project]`` (the task system's) and no instrument (``[[collect]]``, ``[[scrape]]``,
``[[sources]]``); and the Supervisor's record has no setup that ran. A setup that ran on the dummy
harness, which cannot read the repository, does not count once a real harness runs the agents.

The wake-up gives the goal, not steps; the Supervisor's prompt (``prompts/operator.md``, "Setting
up the project") says what setting up means, and the guides (``guides.py``) say how each setting
is chosen. The record is the Supervisor's agent state, ``setup``: ``{ran_at, harness}`` once the
setup turn ended; the same goal, asked for in the chat ("set up this project", "re-check the
setup"), is the Supervisor's prompt's alone and records nothing."""

from __future__ import annotations

import time

from .protocol import SUPERVISOR

MARK = "[First run: set up this project]"
INSTRUMENTS = ("collect", "scrape", "sources")

GOAL = MARK + """ This project has not been set up for Orchestra: its configuration has no [project] and no
instruments, and no setup has run. The human installed Orchestra and its plugins and has not configured
anything; they want you to do it. The goal: understand this repository, then configure Orchestra and the
installed plugins for it, as your prompt's "Setting up the project" says. Read the guides first
(list_guides, then read_guide for what you will set), explore the repository read-only, make each setting
that is plainly right with your setup tools (each with its reason), check that what you declared works,
and leave the human one short message: what you set and why, and the few choices that are theirs, each
with your recommendation."""


def configured(raw: dict) -> bool:
    """Whether the configuration shows the project was set up: the task system's ``[project]``, or
    any instrument."""
    return bool(raw.get("project")) or any(raw.get(k) for k in INSTRUMENTS)


def needs_setup(raw: dict, state: dict, harness: str) -> bool:
    """Whether the host should wake the Supervisor to set the project up (``state``: the
    Supervisor's agent state)."""
    if configured(raw):
        return False
    ran = state.get("setup") or {}
    return not ran.get("ran_at") or (ran.get("harness") == "dummy" and harness != "dummy")


class FirstRun:
    """Offers the setup once per host, and records it when the Supervisor's turn on it ends."""

    def __init__(self, host):
        self.host = host
        self.offered = False

    def offer(self) -> bool:
        h = self.host
        if self.offered or not needs_setup(h.project.orchestra.config.raw, h.store.agent_state(SUPERVISOR), h.cfg.harness):
            return False
        self.offered = True
        h.store.add_wakeup(SUPERVISOR, GOAL)
        h.log("the project is not set up: the Supervisor is woken to set it up")
        return True

    def ended(self, reasons: list[str], ok: bool) -> None:
        """A Supervisor wake-up ended; when it was the setup's and it did not fail, the setup ran."""
        if ok and any(r.startswith(MARK) for r in reasons):
            self.host.store.set_agent_state(SUPERVISOR, setup={"ran_at": time.time(), "harness": self.host.cfg.harness})
