"""Tunable configuration for the accounting module — where its data lives on disk.

Deliberately independent of `trades.config.AppConfig`: accounting reads
from the trades module (net worth includes the investment portfolio's
value) but trades never reads from accounting, so the two configs stay
uncoupled the same way the two packages do.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class AccountingConfig(BaseModel):
    """Where every accounting data file lives, all derived from one root directory."""

    model_config = ConfigDict(frozen=True)

    data_dir: Path = _REPO_ROOT / "data" / "accounting"

    @property
    def raw_statement_dir(self) -> Path:
        """Where every imported CSV is archived verbatim, under `data_dir`."""
        return self.data_dir / "raw_statements"

    @property
    def ledger_csv_path(self) -> Path:
        """Where the derived posting ledger is cached, under `data_dir`."""
        return self.data_dir / "ledger.csv"

    @property
    def store_path(self) -> Path:
        """Where accounts, categories, tags, rules, and other assets are persisted, under `data_dir`."""
        return self.data_dir / "store.json"

    @property
    def overrides_path(self) -> Path:
        """Where manual per-posting categorization overrides are persisted, under `data_dir`."""
        return self.data_dir / "manual_overrides.json"

    @property
    def exchange_rates_raw_dir(self) -> Path:
        """Where every fetched exchange-rate-history response is archived verbatim, under `data_dir`."""
        return self.data_dir / "exchange_rates" / "raw"

    @property
    def exchange_rates_csv_path(self) -> Path:
        """Where the derived daily exchange-rate history is cached, under `data_dir`."""
        return self.data_dir / "exchange_rates" / "rates.csv"
