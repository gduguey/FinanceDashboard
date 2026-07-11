"""Test fixtures scoped to `trades` tests.

Disables R2 for `trades.utils.cache_backup` across every `trades` test by
default, the same way the repo-wide `tests/conftest.py`'s `_no_r2_by_default`
fixture already does for `trades.utils.statement_archive`/`accounting.utils.statement_archive`
— see that fixture's own docstring for why both a monkeypatched credentials
getter *and* stripped `R2_*` env vars are needed. Kept local to `tests/trades/`
rather than added to the shared top-level fixture so this package's test
setup doesn't need to touch a file `accounting`'s parallel `cache_backup`
work also touches.
"""

from __future__ import annotations

import pytest

from trades.utils import cache_backup


@pytest.fixture(autouse=True)
def _no_r2_by_default_for_cache_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cache_backup, "get_cache_backup_r2_credentials", lambda: cache_backup.CacheBackupR2Credentials(_env_file=None)
    )
