"""One expected edit of a machine's expected effect in SPEC's ``expect`` kinds, checked by
Orchestra's own grammar (``orchestra.sd.predictions.expectation``) and written back in its one
shape (``plain``). ``rises`` and ``falls`` (a stock's slope, intended-model.md §2) are Orchestra's
from task C1 on; an Orchestra whose grammar has no slope yet gets them checked here the same way,
and ``slopes()`` says so, because such an Orchestra cannot judge one when it is published."""

from __future__ import annotations

from functools import cache

from .store import StoreError


@cache
def slopes() -> bool:
    """Whether the installed Orchestra's grammar takes ``rises`` and ``falls``."""
    from orchestra.sd.predictions import PredictionError, expectation
    try:
        expectation({"stock": "x", "rises": True})
    except PredictionError:
        return False
    return True


def is_slope(e: dict) -> bool:
    return isinstance(e, dict) and (e.get("rises") is True or e.get("falls") is True)


def expect_edit(e) -> dict:
    from orchestra.sd.predictions import PredictionError, expectation, plain
    if is_slope(e) and not slopes():
        if set(e) - {"stock", "rises", "falls"} or not isinstance(e.get("stock"), str) or not e["stock"].strip() \
                or (e.get("rises") and e.get("falls")):
            raise StoreError(f"rises and falls are said of one stock's slope: {{\"stock\": S, \"rises\": true}}, not {e!r}")
        return {"stock": e["stock"], "rises" if e.get("rises") else "falls": True}
    try:
        return plain(expectation(e))
    except PredictionError as err:
        raise StoreError(f"an expected edit is {{flow, factor}}, {{from, to, appears: true}}, {{stock, below|above: x}} or "
                         f"{{stock, rises|falls: true}}, rates where you can: {err}") from None
