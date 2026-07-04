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


class AggregationConfig(BaseModel):
    """Controls how same-day, same-symbol fills are merged."""

    model_config = ConfigDict(frozen=True)

    same_day_price_tolerance: float = Field(
        default=0.0001,
        gt=0,
        lt=1,
        description="Max relative $/share difference for two same-day fills to be merged.",
    )


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
    """The return/annualization math and the cash benchmark to compare against."""

    model_config = ConfigDict(frozen=True)

    annualization_days: int = Field(default=365, gt=0)
    hysa_annual_rate: float = Field(default=0.04, ge=0)
    trend_fit_kind: Literal["linear", "mean"] = "linear"


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


class AppConfig(BaseModel):
    """Every sub-config for the application, composed into one object."""

    model_config = ConfigDict(frozen=True)

    ledger: LedgerConfig = Field(default_factory=LedgerConfig)
    aggregation: AggregationConfig = Field(default_factory=AggregationConfig)
    prices: PriceApiConfig = Field(default_factory=PriceApiConfig)
    cpi: CpiConfig = Field(default_factory=CpiConfig)
    returns: ReturnsConfig = Field(default_factory=ReturnsConfig)
    ibkr: IbkrFlexApiConfig = Field(default_factory=IbkrFlexApiConfig)
    timezone: TimezoneConfig = Field(default_factory=TimezoneConfig)
