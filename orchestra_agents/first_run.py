"""The project's first run: when the host starts on a project that is not set up, it wakes the
Supervisor once to set it up (the owner, 2026-10-28: "I want the Supervisor to set up my
environment. That may be the first thing I want to do.").

Not set up means all of: the configuration (orchestra.toml with the owner's overlay) has no
``[project]`` (the task system's) and no instrument (``[[collect]]``, ``[[scrape]]``,
``[[sources]]``); and the Supervisor's record has no setup that ran. A setup that ran on the dummy
harness, which cannot read the repository, does not count once a real harness runs the agents.

The wake-up gives the goal, not steps; the Supervisor's `setup` skill (``prompts/skills/setup.md``, "Setting
up the project") says what setting up means, and the guides (``guides.py``) say how each setting
is chosen. The record is the Supervisor's agent state, ``setup``: ``{ran_at, harness}`` once the
setup turn ended; the same goal, asked for in the chat ("set up this project", "re-check the
setup"), is the Supervisor's prompt's alone and records nothing.

**With no ``[[sources]]``** (the owner, 2026-11-04: "a short discussion with the supervisor to provide
key details and then whatever happens after that should be an automated exploration and set up by
the supervisor"), the wake-up adds ``CHECKLIST``: the Supervisor's first message asks it, all in one
message, before it reads any store: which systems (stores) to read, where each one's token file is
(a path or a variable's name, never the token), which part of each store it may read (the ``match``)
and the wider part it may survey (``explore``), the caps (series, requests a second and a day, what
its own rounds may spend), and that everything stays read-only. Then it works from the owner's
answer without asking again, within it. The record notes the checklist was asked (``setup.asked``)."""

from __future__ import annotations

import time

from .protocol import SUPERVISOR

MARK = "[First run: set up this project]"
INSTRUMENTS = ("collect", "scrape", "sources")

GOAL = MARK + """ This project has not been set up for Orchestra: its configuration has no [project] and no
instruments, and no setup has run. The human installed Orchestra and its plugins and has not configured
anything; they want you to do it. The goal: understand this repository, then configure Orchestra and the
installed plugins for it, as your `setup` skill says (read_skill setup first). Read the guides
(list_guides; then read_guide for a guide's contents, and its sections one at a time for what you will set), explore the repository read-only, make each setting
that is plainly right with your setup tools (each with its reason), check that what you declared works,
and leave the human one short message: what you set and why, and the few choices that are theirs, each
with your recommendation."""


CHECKLIST = """Your first message to the owner is this checklist, all of it in one short message (your words,
every point kept, each with your recommendation where you have one), and nothing else this turn: no
store is read before they answer.
1. Which systems (stores) should I read: a name for each and its address (a Prometheus-API URL).
2. Where is each one's token: the path of the file that holds it, or the name of the environment
   variable. Never ask for the token itself, and never read, print or copy it: the tools read it.
3. Which part of each store may the model read (the `match`: a namespace, a service, a selector),
   and which wider part may I survey to choose what it reads next (the `explore`: theirs alone,
   never widened by you)?
4. The caps: at most how many series, how many requests a second and a day to the store, and what
   my own rounds may spend a day (the defaults are 200 series, 5 a second, 20,000 a day, $2; say so).
5. Confirm it stays read-only: I never write to their stores, and change only Orchestra's settings.
Once they answer, set it up and explore within what they gave, without asking again (your
`exploring` skill)."""


def wants_checklist(raw: dict) -> bool:
    """Whether the first run opens with the owner's checklist: the configuration names no store."""
    return not raw.get("sources")


def goal(raw: dict) -> str:
    """The first run's wake-up: the goal, and the checklist first when no store is declared."""
    return GOAL + ("\n\n" + CHECKLIST if wants_checklist(raw) else "")


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
        self.asked = False

    def offer(self) -> bool:
        h = self.host
        if self.offered or not needs_setup(h.project.orchestra.config.raw, h.store.agent_state(SUPERVISOR), h.cfg.harness):
            return False
        self.offered = True
        self.asked = wants_checklist(h.project.orchestra.config.raw)
        h.store.add_wakeup(SUPERVISOR, goal(h.project.orchestra.config.raw))
        h.log("the project is not set up: the Supervisor is woken to set it up")
        return True

    def ended(self, reasons: list[str], ok: bool) -> None:
        """A Supervisor wake-up ended; when it was the setup's and it did not fail, the setup ran."""
        if ok and any(r.startswith(MARK) for r in reasons):
            self.host.store.set_agent_state(SUPERVISOR, setup={"ran_at": time.time(), "harness": self.host.cfg.harness,
                                                               **({"asked": "checklist"} if self.asked else {})})
