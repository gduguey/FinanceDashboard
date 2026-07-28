"""Tests for `db.settings`: which Postgres connection string each role actually uses.

`AppRuntimeDatabaseSettings` and `DatabaseSettings` must each require their
own env var and never silently accept the other's — a fallback here would
mean RLS policies exist but the app quietly isn't actually protected by
them (see `db/settings.py`'s own docstring).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from db.settings import AppRuntimeDatabaseSettings, DatabaseSettings


def test_app_runtime_database_settings_uses_database_url_app(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL_APP", "postgresql://app_runtime:y@host/db")

    assert AppRuntimeDatabaseSettings(_env_file=None).database_url == "postgresql://app_runtime:y@host/db"


def test_app_runtime_database_settings_never_falls_back_to_database_url(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://finance:x@host/db")
    monkeypatch.delenv("DATABASE_URL_APP", raising=False)

    with pytest.raises(ValidationError, match="DATABASE_URL_APP"):
        AppRuntimeDatabaseSettings(_env_file=None)


def test_database_settings_never_falls_back_to_database_url_app(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL_APP", "postgresql://app_runtime:y@host/db")

    with pytest.raises(ValidationError, match="DATABASE_URL"):
        DatabaseSettings(_env_file=None)
