"""Shared local-disk cache primitives used by every module that caches fetched data to a CSV file."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import polars as pl


def write_csv_atomic(frame: pl.DataFrame, path: Path) -> None:
    """Write a DataFrame to a CSV file atomically.

    Writes to a temporary file in the same directory, then replaces the
    destination in one filesystem operation, so a crash mid-write can
    never leave a half-written cache file.

    Parameters
    ----------
    frame
        The data to write.
    path
        The destination CSV path. Its parent directory is created if missing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".csv.tmp")
    frame.write_csv(tmp_path)
    tmp_path.replace(path)
