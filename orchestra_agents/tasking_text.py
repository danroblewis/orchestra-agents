"""Words that hold only with one of the plugins that hand work on installed, in the prompts and the
tool texts. Three conditions, each a pair of markers, kept when it holds and dropped otherwise:

- ``[[tasking]]…[[/tasking]]``: the task system is installed (``installed.tasking``). The
  Coordinator plans code work into tickets, so without it there is no Coordinator: the T2c
  walkthrough's Supervisor told the owner to hand a program "to the Coordinator", which was not
  installed. ``[[no-tasking]]…[[/no-tasking]]`` the other way round.
- ``[[workflows]]…[[/workflows]]``: no task system, but the Workflows plugin is
  (``installed.workflows``). Code work then goes to a worker as a Workflows item, on a branch in a
  worktree (skill ``code-work``): the owner's report of 2026-11-04, a Supervisor that said it could
  not do coding work although Workflows was installed.
- ``[[no-workflows]]…[[/no-workflows]]``: neither is installed. Only then is code work not handed
  on here, and the Supervisor says what is missing (the Workflows plugin) and that installing it is
  the one step, never that it is not capable.

With both installed the task system's words hold and the Workflows-only ones are dropped.

One more pair, not about a plugin: ``[[act]]…[[/act]]`` holds under ``[agents] ask = "outside"`` (the
default: the Supervisor acts and says what it did) and ``[[ask]]…[[/ask]]`` under ``ask = "always"``
(it asks first), ``ask.py``. Text gated without a project (a tool's description) takes the default."""

from __future__ import annotations

import re

WITH = re.compile(r"\[\[tasking\]\](.*?)\[\[/tasking\]\]", re.DOTALL)
WITHOUT = re.compile(r"\[\[no-tasking\]\](.*?)\[\[/no-tasking\]\]", re.DOTALL)
FLOOR = re.compile(r"\[\[workflows\]\](.*?)\[\[/workflows\]\]", re.DOTALL)
NEITHER = re.compile(r"\[\[no-workflows\]\](.*?)\[\[/no-workflows\]\]", re.DOTALL)
ACT = re.compile(r"\[\[act\]\](.*?)\[\[/act\]\]", re.DOTALL)
ASK = re.compile(r"\[\[ask\]\](.*?)\[\[/ask\]\]", re.DOTALL)


def gate(text: str, on: bool | None = None, workflows: bool | None = None, ask: str | None = None) -> str:
    """The text as it holds now: ``on`` is whether the task system is installed, ``workflows``
    whether the Workflows plugin is (each read when None), ``ask`` the project's ``[agents] ask``
    (the default when None)."""
    if "[[" not in text:
        return text
    from .ask import DEFAULT
    acting = (ask or DEFAULT) == "outside"
    text = ACT.sub(lambda m: m.group(1) if acting else "", text)
    text = ASK.sub(lambda m: "" if acting else m.group(1), text)
    if "[[" not in text:
        return text
    from . import installed
    if on is None:
        on = installed.tasking()
    if workflows is None:
        workflows = installed.workflows()
    text = WITH.sub(lambda m: m.group(1) if on else "", text)
    text = WITHOUT.sub(lambda m: "" if on else m.group(1), text)
    text = FLOOR.sub(lambda m: m.group(1) if workflows and not on else "", text)
    return NEITHER.sub(lambda m: "" if workflows or on else m.group(1), text)


def gate_schema(v, on: bool, workflows: bool | None = None):
    """A tool's input schema with every description in it gated."""
    if isinstance(v, dict):
        return {k: gate(x, on, workflows) if k == "description" and isinstance(x, str) else gate_schema(x, on, workflows)
                for k, x in v.items()}
    if isinstance(v, list):
        return [gate_schema(x, on, workflows) for x in v]
    return v
