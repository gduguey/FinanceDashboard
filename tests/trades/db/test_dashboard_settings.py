"""Tests for `trades.dashboard.settings.load_settings`/`save_settings`: the Postgres-backed dashboard preferences store."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from trades.dashboard.settings import DashboardSettings, load_settings, save_settings

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


def test_load_settings_with_nothing_saved_returns_defaults(db_session: Session, test_user_id: uuid.UUID) -> None:
    assert load_settings(db_session, test_user_id) == DashboardSettings()


def test_save_then_load_settings_round_trips_every_field(db_session: Session, test_user_id: uuid.UUID) -> None:
    settings = DashboardSettings(
        target_allocation_pct={"VOO": 60.0, "BND": 40.0},
        hysa_bank_id="marcus",
        hysa_fixed_rate_pct=4.5,
        benchmark_symbol_override="QQQ",
        tax_enabled=True,
        tax_regime="NRA",
        residency_status_change_date=date(2025, 10, 1),
        w8ben_claimed=True,
        w8ben_treaty_rate_pct=15.0,
        marginal_ordinary_rate_pct=32.0,
        qualified_ltcg_rate_pct=20.0,
    )

    save_settings(settings, db_session, test_user_id)

    assert load_settings(db_session, test_user_id) == settings


def test_save_settings_overwrites_rather_than_merges(db_session: Session, test_user_id: uuid.UUID) -> None:
    save_settings(
        DashboardSettings(target_allocation_pct={"VOO": 80.0}, hysa_bank_id="marcus"), db_session, test_user_id
    )

    save_settings(DashboardSettings(target_allocation_pct={"VOO": 80.0}), db_session, test_user_id)

    assert load_settings(db_session, test_user_id).hysa_bank_id is None
