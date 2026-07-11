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

import subprocess  # noqa: S404 — used only via the fixed, non-shell argv in run_pg_dump below
from datetime import UTC, datetime
from pathlib import Path

import boto3
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

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


def run_pg_dump(database_url: str) -> bytes:
    """Run `pg_dump` against `database_url`, returning the dump's bytes (custom format).

    `check=True` below means a non-zero `pg_dump` exit propagates as
    `subprocess.CalledProcessError` — deliberately uncaught, so a broken
    dump surfaces loudly rather than silently uploading nothing.

    Parameters
    ----------
    database_url
        A `postgresql://`/`postgresql+psycopg://` connection string —
        `pg_dump` accepts either scheme's connection string form directly.

    Returns
    -------
    bytes
        The dump, in `pg_dump`'s `--format=custom` binary form — restorable
        with `pg_restore`, and far smaller than plain SQL text.
    """
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell, database_url is our own config, not user input
        ["pg_dump", "--format=custom", "--dbname", database_url],  # noqa: S607 — resolved via PATH, standard practice
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


def run_backup() -> str:
    """Dump the whole database and upload it, end to end — the one function the cron entry calls.

    Returns
    -------
    str
        Wherever the dump ended up (see `upload_backup`).
    """
    dump = run_pg_dump(DatabaseSettings().database_url)  # type: ignore[call-arg]  # see db.session.get_engine's own note
    relative_path = backup_relative_path(datetime.now(tz=UTC))
    return upload_backup(dump, relative_path)


if __name__ == "__main__":
    destination = run_backup()
    print(f"Backed up to {destination}")  # noqa: T201 — this is a CLI entrypoint, not library code
