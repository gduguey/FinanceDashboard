"""Back up the whole Postgres database to Cloudflare R2, verbatim, on a schedule.

Run via `python -m db.backup` (see `docs/server-setup/maintenance.md` for
the cron entry that actually schedules it). Uses `DatabaseSettings` (the
migration-owning role), not `AppRuntimeDatabaseSettings` — a backup needs
to read every row in every table regardless of Row-Level Security, which
is exactly what the app's own restricted `app_runtime` role must never be
trusted with implicitly.

A deliberate duplicate of `trades.utils.statement_archive`'s R2 credential
handling, not a shared import from it — `db` is the one layer both
`accounting` and `trades` depend on, never the reverse, so it can't import
either package's copy without inverting that dependency direction (see
`docs/architecture.md`).
"""

from __future__ import annotations

import subprocess  # noqa: S404 — used only via the fixed, non-shell argv in run_pg_dump/verify_backup_restorable below
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import boto3
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from db.settings import DatabaseSettings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOCAL_BACKUP_DIR = _REPO_ROOT / "data" / "backups" / "postgres"
_R2_PREFIX = "backups/postgres"


class BackupR2Credentials(BaseSettings):
    """Cloudflare R2 credentials for backup uploads, read from `.env` or the environment.

    Every field is optional: their absence just means R2 isn't set up yet,
    not a startup error — a missing configuration falls back to writing
    the dump to local disk instead (see `run_backup`).
    """

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    account_id: SecretStr | None = Field(default=None, validation_alias="R2_ACCOUNT_ID")
    access_key_id: SecretStr | None = Field(default=None, validation_alias="R2_ACCESS_KEY_ID")
    secret_access_key: SecretStr | None = Field(default=None, validation_alias="R2_SECRET_ACCESS_KEY")
    bucket_name: str | None = Field(default=None, validation_alias="R2_BUCKET_NAME")
    endpoint_url: str | None = Field(default=None, validation_alias="R2_ENDPOINT_URL")

    def configured(self) -> bool:
        """Whether every field needed to actually reach R2 is set.

        Returns
        -------
        bool
        """
        return all([self.account_id, self.access_key_id, self.secret_access_key, self.bucket_name, self.endpoint_url])


def get_backup_r2_credentials() -> BackupR2Credentials:
    """Read R2 credentials for backups from `.env`/the environment.

    A thin wrapper so tests can monkeypatch this one seam, the same as
    `trades.utils.statement_archive.get_r2_credentials`.

    Returns
    -------
    BackupR2Credentials
    """
    return BackupR2Credentials()


def _libpq_url(database_url: str) -> str:
    """Strip a SQLAlchemy driver suffix (e.g. `postgresql+psycopg://`) down to a plain `postgresql://` URL.

    `pg_dump`/`pg_restore` link against libpq directly, not SQLAlchemy —
    libpq's own URI parser only recognizes the bare `postgresql://`/
    `postgres://` schemes. Handed a `+driver` suffix instead, it doesn't
    raise a clear "invalid URL" error; it silently fails to parse the
    string as a URI at all and treats the *entire string* as a literal
    database name, attempting a default local-socket connection with that
    as the dbname — a confusing, silent misbehavior, not a clean rejection
    (confirmed directly: `pg_dump --dbname "postgresql+psycopg://..."`
    tries to connect to the local socket and complains that a database
    named `postgresql+psycopg://...` doesn't exist). This only matters for
    local, non-Docker use of this module — `.env.docker` already uses the
    plain scheme.

    Parameters
    ----------
    database_url
        Any `postgresql[+driver]://` connection string.

    Returns
    -------
    str
        The same URL with its scheme normalized to plain `postgresql://`.
    """
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def run_pg_dump(database_url: str) -> bytes:
    """Run `pg_dump` against `database_url`, returning the dump's bytes (custom format).

    `check=True` below means a non-zero `pg_dump` exit propagates as
    `subprocess.CalledProcessError` — deliberately uncaught, so a broken
    dump surfaces loudly rather than silently uploading nothing.

    Parameters
    ----------
    database_url
        A `postgresql://`/`postgresql+psycopg://` connection string — either
        form works, `_libpq_url` normalizes it before it ever reaches `pg_dump`.

    Returns
    -------
    bytes
        The dump, in `pg_dump`'s `--format=custom` binary form — restorable
        with `pg_restore`, and far smaller than plain SQL text.
    """
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell, database_url is our own config, not user input
        ["pg_dump", "--format=custom", "--dbname", _libpq_url(database_url)],  # noqa: S607 — resolved via PATH
        capture_output=True,
        check=True,
    )
    return result.stdout


