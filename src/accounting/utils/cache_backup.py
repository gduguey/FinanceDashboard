"""Back up a cache file's current bytes to Cloudflare R2 (S3-compatible), falling back to local disk.

Per `docs/accounting/architecture.md`'s caching rule, a derived cache file
(e.g. `AccountingConfig.exchange_rates_csv_path`) is disposable — cheap to
delete and regenerate from the raw archive. But "disposable" only covers
the case where the file is simply *missing*; it says nothing about a file
that's still *present* but corrupted (a botched write outside the app's
control, a Docker volume issue) and can no longer be parsed. This module
covers exactly that case: it keeps exactly one "last known good" copy of a
cache file — no history, no retention logic, just overwrite the single
backup slot every time a fresh, successfully-fetched file is written — so
a corrupted cache can be transparently repaired instead of silently
treated as empty. It has nothing to do with a *failed fetch*: a failed
fetch (network error, upstream API down) never touches the existing file
in the first place, so it's already left stale-but-valid and needs no
backup to recover from.

A deliberate duplicate of `trades.utils.cache_backup`, not a shared import
from it — see `accounting.utils.statement_archive`'s own docstring for why
accounting keeps its own copy of infra code like this rather than
depending on `trades` for something with nothing to do with investments.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOCAL_BACKUP_ROOT = _REPO_ROOT / "data" / "backups" / "cache" / "accounting"
_R2_PREFIX = "cache-backups/accounting"


@dataclass(frozen=True)
class _ResolvedR2Credentials:
    """R2 credentials known to be fully present — see `CacheBackupR2Credentials.resolve`."""

    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket_name: str
    endpoint_url: str


class CacheBackupR2Credentials(BaseSettings):
    """Cloudflare R2 credentials for cache-file backups, read from `.env` or the environment.

    Every field is optional: their absence just means R2 isn't set up yet,
    not a startup error — `resolve()` is how a caller finds out which case
    it's in, and `backup_cache_file`/`restore_cache_file` fall back to
    local disk when it returns `None`.
    """

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    account_id: SecretStr | None = Field(default=None, validation_alias="R2_ACCOUNT_ID")
    access_key_id: SecretStr | None = Field(default=None, validation_alias="R2_ACCESS_KEY_ID")
    secret_access_key: SecretStr | None = Field(default=None, validation_alias="R2_SECRET_ACCESS_KEY")
    bucket_name: str | None = Field(default=None, validation_alias="R2_BUCKET_NAME")
    endpoint_url: str | None = Field(default=None, validation_alias="R2_ENDPOINT_URL")

    def resolve(self) -> _ResolvedR2Credentials | None:
        """All five fields, unwrapped, or `None` if any is missing.

        Returns
        -------
        _ResolvedR2Credentials or None
            `None` means "R2 isn't configured, use local disk" — never
            raises, since running without R2 set up is an expected,
            supported state, not an error.
        """
        account_id, access_key_id, secret_access_key = self.account_id, self.access_key_id, self.secret_access_key
        bucket_name, endpoint_url = self.bucket_name, self.endpoint_url
        if (
            account_id is None
            or access_key_id is None
            or secret_access_key is None
            or bucket_name is None
            or endpoint_url is None
        ):
            return None
        return _ResolvedR2Credentials(
            account_id=account_id.get_secret_value(),
            access_key_id=access_key_id.get_secret_value(),
            secret_access_key=secret_access_key.get_secret_value(),
            bucket_name=bucket_name,
            endpoint_url=endpoint_url,
        )


def get_cache_backup_r2_credentials() -> CacheBackupR2Credentials:
    """Read R2 credentials for cache-file backups from `.env`/the environment.

    A thin wrapper around `CacheBackupR2Credentials()` so tests can
    intercept R2 by monkeypatching this one seam, the same as
    `accounting.utils.statement_archive.get_r2_credentials`.

    Returns
    -------
    CacheBackupR2Credentials
    """
    return CacheBackupR2Credentials()


def _client(resolved: _ResolvedR2Credentials) -> Any:  # noqa: ANN401 — boto3 ships no typed client
    """Build a boto3 S3 client pointed at `resolved.endpoint_url`.

    Parameters
    ----------
    resolved
        Resolved R2 credentials.

    Returns
    -------
    Any
    """
    return boto3.client(
        "s3",
        endpoint_url=resolved.endpoint_url,
        aws_access_key_id=resolved.access_key_id,
        aws_secret_access_key=resolved.secret_access_key,
        region_name="auto",
    )


def backup_cache_file(local_path: Path, backup_key: str, credentials: CacheBackupR2Credentials | None = None) -> None:
    """Upload `local_path`'s current bytes as the one "last known good" backup for `backup_key`.

    Overwrites whatever was backed up before for this key, no history
    kept. Meant to be called right after a cache file has just been
    written by a fully successful fetch (e.g. right after
    `accounting.utils.io_utils.write_csv_atomic` succeeds) — never on a
    stale-but-still-valid file left untouched by a failed fetch.

    Parameters
    ----------
    local_path
        The cache file to back up; read in full and uploaded verbatim.
    backup_key
        What distinguishes this cache file from any other, e.g.
        `"exchange_rates.csv"`. Becomes the R2 key
        `f"{_R2_PREFIX}/{backup_key}"`, or the local fallback path
        `_LOCAL_BACKUP_ROOT / backup_key`.
    credentials
        R2 credentials to use; defaults to `get_cache_backup_r2_credentials()`.
    """
    data = local_path.read_bytes()
    resolved = (credentials if credentials is not None else get_cache_backup_r2_credentials()).resolve()
    if resolved is None:
        backup_path = _LOCAL_BACKUP_ROOT / backup_key
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(data)
        return
    _client(resolved).put_object(Bucket=resolved.bucket_name, Key=f"{_R2_PREFIX}/{backup_key}", Body=data)


def restore_cache_file(local_path: Path, backup_key: str, credentials: CacheBackupR2Credentials | None = None) -> bool:
    """Overwrite `local_path` with the backed-up bytes for `backup_key`, if one exists.

    Parameters
    ----------
    local_path
        The cache file to repair in place.
    backup_key
        The same key `backup_cache_file` was called with.
    credentials
        R2 credentials to use; defaults to `get_cache_backup_r2_credentials()`.

    Returns
    -------
    bool
        Whether a backup existed and was restored — `False` (and
        `local_path` left untouched) if nothing's ever been backed up for
        this key yet.

    Raises
    ------
    botocore.exceptions.ClientError
        If R2 is configured and the lookup fails for any reason other
        than the backup not existing.
    """
    resolved = (credentials if credentials is not None else get_cache_backup_r2_credentials()).resolve()
    if resolved is None:
        backup_path = _LOCAL_BACKUP_ROOT / backup_key
        if not backup_path.exists():
            return False
        data = backup_path.read_bytes()
    else:
        try:
            response = _client(resolved).get_object(Bucket=resolved.bucket_name, Key=f"{_R2_PREFIX}/{backup_key}")
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                return False
            raise
        data = response["Body"].read()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    return True
