"""One-slot "last known good" backup and restore for `trades`'s local cache files.

This guards against on-disk *corruption* of an already-written cache file
(a botched write outside the app's control, a Docker volume issue) — not
against a failed *fetch*. A failed fetch (network error, bad scrape) never
touches the existing file in the first place, since every cache writer in
this package only calls `trades.utils.io_utils.write_csv_atomic` after a
fully successful fetch; the existing, still-valid file is simply left
alone, stale but not broken. That already-correct behavior needs no help
from this module.

Unlike `db.backup`'s versioned, retained-for-14-days backups of the whole
database, this keeps exactly one backup per cache file: every successful
write overwrites whatever was backed up before, no history. There's
nothing to prune and nothing to pick a version from — `restore_cache_file`
always restores the one copy that exists, or reports that none does.

Cloudflare R2 when `R2_ACCOUNT_ID` etc. are set in `.env`, local disk under
`data/backups/cache/trades/` otherwise — the same `configured()`-gated
fallback `db.backup.BackupR2Credentials`/`trades.utils.statement_archive.R2Credentials`
already use. A deliberate duplicate of `accounting.utils.cache_backup`, not
a shared import from it — see `trades.utils.statement_archive`'s own
docstring for why this repo keeps separate copies of infra code like this
between the two packages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOCAL_BACKUP_ROOT = _REPO_ROOT / "data" / "backups" / "cache" / "trades"
_R2_PREFIX = "cache-backups/trades"


class CacheBackupR2Credentials(BaseSettings):
    """Cloudflare R2 credentials for cache-file backups, read from `.env` or the environment.

    Every field is optional: their absence just means R2 isn't set up yet,
    not a startup error — a missing configuration falls back to writing
    the backup to local disk instead (see `backup_cache_file`/`restore_cache_file`).
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


def get_cache_backup_r2_credentials() -> CacheBackupR2Credentials:
    """Read R2 credentials for cache backups from `.env`/the environment.

    A thin wrapper so tests can monkeypatch this one seam, the same as
    `trades.utils.statement_archive.get_r2_credentials`/`db.backup.get_backup_r2_credentials`.

    Returns
    -------
    CacheBackupR2Credentials
    """
    return CacheBackupR2Credentials()


def _client(credentials: CacheBackupR2Credentials) -> Any:  # noqa: ANN401 — boto3 ships no typed client
    """Build a boto3 S3 client from resolved, R2-configured credentials.

    Returns
    -------
    Any
        A boto3 S3 client pointed at `credentials.endpoint_url`.
    """
    access_key = credentials.access_key_id.get_secret_value() if credentials.access_key_id else None
    secret_key = credentials.secret_access_key.get_secret_value() if credentials.secret_access_key else None
    return boto3.client(
        "s3",
        endpoint_url=credentials.endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def _local_backup_path(backup_key: str) -> Path:
    """Where `backup_key`'s local-disk backup copy lives, when R2 isn't configured.

    Returns
    -------
    Path
    """
    return _LOCAL_BACKUP_ROOT / backup_key


def backup_cache_file(local_path: Path, backup_key: str, credentials: CacheBackupR2Credentials | None = None) -> None:
    """Upload `local_path`'s current bytes as the one "last known good" backup for `backup_key`.

    Overwrites whatever was backed up before — no history is kept. Call
    this right after a cache writer's `write_csv_atomic` call succeeds, so
    the backup slot only ever holds bytes that were themselves the result
    of a fully successful write.

    Parameters
    ----------
    local_path
        The cache file whose current on-disk bytes should become the backup.
    backup_key
        Where this file's backup lives, relative to `_R2_PREFIX`/`_LOCAL_BACKUP_ROOT`
        (e.g. `"prices/AAPL.csv"`, `"cpi.csv"`, `"hysa_rates.csv"`).
    credentials
        Defaults to `get_cache_backup_r2_credentials()`.
    """
    resolved = credentials if credentials is not None else get_cache_backup_r2_credentials()
    data = local_path.read_bytes()
    if not resolved.configured():
        path = _local_backup_path(backup_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return
    _client(resolved).put_object(Bucket=resolved.bucket_name, Key=f"{_R2_PREFIX}/{backup_key}", Body=data)


def restore_cache_file(local_path: Path, backup_key: str, credentials: CacheBackupR2Credentials | None = None) -> bool:
    """Overwrite `local_path` with the backed-up bytes for `backup_key`, if one exists.

    Parameters
    ----------
    local_path
        Where to write the restored bytes, overwriting whatever's there now.
    backup_key
        Which backup to restore (see `backup_cache_file`).
    credentials
        Defaults to `get_cache_backup_r2_credentials()`.

    Returns
    -------
    bool
        Whether a backup existed and was restored — `False` (and
        `local_path` left untouched) if nothing's ever been backed up for
        this key yet.

    Raises
    ------
    botocore.exceptions.ClientError
        If R2 is configured and the lookup fails for any reason other than
        the backup object not existing.
    """
    resolved = credentials if credentials is not None else get_cache_backup_r2_credentials()
    if not resolved.configured():
        path = _local_backup_path(backup_key)
        if not path.exists():
            return False
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(path.read_bytes())
        return True

    client = _client(resolved)
    try:
        response = client.get_object(Bucket=resolved.bucket_name, Key=f"{_R2_PREFIX}/{backup_key}")
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
            return False
        raise
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(response["Body"].read())
    return True
