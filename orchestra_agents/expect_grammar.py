"""One expected edit of a machine's expected effect in SPEC's ``expect`` kinds, checked by
Orchestra's own grammar (``orchestra.sd.predictions.expectation``, which takes a stock's slope,
``rises`` and ``falls``, intended-model.md §2) and written back in its one shape (``plain``)."""

from __future__ import annotations

from .store import StoreError


def expect_edit(e) -> dict:
    from orchestra.sd.predictions import PredictionError, expectation, plain
    try:
        return plain(expectation(e))
    except PredictionError as err:
        raise StoreError(f"an expected edit is {{flow, factor}}, {{from, to, appears: true}}, {{stock, below|above: x}} or "
                         f"{{stock, rises|falls: true}}, rates where you can: {err}") from None