def backup_relative_path(taken_at: datetime) -> str:
    """Build the relative path one backup taken at `taken_at` is stored under, both locally and in R2.

    Parameters
    ----------
    taken_at
        When the dump was taken; formatted to the second, UTC.

    Returns
    -------
    str
    """
    return f"{taken_at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}.dump"


def upload_backup(data: bytes, relative_path: str, credentials: BackupR2Credentials | None = None) -> str:
    """Write `data` to R2 under `{_R2_PREFIX}/{relative_path}`, or to local disk if R2 isn't configured.

    Parameters
    ----------
    data
        The dump's bytes.
    relative_path
        Where to store it, e.g. from `backup_relative_path`.
    credentials
        Defaults to `get_backup_r2_credentials()`.

    Returns
    -------
    str
        Where it was written: an R2 key (`{_R2_PREFIX}/...`) if R2 is
        configured, otherwise a local filesystem path — the caller doesn't
        need to know which, only that it's now durably stored.
    """
    resolved = credentials if credentials is not None else get_backup_r2_credentials()
    if not resolved.configured():
        _LOCAL_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        path = _LOCAL_BACKUP_DIR / relative_path
        path.write_bytes(data)
        return str(path)

    key = f"{_R2_PREFIX}/{relative_path}"
    client = boto3.client(
        "s3",
        endpoint_url=resolved.endpoint_url,
        aws_access_key_id=resolved.access_key_id.get_secret_value() if resolved.access_key_id else None,
        aws_secret_access_key=resolved.secret_access_key.get_secret_value() if resolved.secret_access_key else None,
        region_name="auto",
    )
    client.put_object(Bucket=resolved.bucket_name, Key=key, Body=data)
    return key


def verify_backup_restorable(dump: bytes) -> None:
    """Prove a dump is actually restorable by restoring it into a disposable scratch database.

    Connects to the Postgres *server* (not a specific app database) as
    `DatabaseSettings`'s role — a cluster superuser in this project's
    Docker setup, so it has the `CREATEDB`/`DROP DATABASE` privileges this
    needs — via a maintenance connection pointed at the `postgres` system
    database. `CREATE DATABASE` can't run inside a transaction block, so
    the maintenance engine uses `AUTOCOMMIT`.

    A failed `pg_restore` (`subprocess.CalledProcessError`) propagates
    uncaught — a broken dump must surface loudly, never be swallowed. The
    scratch database and temp file are always cleaned up in a `finally`
    block, regardless of whether the restore succeeded.

    Parameters
    ----------
    dump
        The dump's bytes, in `pg_dump --format=custom` form (see `run_pg_dump`).
    """
    maintenance_url = make_url(DatabaseSettings().database_url).set(database="postgres")  # type: ignore[call-arg]
    engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    scratch_db_name = f"backup_verify_{uuid.uuid4().hex}"
    scratch_url = maintenance_url.set(database=scratch_db_name)

    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{scratch_db_name}"'))

    temp_file = tempfile.NamedTemporaryFile(delete=False)  # noqa: SIM115 — needs a stable path for pg_restore's argv
    temp_path = Path(temp_file.name)
    try:
        temp_path.write_bytes(dump)
        subprocess.run(  # noqa: S603 — fixed argv, no shell, every value is our own config, not user input
            [  # noqa: S607 — resolved via PATH, standard practice
                "pg_restore",
                "--clean",
                "--if-exists",
                "--dbname",
                _libpq_url(scratch_url.render_as_string(hide_password=False)),
                str(temp_path),
            ],
            capture_output=True,
            check=True,
        )
    finally:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch_db_name}" WITH (FORCE)'))
        engine.dispose()
        temp_path.unlink(missing_ok=True)


