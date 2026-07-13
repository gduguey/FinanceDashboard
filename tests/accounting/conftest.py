"""Fixtures shared across `tests/accounting/`.

Guards `accounting.utils.cache_backup` the same way `tests/conftest.py`'s
`_no_r2_by_default` already guards `accounting.utils.statement_archive`:
`CacheBackupR2Credentials` reads `.env` directly (via `model_config`'s
`env_file`), so deleting `R2_*` from `os.environ` alone (which
`_no_r2_by_default` does) isn't enough to stop it resolving this repo's
real R2 credentials — a pydantic-settings model has two independent
sources for a real credential (the environment and its own `.env` file
fallback), and both have to be closed for the same model: clearing the
env var alone still leaves the `.env`-file fallback able to silently
resolve the same real secret, and passing `_env_file=None` alone does
nothing about a value already sitting in `os.environ`. Without this, any
accounting test that exercises `exchange_rates.update_rate_history_cache`/
`load_rate_history` (which call `backup_cache_file`/`restore_cache_file`
with no explicit `credentials`) would silently read from and write to the
real production R2 bucket whenever this developer's own `.env` has R2
configured.

This also redirects the local-disk fallback (`cache_backup._LOCAL_BACKUP_ROOT`)
into each test's own `tmp_path`, so a test that never mentions backups at
all can't leave stray files under this repo's own `data/backups/cache/accounting/`.
"""

from __future__ import annotations

import pytest

from accounting.utils import cache_backup


@pytest.fixture(autouse=True)
def _no_cache_backup_r2_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(
        cache_backup, "get_cache_backup_r2_credentials", lambda: cache_backup.CacheBackupR2Credentials(_env_file=None)
    )
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "cache-backups")
