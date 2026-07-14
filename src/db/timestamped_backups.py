"""Generic newest-N pruning for timestamp-named backups, shared by `db.backup` and both cache-file backup modules.

Every backup this repo writes — the whole-database dump, and each of the
market-data cache files — is named `{UTC timestamp}{suffix}`, which sorts
lexicographically the same as chronologically. That one property is what
lets "keep the newest N" be a plain string sort with no timestamp parsing
anywhere, for both local-disk globs and R2 object listings. This module is
the one place that sort-and-prune logic lives; `db.backup.prune_old_backups`
and `accounting.utils.cache_backup`/`trades.utils.cache_backup`'s own
pruning all call straight through to it rather than each re-implementing
the same two loops.
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path


def timestamped_filename(taken_at: datetime, suffix: str) -> str:
    """Build a `{UTC timestamp}{suffix}` filename that sorts lexicographically the same as chronologically.

    Parameters
    ----------
    taken_at
        When this backup was taken; formatted to the second, UTC.
    suffix
        Including the leading dot, e.g. `".dump"`, `".csv"`.

    Returns
    -------
    str
    """
    return f"{taken_at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}{suffix}"


def prune_local_timestamped_files(directory: Path, pattern: str, retention_count: int) -> list[str]:
    """Delete every file matching `pattern` in `directory` beyond the newest `retention_count`.

    Parameters
    ----------
    directory
        Where the timestamp-named files live.
    pattern
        A `Path.glob` pattern, e.g. `"*.dump"`, `"*.csv"`.
    retention_count
        How many of the newest matching files to keep.

    Returns
    -------
    list[str]
        The full path of every file that was deleted.
    """
    files = sorted(directory.glob(pattern), key=lambda path: path.name, reverse=True)
    to_delete = files[retention_count:]
    for path in to_delete:
        path.unlink()
    return [str(path) for path in to_delete]


def prune_r2_timestamped_objects(client: Any, bucket_name: str, prefix: str, retention_count: int) -> list[str]:  # noqa: ANN401 — boto3 ships no typed client
    """Delete every object under `prefix` in `bucket_name` beyond the newest `retention_count`.

    Parameters
    ----------
    client
        A boto3 S3 client.
    bucket_name
        The R2 bucket to list/delete from.
    prefix
        List every object whose key starts with this.
    retention_count
        How many of the newest matching objects to keep.

    Returns
    -------
    list[str]
        The key of every object that was deleted.
    """
    response = client.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
    keys = sorted((entry["Key"] for entry in response.get("Contents", [])), reverse=True)
    to_delete_keys = keys[retention_count:]
    for key in to_delete_keys:
        client.delete_object(Bucket=bucket_name, Key=key)
    return to_delete_keys
