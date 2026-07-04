"""Tunable configuration for the whole application.

Every configurable value is a field on one of the frozen models below.
`AppConfig` composes all of them into a single object that gets passed to
every function that needs configuration. `IbkrFlexCredentials` is kept
separate because it is a secret read from the environment, not a tunable
with a sensible default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]

LedgerEventType = Literal["DEPOSIT", "WITHDRAWAL", "BUY", "SELL", "DIVIDEND", "WITHHOLDING", "FEE", "SPLIT"]
"""The transaction kinds the ledger knows. `models.LedgerEvent.event_type`
is typed against this.
"""


class LedgerConfig(BaseModel):
    """Ledger-replay tunables."""

    model_config = ConfigDict(frozen=True)

    cash_symbol: str = Field(default="CASH", min_length=1)
    long_term_holding_days: int = Field(default=365, gt=0)


class PriceApiConfig(BaseModel):
    """Where price history is cached and how the Yahoo Finance API is called."""

    model_config = ConfigDict(frozen=True)

    cache_dir: Path = _REPO_ROOT / "data" / "prices"
    chart_url_template: str = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
    request_headers: dict[str, str] = Field(
        default_factory=lambda: {"User-Agent": "Mozilla/5.0 (compatible; trades/0.1)"}
    )
    request_timeout_seconds: float = Field(default=10.0, gt=0)


class CpiConfig(BaseModel):
    """Where the CPI index cache lives and how FRED's public CSV export is called."""

    model_config = ConfigDict(frozen=True)

    cache_dir: Path = _REPO_ROOT / "data" / "cpi"
    series_id: str = Field(default="CPIAUCSL", min_length=1)
    csv_url_template: str = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"  # noqa: RUF027
    request_timeout_seconds: float = Field(default=10.0, gt=0)


class SymbolSearchConfig(BaseModel):
    """How Yahoo Finance's public ticker-search endpoint is called.

    Nothing here is cached: a symbol search is a live, on-demand lookup
    for the frontend's benchmark picker, not data the app replays against.
    """

    model_config = ConfigDict(frozen=True)

    search_url: str = "https://query2.finance.yahoo.com/v1/finance/search"
    request_headers: dict[str, str] = Field(
        default_factory=lambda: {"User-Agent": "Mozilla/5.0 (compatible; trades/0.1)"}
    )
    request_timeout_seconds: float = Field(default=10.0, gt=0)
    max_results: int = Field(default=8, gt=0)


class HysaRatesConfig(BaseModel):
    """Where the HYSA bank-rate-history cache lives and how apyarchives.com is scraped."""

    model_config = ConfigDict(frozen=True)

    cache_dir: Path = _REPO_ROOT / "data" / "hysa_rates"
    source_url: str = "https://www.apyarchives.com"
    request_timeout_seconds: float = Field(default=15.0, gt=0)
    default_bank_id: str = Field(default="ally-bank", min_length=1)


class TimezoneConfig(BaseModel):
    """The timezone timestamps are displayed in.

    Every timestamp is stored as naive UTC (see `brokers/ibkr/models.py`);
    this only controls what zone a human-facing value (e.g. an API
    response's `last_synced_at`) is converted to before display. It has
    no effect on how broker data is parsed or how the ledger is stored.
    """

    model_config = ConfigDict(frozen=True)

    local_zone: str = Field(default="America/New_York", min_length=1)

    @field_validator("local_zone")
    @classmethod
    def _validate_zone_name(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            message = f"{value!r} is not a known IANA timezone name."
            raise ValueError(message) from error
        return value


class ReturnsConfig(BaseModel):
    """The annualization convention and the counterfactual benchmarks to compare against.

    `hysa_annual_rate` stands in for a real HYSA rate time series until
    `market_data.hysa_rates` is implemented — `counterfactuals.hysa_counterfactual_value`
    takes a `rate_lookup` callable specifically so this constant can be
    swapped for a real series later without changing that function.
    """

    model_config = ConfigDict(frozen=True)

    annualization_days: int = Field(default=365, gt=0)
    hysa_annual_rate: float = Field(default=0.04, ge=0)
    benchmark_symbol: str = Field(default="VOO", min_length=1, description="The all-equity counterfactual symbol.")


class IbkrFlexCredentials(BaseSettings):
    """The IBKR Flex Web Service token and query ID, read from `.env` or the environment."""

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    token: SecretStr = Field(validation_alias="IBKR_FLEX_WEB_SERVICE_TOKEN")
    query_id: str = Field(validation_alias="IBKR_QUERY_ID")


class IbkrFlexApiConfig(BaseModel):
    """Where IBKR data is cached locally and how the Flex Web Service is called.

    `server_busy_retry_seconds`/`throttled_retry_seconds` mirror IBKR's own
    documented guidance for error codes 1009/1019 (statement still
    generating) and 1018 (too many requests).
    """

    model_config = ConfigDict(frozen=True)

    cache_dir: Path = _REPO_ROOT / "data" / "brokers" / "ibkr"
    send_request_url: str = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
    fallback_statement_url: str = "https://gdcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"
    request_headers: dict[str, str] = Field(default_factory=lambda: {"User-Agent": "Java"})
    request_timeout_seconds: float = Field(default=15.0, gt=0)
    max_poll_attempts: int = Field(default=10, gt=0)
    server_busy_retry_seconds: float = Field(default=5.0, gt=0)
    throttled_retry_seconds: float = Field(default=10.0, gt=0)
    drip_reinvestment_note_code: str = Field(
        default="R",
        min_length=1,
        description="IBKR's <Trade notes='...'> code for a dividend reinvestment.",
    )

    @property
    def ledger_csv_path(self) -> Path:
        """Where the derived ledger CSV is cached, under `cache_dir`."""
        return self.cache_dir / "ledger.csv"

    @property
    def raw_statement_dir(self) -> Path:
        """Where every raw Flex statement is archived, under `cache_dir`."""
        return self.cache_dir / "raw_statements"


class DashboardConfig(BaseModel):
    """Where dashboard-only, user-editable settings (e.g. a target allocation) are persisted.

    These aren't fetched data (see `docs/architecture.md`'s caching rule)
    and aren't a code-level tunable either — they're settings a user
    changes from the frontend, so `dashboard.py` reads/writes a small JSON
    file here instead of holding them as a hardcoded default.
    """

    model_config = ConfigDict(frozen=True)

    settings_path: Path = _REPO_ROOT / "data" / "dashboard_settings.json"


class AppConfig(BaseModel):
    """Every sub-config for the application, composed into one object."""

    model_config = ConfigDict(frozen=True)

    ledger: LedgerConfig = Field(default_factory=LedgerConfig)
    prices: PriceApiConfig = Field(default_factory=PriceApiConfig)
    cpi: CpiConfig = Field(default_factory=CpiConfig)
    hysa_rates: HysaRatesConfig = Field(default_factory=HysaRatesConfig)
    symbol_search: SymbolSearchConfig = Field(default_factory=SymbolSearchConfig)
    returns: ReturnsConfig = Field(default_factory=ReturnsConfig)
    ibkr: IbkrFlexApiConfig = Field(default_factory=IbkrFlexApiConfig)
    timezone: TimezoneConfig = Field(default_factory=TimezoneConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
