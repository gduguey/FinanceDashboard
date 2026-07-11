"""Tests for `db.backup`: dumping the whole database and archiving it to R2 (or local disk)."""

from __future__ import annotations

import subprocess  # noqa: S404 — only ever monkeypatched (subprocess.run) here, never actually invoked
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

import db.backup as backup_module
from db.backup import (
    BackupR2Credentials,
    backup_relative_path,
    prune_old_backups,
    run_backup,
    run_pg_dump,
    upload_backup,
    verify_backup_restorable,
)
from db.settings import TestDatabaseSettings


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


class _FakeMaintenanceConnection:
    """Fake AUTOCOMMIT connection recording every executed statement's SQL text."""

    def __init__(self, executed: list[str]) -> None:
        self._executed = executed

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, statement: Any) -> None:
        self._executed.append(str(statement))


class _FakeMaintenanceEngine:
    """Fake SQLAlchemy engine standing in for `create_engine(..., isolation_level="AUTOCOMMIT")`."""

    def __init__(self, executed: list[str]) -> None:
        self._executed = executed

    def connect(self) -> _FakeMaintenanceConnection:
        return _FakeMaintenanceConnection(self._executed)

    def dispose(self) -> None:
        return None


def _patch_maintenance_engine(monkeypatch: pytest.MonkeyPatch, executed: list[str]) -> None:
    monkeypatch.setattr(backup_module, "create_engine", lambda *args, **kwargs: _FakeMaintenanceEngine(executed))
    monkeypatch.setattr(
        backup_module.DatabaseSettings,
        "database_url",
        property(lambda self: "postgresql://user:pass@localhost:5433/finance_dev"),
        raising=False,
    )


