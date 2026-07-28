"""Newest-N backup and restore for `trades`'s local cache files, sharing `db.backup`'s own retention setting.

This guards against on-disk *corruption* of an already-written cache file
(a botched write outside the app's control, a Docker volume issue) — not
against a failed *fetch*. A failed fetch (network error, bad scrape) never
touches the existing file in the first place, since every cache writer in
this package only calls `trades.utils.io_utils.write_csv_atomic` after a
fully successful fetch; the existing, still-valid file is simply left
alone, stale but not broken. That already-correct behavior needs no help
from this module.

Every successful write adds a new timestamped backup (mirroring
`db.backup`'s own `{UTC timestamp}{suffix}` naming — see
`db.timestamped_backups`) under a per-cache-file namespace, then prunes
that same namespace down to the newest `BACKUP_RETENTION_COUNT`
(`db.backup.get_backup_settings`) — the identical env var and retention
count `db.backup` itself uses, not a separate cache-specific setting.
`restore_cache_file` always restores whichever backup is newest.

Cloudflare R2 when `R2_ACCOUNT_ID` etc. are set in `.env`, local disk under
`data/backups/cache/trades/` otherwise — the same `configured()`-gated
fallback `db.backup.BackupR2Credentials`/`trades.utils.statement_archive.R2Credentials`
already use. A deliberate duplicate of `accounting.utils.cache_backup`, not
a shared import from it — see `trades.utils.statement_archive`'s own
docstring for why this repo keeps separate copies of infra code like this
between the two packages. The retention mechanics themselves aren't
duplicated a third time, though — both this module and
`accounting.utils.cache_backup` call straight through to
`db.timestamped_backups`, since `db` is the one layer both packages are
already allowed to depend on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from db.backup import get_backup_settings
from db.timestamped_backups import prune_local_timestamped_files, prune_r2_timestamped_objects, timestamped_filename

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


def _local_backup_dir(backup_key: str) -> Path:
    """Where `backup_key`'s local-disk backup versions live, when R2 isn't configured.

    Returns
    -------
    Path
    """
    return _LOCAL_BACKUP_ROOT / backup_key


def backup_cache_file(
    local_path: Path,
    backup_key: str,
    credentials: CacheBackupR2Credentials | None = None,
    *,
    taken_at: datetime | None = None,
) -> None:
    """Upload `local_path`'s current bytes as a new timestamped backup for `backup_key`, then prune old ones.

    Call this right after a cache writer's `write_csv_atomic` call
    succeeds, so a backup version only ever holds bytes that were
    themselves the result of a fully successful write.

    Parameters
    ----------
    local_path
        The cache file whose current on-disk bytes should become the new backup version.
    backup_key
        This cache file's own backup namespace, relative to
        `_R2_PREFIX`/`_LOCAL_BACKUP_ROOT` (e.g. `"prices/AAPL.csv"`,
        `"cpi.csv"`, `"hysa_rates.csv"`) — every version for this key is
        stored underneath it.
    credentials
        Defaults to `get_cache_backup_r2_credentials()`.
    taken_at
        When this version was taken; defaults to now. Only ever overridden
        by tests that need two distinct versions within the same second —
        real callers always take the default.
    """
    resolved = credentials if credentials is not None else get_cache_backup_r2_credentials()
    data = local_path.read_bytes()
    filename = timestamped_filename(taken_at if taken_at is not None else datetime.now(tz=UTC), local_path.suffix)
    retention_count = get_backup_settings().retention_count
    if not resolved.configured():
        version_dir = _local_backup_dir(backup_key)
        if version_dir.exists() and not version_dir.is_dir():
            # A single-file backup from before this module kept multiple
            # versions — same path, now needs to be a directory instead.
            version_dir.unlink()
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / filename).write_bytes(data)
        prune_local_timestamped_files(version_dir, f"*{local_path.suffix}", retention_count)
        return
    assert resolved.bucket_name is not None  # noqa: S101 — configured() above already guarantees this; documents the invariant for mypy
    client = _client(resolved)
    client.put_object(Bucket=resolved.bucket_name, Key=f"{_R2_PREFIX}/{backup_key}/{filename}", Body=data)
    prune_r2_timestamped_objects(client, resolved.bucket_name, f"{_R2_PREFIX}/{backup_key}/", retention_count)


def restore_cache_file(local_path: Path, backup_key: str, credentials: CacheBackupR2Credentials | None = None) -> bool:
    """Overwrite `local_path` with the newest backed-up version for `backup_key`, if any exist.

    Parameters
    ----------
    local_path
        Where to write the restored bytes, overwriting whatever's there now.
    backup_key
        Which backup namespace to restore from (see `backup_cache_file`).
    credentials
        Defaults to `get_cache_backup_r2_credentials()`.

    Returns
    -------
    bool
        Whether a backup existed and was restored — `False` (and
        `local_path` left untouched) if nothing's ever been backed up for
        this key yet.
    """
    resolved = credentials if credentials is not None else get_cache_backup_r2_credentials()
    if not resolved.configured():
        versions = sorted(
            _local_backup_dir(backup_key).glob(f"*{local_path.suffix}"), key=lambda p: p.name, reverse=True
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
