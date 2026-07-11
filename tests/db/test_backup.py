"""Tests for `db.backup`: dumping the whole database and archiving it to R2 (or local disk)."""

from __future__ import annotations

import subprocess  # noqa: S404 — only ever monkeypatched (subprocess.run) here, never actually invoked
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import db.backup as backup_module
from db.backup import (
    BackupR2Credentials,
    backup_relative_path,
    run_backup,
    run_pg_dump,
    upload_backup,
)


def test_backup_relative_path_formats_as_utc_timestamp() -> None:
    taken_at = datetime(2026, 7, 11, 3, 4, 5, tzinfo=UTC)

    assert backup_relative_path(taken_at) == "20260711T030405Z.dump"


def test_run_pg_dump_invokes_pg_dump_with_custom_format_and_the_given_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout=b"dump-bytes", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_pg_dump("postgresql://user:pass@localhost/db")

    assert result == b"dump-bytes"
    assert captured["argv"] == ["pg_dump", "--format=custom", "--dbname", "postgresql://user:pass@localhost/db"]


def test_run_pg_dump_raises_when_pg_dump_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        run_pg_dump("postgresql://user:pass@localhost/db")


def test_upload_backup_falls_back_to_local_disk_when_r2_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(backup_module, "_LOCAL_BACKUP_DIR", tmp_path / "backups")

    destination = upload_backup(b"dump-bytes", "20260711T030405Z.dump", credentials=BackupR2Credentials(_env_file=None))

    written = tmp_path / "backups" / "20260711T030405Z.dump"
    assert Path(destination) == written
    assert written.read_bytes() == b"dump-bytes"


def test_upload_backup_uploads_to_r2_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    put_calls: list[dict[str, Any]] = []

    class _FakeS3Client:
        def put_object(self, **kwargs: Any) -> None:
            put_calls.append(kwargs)

    monkeypatch.setattr(backup_module.boto3, "client", lambda *args, **kwargs: _FakeS3Client())

    credentials = BackupR2Credentials(
        _env_file=None,
        R2_ACCOUNT_ID="acct",
        R2_ACCESS_KEY_ID="key",
        R2_SECRET_ACCESS_KEY="secret",  # noqa: S106
        R2_BUCKET_NAME="my-bucket",
        R2_ENDPOINT_URL="https://example.r2.cloudflarestorage.com",
    )

    destination = upload_backup(b"dump-bytes", "20260711T030405Z.dump", credentials=credentials)

    assert destination == "backups/postgres/20260711T030405Z.dump"
    assert put_calls == [
        {"Bucket": "my-bucket", "Key": "backups/postgres/20260711T030405Z.dump", "Body": b"dump-bytes"}
    ]


def test_run_backup_dumps_then_uploads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup_module, "run_pg_dump", lambda database_url: b"dump-bytes")
    monkeypatch.setattr(
        backup_module.DatabaseSettings, "database_url", property(lambda self: "postgresql://x/y"), raising=False
    )

    captured: dict[str, Any] = {}

    def fake_upload(data: bytes, relative_path: str, credentials: BackupR2Credentials | None = None) -> str:
        captured["data"] = data
        captured["relative_path"] = relative_path
        return "backups/postgres/whatever.dump"

    monkeypatch.setattr(backup_module, "upload_backup", fake_upload)

    destination = run_backup()

    assert destination == "backups/postgres/whatever.dump"
    assert captured["data"] == b"dump-bytes"
    assert captured["relative_path"].endswith(".dump")
