"""Back up a cache file's current bytes to Cloudflare R2 (S3-compatible), falling back to local disk.

Per `docs/accounting/architecture.md`'s caching rule, a derived cache file
(e.g. `AccountingConfig.exchange_rates_csv_path`) is disposable — cheap to
delete and regenerate from the raw archive. But "disposable" only covers
the case where the file is simply *missing*; it says nothing about a file
that's still *present* but corrupted (a botched write outside the app's
control, a Docker volume issue) and can no longer be parsed. This module
covers exactly that case: every successful write adds a new timestamped
backup version, restorable from whichever is newest, so a corrupted cache
can be transparently repaired instead of silently treated as empty. It has
nothing to do with a *failed fetch*: a failed fetch (network error,
upstream API down) never touches the existing file in the first place, so
it's already left stale-but-valid and needs no backup to recover from.

Every version is timestamped the same way `db.backup` names its own dumps
(`{UTC timestamp}{suffix}` — see `db.timestamped_backups`), pruned down to
the newest `BACKUP_RETENTION_COUNT` (`db.backup.get_backup_settings`) —
the identical env var and retention count `db.backup` itself uses, not a
separate cache-specific setting.

A deliberate duplicate of `trades.utils.cache_backup`, not a shared import
from it — see `accounting.utils.statement_archive`'s own docstring for why
accounting keeps its own copy of infra code like this rather than
depending on `trades` for something with nothing to do with investments.
The retention mechanics themselves aren't duplicated a third time, though
— both this module and `trades.utils.cache_backup` call straight through
to `db.timestamped_backups`, since `db` is the one layer both packages are
already allowed to depend on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from db.backup import get_backup_settings
from db.timestamped_backups import prune_local_timestamped_files, prune_r2_timestamped_objects, timestamped_filename

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


def backup_cache_file(
    local_path: Path,
    backup_key: str,
    credentials: CacheBackupR2Credentials | None = None,
    *,
    taken_at: datetime | None = None,
) -> None:
    """Upload `local_path`'s current bytes as a new timestamped backup for `backup_key`, then prune old ones.

    Meant to be called right after a cache file has just been written by a
    fully successful fetch (e.g. right after
    `accounting.utils.io_utils.write_csv_atomic` succeeds) — never on a
    stale-but-still-valid file left untouched by a failed fetch.

    Parameters
    ----------
    local_path
        The cache file to back up; read in full and uploaded verbatim.
    backup_key
        This cache file's own backup namespace, e.g. `"exchange_rates.csv"`
        — every version for this key is stored underneath it, as
        `f"{_R2_PREFIX}/{backup_key}/{timestamp}"` or
        `_LOCAL_BACKUP_ROOT / backup_key / {timestamp}`.
    credentials
        R2 credentials to use; defaults to `get_cache_backup_r2_credentials()`.
    taken_at
        When this version was taken; defaults to now. Only ever overridden
        by tests that need two distinct versions within the same second —
        real callers always take the default.
    """
    data = local_path.read_bytes()
    filename = timestamped_filename(taken_at if taken_at is not None else datetime.now(tz=UTC), local_path.suffix)
    retention_count = get_backup_settings().retention_count
    resolved = (credentials if credentials is not None else get_cache_backup_r2_credentials()).resolve()
    if resolved is None:
        version_dir = _LOCAL_BACKUP_ROOT / backup_key
        if version_dir.exists() and not version_dir.is_dir():
            # A single-file backup from before this module kept multiple
            # versions — same path, now needs to be a directory instead.
            version_dir.unlink()
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / filename).write_bytes(data)
        prune_local_timestamped_files(version_dir, f"*{local_path.suffix}", retention_count)
        return
    client = _client(resolved)
    client.put_object(Bucket=resolved.bucket_name, Key=f"{_R2_PREFIX}/{backup_key}/{filename}", Body=data)
    prune_r2_timestamped_objects(client, resolved.bucket_name, f"{_R2_PREFIX}/{backup_key}/", retention_count)


def restore_cache_file(local_path: Path, backup_key: str, credentials: CacheBackupR2Credentials | None = None) -> bool:
    """Overwrite `local_path` with the newest backed-up version for `backup_key`, if any exist.

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
    """
    resolved = (credentials if credentials is not None else get_cache_backup_r2_credentials()).resolve()
    if resolved is None:
        versions = sorted(
            (_LOCAL_BACKUP_ROOT / backup_key).glob(f"*{local_path.suffix}"), key=lambda p: p.name, reverse=True
        )
        if not versions:
            return False
        data = versions[0].read_bytes()
    else:
        client = _client(resolved)
        response = client.list_objects_v2(Bucket=resolved.bucket_name, Prefix=f"{_R2_PREFIX}/{backup_key}/")
        keys = sorted((entry["Key"] for entry in response.get("Contents", [])), reverse=True)
        if not keys:
            return False
        data = client.get_object(Bucket=resolved.bucket_name, Key=keys[0])["Body"].read()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    return True
