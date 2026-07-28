"""Tests for `db.timestamped_backups`: the generic newest-N pruning logic `db.backup` and the cache-file

backup modules (`accounting.utils.cache_backup`/`trades.utils.cache_backup`) both build on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from db.timestamped_backups import prune_local_timestamped_files, prune_r2_timestamped_objects, timestamped_filename

if TYPE_CHECKING:
    from pathlib import Path


def test_timestamped_filename_formats_as_utc_timestamp_with_the_given_suffix() -> None:
    taken_at = datetime(2026, 7, 14, 3, 4, 5, tzinfo=UTC)

    assert timestamped_filename(taken_at, ".dump") == "20260714T030405Z.dump"
    assert timestamped_filename(taken_at, ".csv") == "20260714T030405Z.csv"


def test_prune_local_timestamped_files_deletes_oldest_beyond_retention_count(tmp_path: Path) -> None:
    names = [f"2026070{i}T000000Z.csv" for i in range(1, 6)]
    for name in names:
        (tmp_path / name).write_bytes(b"x")

    deleted = prune_local_timestamped_files(tmp_path, "*.csv", retention_count=2)

    remaining = sorted(p.name for p in tmp_path.glob("*.csv"))
    assert remaining == names[-2:]
    assert sorted(deleted) == sorted(str(tmp_path / name) for name in names[:-2])


def test_prune_local_timestamped_files_deletes_nothing_when_fewer_files_than_retention_count(tmp_path: Path) -> None:
    (tmp_path / "20260701T000000Z.csv").write_bytes(b"x")

    deleted = prune_local_timestamped_files(tmp_path, "*.csv", retention_count=14)

    assert deleted == []
    assert len(list(tmp_path.glob("*.csv"))) == 1


def test_prune_local_timestamped_files_only_matches_the_given_pattern(tmp_path: Path) -> None:
    (tmp_path / "20260701T000000Z.csv").write_bytes(b"x")
    (tmp_path / "20260701T000000Z.adjusted.csv").write_bytes(b"x")

    deleted = prune_local_timestamped_files(tmp_path, "*.adjusted.csv", retention_count=0)

    assert deleted == [str(tmp_path / "20260701T000000Z.adjusted.csv")]
    assert (tmp_path / "20260701T000000Z.csv").exists()


def test_prune_r2_timestamped_objects_deletes_oldest_beyond_retention_count() -> None:
    names = [f"2026070{i}T000000Z.csv" for i in range(1, 6)]
    keys = [f"cache-backups/trades/prices/AAPL.csv/{name}" for name in names]
    delete_calls: list[dict[str, Any]] = []

    class _FakeS3Client:
        def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
            return {"Contents": [{"Key": key} for key in keys]}

        def delete_object(self, **kwargs: Any) -> None:
            delete_calls.append(kwargs)

    deleted = prune_r2_timestamped_objects(
        _FakeS3Client(), bucket_name="my-bucket", prefix="cache-backups/trades/prices/AAPL.csv/", retention_count=2
    )

    assert sorted(deleted) == sorted(keys[:-2])
    assert sorted(call["Key"] for call in delete_calls) == sorted(keys[:-2])
    assert all(call["Bucket"] == "my-bucket" for call in delete_calls)


def test_prune_r2_timestamped_objects_deletes_nothing_when_fewer_objects_than_retention_count() -> None:
    keys = ["backups/postgres/20260701T000000Z.dump"]
    delete_calls: list[dict[str, Any]] = []

    class _FakeS3Client:
        def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
            return {"Contents": [{"Key": key} for key in keys]}

        def delete_object(self, **kwargs: Any) -> None:
            delete_calls.append(kwargs)

    deleted = prune_r2_timestamped_objects(
        _FakeS3Client(), bucket_name="my-bucket", prefix="backups/postgres/", retention_count=14
    )

    assert deleted == []
    assert delete_calls == []
