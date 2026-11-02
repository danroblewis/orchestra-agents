"""Words that hold only with the task system installed (``installed.tasking``), in the prompts and
the tool texts: ``[[tasking]]…[[/tasking]]`` is kept with it and dropped without it;
``[[no-tasking]]…[[/no-tasking]]`` the other way round. The Coordinator plans code work into
tickets, so without the task system there is no Coordinator: the T2c walkthrough's Supervisor told
the owner to hand a program "to the Coordinator", which was not installed. Without it the texts say
that code work is not handed on here, and that the Supervisor says so."""

from __future__ import annotations

import re

WITH = re.compile(r"\[\[tasking\]\](.*?)\[\[/tasking\]\]", re.DOTALL)
WITHOUT = re.compile(r"\[\[no-tasking\]\](.*?)\[\[/no-tasking\]\]", re.DOTALL)


def gate(text: str, on: bool | None = None) -> str:
    """The text as it holds now: ``on`` is whether the task system is installed (read when None)."""
    if "[[" not in text:
        return text
    if on is None:
        from . import installed
        on = installed.tasking()
    text = WITH.sub(lambda m: m.group(1) if on else "", text)
    return WITHOUT.sub(lambda m: "" if on else m.group(1), text)


def gate_schema(v, on: bool):
    """A tool's input schema with every description in it gated."""
    if isinstance(v, dict):
        return {k: gate(x, on) if k == "description" and isinstance(x, str) else gate_schema(x, on) for k, x in v.items()}
    if isinstance(v, list):
        return [gate_schema(x, on) for x in v]
    return v
