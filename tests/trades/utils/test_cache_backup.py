"""Tests for `trades.utils.cache_backup`: one-slot last-known-good backup/restore for cache files."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from trades.utils import cache_backup
from trades.utils.cache_backup import CacheBackupR2Credentials, backup_cache_file, restore_cache_file

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:  # noqa: N803
        self.objects[Key] = Body

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


def _configured_credentials() -> CacheBackupR2Credentials:
    return CacheBackupR2Credentials(
        _env_file=None,
        R2_ACCOUNT_ID="acct",
        R2_ACCESS_KEY_ID="key",
        R2_SECRET_ACCESS_KEY="secret",  # noqa: S106
        R2_BUCKET_NAME="my-bucket",
        R2_ENDPOINT_URL="https://example.r2.cloudflarestorage.com",
    )


def _unconfigured_credentials() -> CacheBackupR2Credentials:
    return CacheBackupR2Credentials(_env_file=None)


def test_local_fallback_backup_then_restore_round_trips_bytes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backups")
    local_path = tmp_path / "cache" / "AAPL.csv"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"price_date,close\n2026-01-01,100.0\n")

    backup_cache_file(local_path, "prices/AAPL.csv", credentials=_unconfigured_credentials())

    local_path.write_bytes(b"corrupted garbage")
    restored = restore_cache_file(local_path, "prices/AAPL.csv", credentials=_unconfigured_credentials())

    assert restored is True
    assert local_path.read_bytes() == b"price_date,close\n2026-01-01,100.0\n"


def test_local_fallback_restore_returns_false_and_leaves_file_untouched_when_nothing_backed_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backups")
    local_path = tmp_path / "cache" / "AAPL.csv"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"still here")

    restored = restore_cache_file(local_path, "prices/AAPL.csv", credentials=_unconfigured_credentials())

    assert restored is False
    assert local_path.read_bytes() == b"still here"


def test_r2_backup_then_restore_round_trips_bytes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_client = _FakeS3Client()
    monkeypatch.setattr(cache_backup.boto3, "client", lambda *args, **kwargs: fake_client)
    local_path = tmp_path / "cache" / "AAPL.csv"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"price_date,close\n2026-01-01,100.0\n")

    backup_cache_file(local_path, "prices/AAPL.csv", credentials=_configured_credentials())

    assert fake_client.objects == {"cache-backups/trades/prices/AAPL.csv": b"price_date,close\n2026-01-01,100.0\n"}

    local_path.write_bytes(b"corrupted garbage")
    restored = restore_cache_file(local_path, "prices/AAPL.csv", credentials=_configured_credentials())

    assert restored is True
    assert local_path.read_bytes() == b"price_date,close\n2026-01-01,100.0\n"


def test_r2_restore_returns_false_and_leaves_file_untouched_when_nothing_backed_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_client = _FakeS3Client()
    monkeypatch.setattr(cache_backup.boto3, "client", lambda *args, **kwargs: fake_client)
    local_path = tmp_path / "cache" / "AAPL.csv"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"still here")

    restored = restore_cache_file(local_path, "prices/AAPL.csv", credentials=_configured_credentials())

    assert restored is False
    assert local_path.read_bytes() == b"still here"


def test_backup_cache_file_overwrites_the_one_backup_slot_with_no_history_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backups")
    local_path = tmp_path / "cache" / "AAPL.csv"
    local_path.parent.mkdir(parents=True)

    local_path.write_bytes(b"version-1")
    backup_cache_file(local_path, "prices/AAPL.csv", credentials=_unconfigured_credentials())
    local_path.write_bytes(b"version-2")
    backup_cache_file(local_path, "prices/AAPL.csv", credentials=_unconfigured_credentials())

    local_path.write_bytes(b"corrupted")
    restore_cache_file(local_path, "prices/AAPL.csv", credentials=_unconfigured_credentials())

    assert local_path.read_bytes() == b"version-2"
