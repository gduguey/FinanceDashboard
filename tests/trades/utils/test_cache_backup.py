"""Tests for `trades.utils.cache_backup`: newest-N backup/restore for cache files, sharing `db.backup`'s own retention."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from db.backup import BackupSettings
from trades.utils import cache_backup
from trades.utils.cache_backup import CacheBackupR2Credentials, backup_cache_file, restore_cache_file

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_T1 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
_T2 = datetime(2026, 7, 14, 12, 5, 0, tzinfo=UTC)
_T3 = datetime(2026, 7, 14, 12, 10, 0, tzinfo=UTC)


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:  # noqa: N803
        self.objects[Key] = Body

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, Bucket: str, Prefix: str) -> dict[str, Any]:  # noqa: N803
        return {"Contents": [{"Key": key} for key in self.objects if key.startswith(Prefix)]}

    def delete_object(self, Bucket: str, Key: str) -> None:  # noqa: N803
        del self.objects[Key]


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

    assert len(fake_client.objects) == 1
    (key, body) = next(iter(fake_client.objects.items()))
    assert key.startswith("cache-backups/trades/prices/AAPL.csv/")
    assert body == b"price_date,close\n2026-01-01,100.0\n"

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


def test_backup_cache_file_keeps_multiple_local_versions_and_restore_uses_the_newest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(cache_backup, "get_backup_settings", lambda: BackupSettings(_env_file=None))
    local_path = tmp_path / "cache" / "AAPL.csv"
    local_path.parent.mkdir(parents=True)

    local_path.write_bytes(b"version-1")
    backup_cache_file(local_path, "prices/AAPL.csv", credentials=_unconfigured_credentials(), taken_at=_T1)
    local_path.write_bytes(b"version-2")
    backup_cache_file(local_path, "prices/AAPL.csv", credentials=_unconfigured_credentials(), taken_at=_T2)

    versions = list((tmp_path / "backups" / "prices" / "AAPL.csv").glob("*.csv"))
    assert len(versions) == 2

    local_path.write_bytes(b"corrupted")
    restore_cache_file(local_path, "prices/AAPL.csv", credentials=_unconfigured_credentials())

    assert local_path.read_bytes() == b"version-2"


def test_backup_cache_file_prunes_local_versions_beyond_retention_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(
        cache_backup, "get_backup_settings", lambda: BackupSettings(_env_file=None, BACKUP_RETENTION_COUNT=2)
    )
    local_path = tmp_path / "cache" / "AAPL.csv"
    local_path.parent.mkdir(parents=True)

    for version, taken_at in enumerate((_T1, _T2, _T3)):
        local_path.write_bytes(f"version-{version}".encode())
        backup_cache_file(local_path, "prices/AAPL.csv", credentials=_unconfigured_credentials(), taken_at=taken_at)

    versions = list((tmp_path / "backups" / "prices" / "AAPL.csv").glob("*.csv"))
    assert len(versions) == 2


def test_backup_cache_file_keeps_multiple_r2_versions_and_restore_uses_the_newest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_client = _FakeS3Client()
    monkeypatch.setattr(cache_backup.boto3, "client", lambda *args, **kwargs: fake_client)
    monkeypatch.setattr(cache_backup, "get_backup_settings", lambda: BackupSettings(_env_file=None))
    local_path = tmp_path / "cache" / "AAPL.csv"
    local_path.parent.mkdir(parents=True)

    local_path.write_bytes(b"version-1")
    backup_cache_file(local_path, "prices/AAPL.csv", credentials=_configured_credentials(), taken_at=_T1)
    local_path.write_bytes(b"version-2")
    backup_cache_file(local_path, "prices/AAPL.csv", credentials=_configured_credentials(), taken_at=_T2)

    assert len(fake_client.objects) == 2

    local_path.write_bytes(b"corrupted")
    restore_cache_file(local_path, "prices/AAPL.csv", credentials=_configured_credentials())

    assert local_path.read_bytes() == b"version-2"


def test_backup_cache_file_prunes_r2_versions_beyond_retention_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_client = _FakeS3Client()
    monkeypatch.setattr(cache_backup.boto3, "client", lambda *args, **kwargs: fake_client)
    monkeypatch.setattr(
        cache_backup, "get_backup_settings", lambda: BackupSettings(_env_file=None, BACKUP_RETENTION_COUNT=2)
    )
    local_path = tmp_path / "cache" / "AAPL.csv"
    local_path.parent.mkdir(parents=True)

    for version, taken_at in enumerate((_T1, _T2, _T3)):
        local_path.write_bytes(f"version-{version}".encode())
        backup_cache_file(local_path, "prices/AAPL.csv", credentials=_configured_credentials(), taken_at=taken_at)

    assert len(fake_client.objects) == 2


def test_backup_cache_file_migrates_a_pre_retention_single_file_backup_to_a_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A backup written before this module kept multiple versions was a plain file at this same path."""
    backups_root = tmp_path / "backups"
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", backups_root)
    stale_backup_path = backups_root / "prices" / "AAPL.csv"
    stale_backup_path.parent.mkdir(parents=True)
    stale_backup_path.write_bytes(b"old single-slot backup")

    local_path = tmp_path / "cache" / "AAPL.csv"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"fresh version")

    backup_cache_file(local_path, "prices/AAPL.csv", credentials=_unconfigured_credentials(), taken_at=_T1)

    assert stale_backup_path.is_dir()
    versions = list(stale_backup_path.glob("*.csv"))
    assert len(versions) == 1
    assert versions[0].read_bytes() == b"fresh version"
