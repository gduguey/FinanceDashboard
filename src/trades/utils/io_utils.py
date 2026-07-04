"""Shared local-disk cache primitives used by every module that caches fetched data to disk."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

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


def write_json_atomic(data: dict[str, Any], path: Path) -> None:
    """Write a JSON-serializable dict to a file atomically.

    Same crash-safety rationale as `write_csv_atomic`: user-editable
    settings (e.g. a target allocation) are read back on the next request,
    so a half-written file would corrupt the dashboard's config, not just
    a data cache.

    Parameters
    ----------
    data
        The data to write.
    path
        The destination JSON path. Its parent directory is created if missing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2))
    tmp_path.replace(path)
