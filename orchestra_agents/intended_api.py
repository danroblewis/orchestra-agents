"""The declared layer, as the design tools reach it: Orchestra's ``project.intended``
(``docs/proposals/intended-model.md`` §6.3, built in Orchestra's task C1) when the installed
Orchestra has it, else the agents' stand-in (``intended_standin.py``).

**The integration seam.** This module is the one place that chooses. The design tools call only
``apply(ops, provenance)``, ``read()`` and ``design()`` on what ``layer`` returns, in the shapes the
proposal gives, so when Orchestra's own store is installed it is used with no other change, and
the stand-in (with this fallback) is deleted. Until then what the tools declare is kept in the
agents' state directory and the pages do not draw it; every answer says so (``where``)."""

from __future__ import annotations

from typing import Any, Protocol

from .config import Project


class Layer(Protocol):
    def apply(self, ops: list[dict], provenance: dict) -> dict: ...
    def read(self, version: int | None = None) -> dict: ...
    def design(self) -> dict: ...
    def journal(self) -> list[dict]: ...


class Core:
    """Orchestra's own store, with its refusals (``IntentError``) said as the tools say theirs."""

    def __init__(self, api):
        self.api = api

    def _call(self, fn, *a):
        try:
            return fn(*a)
        except ValueError as e:                      # orchestra.sd.intended.IntentError, with the refused op's index
            from .store import StoreError
            index = getattr(e, "index", None)
            raise StoreError(f"Orchestra refused {'op ' + str(index) + ': ' if index is not None else ''}{e}; nothing was "
                             "applied") from None

    def apply(self, ops, provenance):
        return self._call(self.api.apply, ops, provenance)

    def read(self, version=None):
        return self._call(self.api.read, version)

    def design(self):
        return self._call(self.api.design)

    def journal(self, after: int = 0):
        return self._call(self.api.journal, after)


def core(project: Project) -> Any | None:
    """Orchestra's declared layer, when the installed Orchestra has one (C1's ``Project.intended``),
    else None."""
    try:
        api = getattr(project.orchestra, "intended", None)
    except Exception:  # noqa: BLE001 - an Orchestra whose store cannot open has none to offer
        return None
    return Core(api) if api is not None and hasattr(api, "apply") else None


def layer(project: Project) -> Layer:
    found = core(project)
    if found is not None:
        return found
    from .intended_standin import StandIn
    return StandIn(project)


def current(project: Project) -> dict:
    """The declared layer as it is now: ``{stocks, effects, flows, machines, events, targets, marks, ...}``."""
    return layer(project).read()["layer"]


def where(project: Project) -> str:
    """Where a declaration is kept, for the tools' answers."""
    if core(project) is not None:
        return "Orchestra's declared layer: the Dynamics and Machines pages draw it"
    return ("the agents' stand-in for Orchestra's declared layer (this Orchestra has none yet): kept, but the pages "
            "draw it only once Orchestra has project.intended")
