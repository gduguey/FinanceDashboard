"""Shared local-disk cache primitives used by every module that caches fetched data to disk."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import polars as pl

from trades.utils.cache_backup import restore_cache_file
from trades.utils.frames import collect_if_lazy


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


def read_csv_recovering_from_corruption(path: Path, backup_key: str, *, try_parse_dates: bool = True) -> pl.DataFrame:
    """Read a cached CSV, transparently repairing it from its one-slot backup if it's corrupted.

    This handles *file corruption* — a bad file already sitting on disk
    (e.g. a botched write outside the app's control, a Docker volume
    issue) — not a failed fetch. A failed fetch never gets this far: it
    never overwrites a cache file in the first place (see
    `trades.utils.cache_backup`'s own docstring), so the existing file a
    caller reads is always either genuinely valid or genuinely corrupted,
    never "corrupted because a fetch failed."

    Parameters
    ----------
    path
        The cache file to read.
    backup_key
        This file's key in `trades.utils.cache_backup`'s one-slot backup
        store (e.g. `"prices/AAPL.csv"`, `"cpi.csv"`, `"hysa_rates.csv"`).
    try_parse_dates
        Forwarded to `polars.read_csv`.

    Returns
    -------
    polars.DataFrame

    Raises
    ------
    polars.exceptions.ComputeError
        If `path` can't be parsed and either no backup exists to restore
        it from, or the restored copy is itself unparseable — surfaced
        loudly rather than silently treated as an empty cache, so a human
        notices instead of the app quietly losing history.
    """
    try:
        return pl.read_csv(path, try_parse_dates=try_parse_dates)
    except pl.exceptions.ComputeError:
        if not restore_cache_file(path, backup_key):
            raise
        return pl.read_csv(path, try_parse_dates=try_parse_dates)
