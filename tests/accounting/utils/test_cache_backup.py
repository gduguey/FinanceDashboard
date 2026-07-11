"""Tests for `accounting.utils.cache_backup`: one "last known good" backup per cache file."""

from __future__ import annotations

import io

from botocore.exceptions import ClientError

from accounting.utils import cache_backup
from accounting.utils.cache_backup import CacheBackupR2Credentials, backup_cache_file, restore_cache_file


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body):  # noqa: N803
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


def _configured_credentials() -> CacheBackupR2Credentials:
    return CacheBackupR2Credentials(
        account_id="acct",
        access_key_id="key",
        secret_access_key="secret",  # noqa: S106
        bucket_name="bucket",
        endpoint_url="https://acct.r2.cloudflarestorage.com",
        _env_file=None,
    )


def test_local_fallback_backup_then_restore_round_trips_bytes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backups")
    local_path = tmp_path / "exchange_rates.csv"
    local_path.write_text("date,currency,rate_to_base\n2026-06-01,EUR,1.10\n")
    credentials = CacheBackupR2Credentials(_env_file=None)

    backup_cache_file(local_path, "exchange_rates.csv", credentials=credentials)
    local_path.write_text("this is now corrupted")
    restored = restore_cache_file(local_path, "exchange_rates.csv", credentials=credentials)

    assert restored is True
    assert local_path.read_text() == "date,currency,rate_to_base\n2026-06-01,EUR,1.10\n"


def test_local_fallback_restore_returns_false_and_leaves_local_path_untouched_when_nothing_backed_up(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backups")
    local_path = tmp_path / "exchange_rates.csv"
    local_path.write_text("still here")
    credentials = CacheBackupR2Credentials(_env_file=None)

    restored = restore_cache_file(local_path, "exchange_rates.csv", credentials=credentials)

    assert restored is False
    assert local_path.read_text() == "still here"


def test_r2_backup_then_restore_round_trips_bytes(monkeypatch, tmp_path) -> None:
    fake_client = _FakeS3Client()
    monkeypatch.setattr(cache_backup.boto3, "client", lambda *args, **kwargs: fake_client)
    local_path = tmp_path / "exchange_rates.csv"
    local_path.write_text("date,currency,rate_to_base\n2026-06-01,EUR,1.10\n")
    credentials = _configured_credentials()

    backup_cache_file(local_path, "exchange_rates.csv", credentials=credentials)

    assert fake_client.objects == {
        "cache-backups/accounting/exchange_rates.csv": b"date,currency,rate_to_base\n2026-06-01,EUR,1.10\n"
    }

    local_path.write_text("this is now corrupted")
    restored = restore_cache_file(local_path, "exchange_rates.csv", credentials=credentials)

    assert restored is True
    assert local_path.read_text() == "date,currency,rate_to_base\n2026-06-01,EUR,1.10\n"
    # Never touches local disk for the backup itself when R2 is configured.
    assert set(tmp_path.iterdir()) == {local_path}


def test_r2_restore_returns_false_and_leaves_local_path_untouched_when_nothing_backed_up(monkeypatch, tmp_path) -> None:
    fake_client = _FakeS3Client()
    monkeypatch.setattr(cache_backup.boto3, "client", lambda *args, **kwargs: fake_client)
    local_path = tmp_path / "exchange_rates.csv"
    local_path.write_text("still here")
    credentials = _configured_credentials()

    restored = restore_cache_file(local_path, "exchange_rates.csv", credentials=credentials)

    assert restored is False
    assert local_path.read_text() == "still here"


def test_resolve_is_none_when_unconfigured() -> None:
    assert CacheBackupR2Credentials(_env_file=None).resolve() is None


def test_resolve_is_none_when_partially_configured() -> None:
    credentials = CacheBackupR2Credentials(account_id="acct", bucket_name="bucket", _env_file=None)
    assert credentials.resolve() is None


def test_resolve_returns_every_field_when_fully_configured() -> None:
    resolved = _configured_credentials().resolve()
    assert resolved is not None
    assert resolved.bucket_name == "bucket"
    assert resolved.endpoint_url == "https://acct.r2.cloudflarestorage.com"