def prune_old_backups(retention_count: int = 14, credentials: BackupR2Credentials | None = None) -> list[str]:
    """Delete every backup beyond the newest `retention_count`, wherever they're stored.

    Backups are named `{UTC timestamp}.dump` (see `backup_relative_path`),
    which already sorts lexicographically the same as chronologically —
    no separate timestamp parsing needed to find the newest ones.

    Parameters
    ----------
    retention_count
        How many of the newest backups to keep.
    credentials
        Defaults to `get_backup_r2_credentials()`; falls back to pruning
        `_LOCAL_BACKUP_DIR` if R2 isn't configured, the same fallback
        `upload_backup` uses.

    Returns
    -------
    list[str]
        Every backup that was deleted: local paths, or R2 keys.
    """
    resolved = credentials if credentials is not None else get_backup_r2_credentials()
    if not resolved.configured():
        backups = sorted(_LOCAL_BACKUP_DIR.glob("*.dump"), key=lambda path: path.name, reverse=True)
        to_delete = backups[retention_count:]
        for path in to_delete:
            path.unlink()
        return [str(path) for path in to_delete]

    client = boto3.client(
        "s3",
        endpoint_url=resolved.endpoint_url,
        aws_access_key_id=resolved.access_key_id.get_secret_value() if resolved.access_key_id else None,
        aws_secret_access_key=resolved.secret_access_key.get_secret_value() if resolved.secret_access_key else None,
        region_name="auto",
    )
    response = client.list_objects_v2(Bucket=resolved.bucket_name, Prefix=f"{_R2_PREFIX}/")
    keys = sorted((entry["Key"] for entry in response.get("Contents", [])), reverse=True)
    to_delete_keys = keys[retention_count:]
    for key in to_delete_keys:
        client.delete_object(Bucket=resolved.bucket_name, Key=key)
    return to_delete_keys


def run_backup(retention_count: int = 14) -> str:
    """Dump the whole database, verify it, upload it, then prune old backups — the one function the cron entry calls.

    Order matters: the dump is verified restorable *before* it's uploaded
    or anything is pruned. If verification raises, this stops immediately
    — nothing is uploaded and no existing backup is touched, so a broken
    dump can never replace or sit alongside a good one.

    Parameters
    ----------
    retention_count
        How many of the newest backups to keep after this one uploads;
        passed straight through to `prune_old_backups`.

    Returns
    -------
    str
        Wherever the dump ended up (see `upload_backup`).
    """
    dump = run_pg_dump(DatabaseSettings().database_url)  # type: ignore[call-arg]  # see db.session.get_engine's own note
    verify_backup_restorable(dump)
    relative_path = backup_relative_path(datetime.now(tz=UTC))
    destination = upload_backup(dump, relative_path)
    prune_old_backups(retention_count)
    return destination


if __name__ == "__main__":
    dump_bytes = run_pg_dump(DatabaseSettings().database_url)  # type: ignore[call-arg]
    verify_backup_restorable(dump_bytes)
    destination = upload_backup(dump_bytes, backup_relative_path(datetime.now(tz=UTC)))
    pruned = prune_old_backups()
    print(f"Backed up to {destination}")  # noqa: T201 — this is a CLI entrypoint, not library code
    print(f"Pruned {len(pruned)} old backup(s)")  # noqa: T201 — this is a CLI entrypoint, not library code
