"""Shared test fixtures.

Disables R2 across the whole suite by default so every existing test keeps
exercising the local-disk fallback path deterministically, regardless of
what's actually configured in the developer's own `.env` — mirrors how
`IbkrFlexCredentials` tests pass `_env_file=None` rather than relying on
whatever `.env` happens to contain. Tests that specifically want to
exercise the R2 branch construct their own configured `R2Credentials`
instance and pass it to `StatementArchive` directly instead of relying on
this default.
"""

from __future__ import annotations

import pytest

import accounting.utils.statement_archive as accounting_storage
import trades.utils.statement_archive as trades_storage


@pytest.fixture(autouse=True)
def _no_r2_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trades_storage, "get_r2_credentials", lambda: trades_storage.R2Credentials(_env_file=None))
    monkeypatch.setattr(
        accounting_storage, "get_r2_credentials", lambda: accounting_storage.R2Credentials(_env_file=None)
    )