def test_verify_backup_restorable_creates_scratch_db_restores_and_drops_it(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[str] = []
    _patch_maintenance_engine(monkeypatch, executed)

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["argv"] = argv
        temp_path = Path(argv[-1])
        assert temp_path.exists()
        assert temp_path.read_bytes() == b"dump-bytes"
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert verify_backup_restorable(b"dump-bytes") is None

    assert len(executed) == 2
    create_statement, drop_statement = executed
    assert create_statement.startswith("CREATE DATABASE ")
    assert drop_statement.startswith("DROP DATABASE ")
    assert "WITH (FORCE)" in drop_statement

    create_name = create_statement.split('"')[1]
    drop_name = drop_statement.split('"')[1]
    assert create_name == drop_name
    assert create_name.startswith("backup_verify_")

    argv = captured["argv"]
    assert argv[0] == "pg_restore"
    assert "--clean" in argv
    assert "--if-exists" in argv
    scratch_url = argv[argv.index("--dbname") + 1]
    assert scratch_url.endswith(f"/{create_name}")
    assert scratch_url.startswith("postgresql://user:pass@localhost:5433/")


def test_verify_backup_restorable_drops_scratch_db_even_when_pg_restore_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []
    _patch_maintenance_engine(monkeypatch, executed)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        verify_backup_restorable(b"dump-bytes")

    assert len(executed) == 2
    assert executed[0].startswith("CREATE DATABASE ")
    assert executed[1].startswith("DROP DATABASE ")
    assert "WITH (FORCE)" in executed[1]


def _real_postgres_reachable() -> bool:
    try:
        engine = create_engine(TestDatabaseSettings().database_url)
    except Exception:  # noqa: BLE001 — any settings/driver error just means "not reachable" for this skip check
        return False
    try:
        with engine.connect():
            return True
    except OperationalError:
        return False
    finally:
        engine.dispose()


@pytest.mark.skipif(not _real_postgres_reachable(), reason="No reachable local Postgres for this integration test")
def test_verify_backup_restorable_against_real_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    real_test_database_url = TestDatabaseSettings().database_url
    monkeypatch.setattr(
        backup_module.DatabaseSettings,
        "database_url",
        property(lambda self: real_test_database_url),
        raising=False,
    )

    dump = run_pg_dump(real_test_database_url)

    assert verify_backup_restorable(dump) is None

    # The real maintenance connection's scratch database must be gone afterwards.
    maintenance_url = make_url(real_test_database_url).set(database="postgres")
    engine = create_engine(maintenance_url)
    with engine.connect() as conn:
        remaining = conn.execute(
            text("SELECT count(*) FROM pg_database WHERE datname LIKE :pattern"), {"pattern": "backup_verify_%"}
        ).scalar_one()
    engine.dispose()
    assert remaining == 0


def test_prune_old_backups_deletes_oldest_local_files_beyond_retention_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    names = [f"2026070{i}T000000Z.dump" for i in range(1, 6)]
    for name in names:
        (backups_dir / name).write_bytes(b"x")
    monkeypatch.setattr(backup_module, "_LOCAL_BACKUP_DIR", backups_dir)

    deleted = prune_old_backups(retention_count=2, credentials=BackupR2Credentials(_env_file=None))

    remaining = sorted(p.name for p in backups_dir.glob("*.dump"))
    assert remaining == names[-2:]
    assert sorted(deleted) == sorted(str(backups_dir / name) for name in names[:-2])


def test_prune_old_backups_deletes_nothing_when_fewer_local_files_than_retention_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    (backups_dir / "20260701T000000Z.dump").write_bytes(b"x")
    monkeypatch.setattr(backup_module, "_LOCAL_BACKUP_DIR", backups_dir)

    deleted = prune_old_backups(retention_count=14, credentials=BackupR2Credentials(_env_file=None))

    assert deleted == []
    assert len(list(backups_dir.glob("*.dump"))) == 1


def _configured_r2_credentials() -> BackupR2Credentials:
    return BackupR2Credentials(
        _env_file=None,
        R2_ACCOUNT_ID="acct",
        R2_ACCESS_KEY_ID="key",
        R2_SECRET_ACCESS_KEY="secret",  # noqa: S106
        R2_BUCKET_NAME="my-bucket",
        R2_ENDPOINT_URL="https://example.r2.cloudflarestorage.com",
    )


def test_prune_old_backups_deletes_oldest_r2_objects_beyond_retention_count(monkeypatch: pytest.MonkeyPatch) -> None:
    names = [f"2026070{i}T000000Z.dump" for i in range(1, 6)]
    keys = [f"backups/postgres/{name}" for name in names]
    delete_calls: list[dict[str, Any]] = []

    class _FakeS3Client:
        def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
            return {"Contents": [{"Key": key} for key in keys]}

        def delete_object(self, **kwargs: Any) -> None:
            delete_calls.append(kwargs)

    monkeypatch.setattr(backup_module.boto3, "client", lambda *args, **kwargs: _FakeS3Client())

    deleted = prune_old_backups(retention_count=2, credentials=_configured_r2_credentials())

    assert sorted(deleted) == sorted(keys[:-2])
    assert sorted(call["Key"] for call in delete_calls) == sorted(keys[:-2])
    assert all(call["Bucket"] == "my-bucket" for call in delete_calls)


def test_prune_old_backups_deletes_nothing_when_fewer_r2_objects_than_retention_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys = ["backups/postgres/20260701T000000Z.dump"]
    delete_calls: list[dict[str, Any]] = []

    class _FakeS3Client:
        def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
            return {"Contents": [{"Key": key} for key in keys]}

        def delete_object(self, **kwargs: Any) -> None:
            delete_calls.append(kwargs)

    monkeypatch.setattr(backup_module.boto3, "client", lambda *args, **kwargs: _FakeS3Client())

    deleted = prune_old_backups(retention_count=14, credentials=_configured_r2_credentials())

    assert deleted == []
    assert delete_calls == []


def test_run_backup_verifies_before_uploading_then_prunes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup_module, "run_pg_dump", lambda database_url: b"dump-bytes")
    monkeypatch.setattr(
        backup_module.DatabaseSettings, "database_url", property(lambda self: "postgresql://x/y"), raising=False
    )

    call_order: list[str] = []

    def fake_verify(dump: bytes) -> None:
        assert dump == b"dump-bytes"
        call_order.append("verify")

    def fake_upload(data: bytes, relative_path: str, credentials: BackupR2Credentials | None = None) -> str:
        call_order.append("upload")
        return "backups/postgres/whatever.dump"

    def fake_prune(retention_count: int = 14, credentials: BackupR2Credentials | None = None) -> list[str]:
        call_order.append("prune")
        assert retention_count == 14
        return ["backups/postgres/old.dump"]

    monkeypatch.setattr(backup_module, "verify_backup_restorable", fake_verify)
    monkeypatch.setattr(backup_module, "upload_backup", fake_upload)
    monkeypatch.setattr(backup_module, "prune_old_backups", fake_prune)

    destination = run_backup()

    assert destination == "backups/postgres/whatever.dump"
    assert call_order == ["verify", "upload", "prune"]


def test_run_backup_does_not_upload_or_prune_when_verification_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup_module, "run_pg_dump", lambda database_url: b"dump-bytes")
    monkeypatch.setattr(
        backup_module.DatabaseSettings, "database_url", property(lambda self: "postgresql://x/y"), raising=False
    )

    def fake_verify(dump: bytes) -> None:
        raise RuntimeError("restore failed")

    called: list[str] = []
    monkeypatch.setattr(backup_module, "verify_backup_restorable", fake_verify)
    monkeypatch.setattr(backup_module, "upload_backup", lambda *a, **k: called.append("upload") or "unused")  # type: ignore[func-returns-value]
    monkeypatch.setattr(backup_module, "prune_old_backups", lambda *a, **k: called.append("prune") or [])  # type: ignore[func-returns-value]

    with pytest.raises(RuntimeError):
        run_backup()

    assert called == []
