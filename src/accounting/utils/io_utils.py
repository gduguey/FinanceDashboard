"""Shared local-disk cache primitives used by every accounting module that caches data to disk.

A deliberate duplicate of `trades.utils.io_utils`, not a shared import from
it — accounting has no functional need for anything else in `trades`, and
this was the one place it depended on that package for something with
nothing to do with investments (see `docs/accounting/architecture.md`).
Keeping accounting's own copy, even though the two are identical today,
means accounting still works if `trades` is ever removed, broken, or not
installed at all.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import polars as pl


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


def write_csv_atomic(frame: pl.DataFrame | pl.LazyFrame, path: Path) -> None:
    """Write a DataFrame or LazyFrame to a CSV file atomically.

    Collects the frame if lazy, writes to a temporary file with a unique name
    in the same directory, then replaces the destination in one filesystem
    operation. A try/finally ensures temp files are cleaned up even if the
    write fails, preventing stale temp files from accumulating. This prevents
    race conditions when multiple concurrent writers target the same path:
    each uses a unique temp name, so one writer's partial write is never picked
    up by another's replace().

    Parameters
    ----------
    frame
        The data to write. If LazyFrame, will be collected before writing.
    path
        The destination CSV path. Its parent directory is created if missing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    eager_frame = collect_if_lazy(frame)

    # Use mkstemp for a unique temp filename with no open file descriptor conflicts
    fd, tmp_path_str = tempfile.mkstemp(suffix=".csv", dir=str(path.parent))
    tmp_path = path.parent / Path(tmp_path_str).name
    try:
        os.close(fd)  # Close the FD opened by mkstemp; we'll write via polars
        eager_frame.write_csv(tmp_path)
        tmp_path.replace(path)
    finally:
        # Clean up temp file if it still exists (e.g., if write_csv failed)
        if tmp_path.exists():
            tmp_path.unlink()
