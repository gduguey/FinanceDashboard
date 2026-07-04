"""Shared helpers for functions that accept either a polars DataFrame or LazyFrame.

Two distinct situations come up repeatedly:

- A function's whole body is a genuine multi-step chain (several
  `with_columns`/`group_by`/`sort` steps) that benefits from the lazy
  query optimizer even when the caller passed an eager `DataFrame` — it
  should still return whatever type the caller gave it. Use
  `preserve_frame_type` once, at the end.
- A function hits a hard boundary it can't avoid — row iteration, a
  scalar extraction, a pivot — where the result is never lazy regardless
  of what came in. Use `collect_if_lazy` right at that boundary, not
  earlier, and don't route through `.lazy()` first: a `DataFrame` never
  needs one to reach that boundary.
"""

from __future__ import annotations

import polars as pl


def preserve_frame_type(result: pl.LazyFrame, like: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame | pl.LazyFrame:
    """Collect a lazily-built result only if the caller originally passed an eager DataFrame.

    Parameters
    ----------
    result
        The lazily-built result of a multi-step internal computation.
    like
        The frame the caller originally passed in, whose type `result` should match.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        `result`, collected if `like` was a `DataFrame`; left lazy otherwise.
    """
    return result.collect() if isinstance(like, pl.DataFrame) else result


def collect_if_lazy(frame: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
    """Materialize a frame only if it isn't already.

    Parameters
    ----------
    frame
        A DataFrame or LazyFrame, at a point where the caller's original
        type no longer matters because the surrounding function's own
        result is never lazy (row iteration, a scalar extraction, a pivot).

    Returns
    -------
    polars.DataFrame
        `frame`, collected if it was a `LazyFrame`; returned unchanged otherwise.
    """
    return frame.collect() if isinstance(frame, pl.LazyFrame) else frame
