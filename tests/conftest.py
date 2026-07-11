"""Shared test fixtures.

Disables R2 across the whole suite by default so every existing test keeps
exercising the local-disk fallback path deterministically, regardless of
what's actually configured in the developer's own `.env` — mirrors how
`IbkrFlexCredentials` tests pass `_env_file=None` rather than relying on
whatever `.env` happens to contain. Tests that specifically want to
exercise the R2 branch construct their own configured `R2Credentials`
instance and pass it to `StatementArchive` directly instead of relying on
this default.

`_env_file=None` alone only disables reading `.env` as a file — it does
NOT stop pydantic-settings from reading real `R2_*` values straight out of
the process environment, which is exactly what happens under the VS Code
Python extension: it auto-loads `${workspaceFolder}/.env` (its default
`python.envFile`) into every test run's environment. Left unguarded, that
makes the whole suite silently read from and write to the real production
R2 bucket — including tests that construct `R2Credentials` directly
rather than through `get_r2_credentials()`. So this fixture also strips
the real `R2_*` variables from `os.environ` itself, which covers every
construction site, not just the ones monkeypatched below.
"""

from __future__ import annotations

import pytest

import accounting.utils.statement_archive as accounting_storage
import trades.utils.statement_archive as trades_storage

_R2_ENV_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_ENDPOINT_URL")


@pytest.fixture(autouse=True)
def _no_r2_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _R2_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(trades_storage, "get_r2_credentials", lambda: trades_storage.R2Credentials(_env_file=None))
    monkeypatch.setattr(
        accounting_storage, "get_r2_credentials", lambda: accounting_storage.R2Credentials(_env_file=None)
    )
