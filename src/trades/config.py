"""Every tunable value in this package lives here, as a field on one of
these frozen config objects — never as a bare module-level constant that a
function silently falls back to. Callers construct the config they need
(defaults below are the package's suggested values, not hidden ones) and
pass it explicitly; nothing is picked up implicitly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class TradeSchema(BaseModel):
    """The canonical trade-row column names every broker's data must be
    mapped onto (by a `standardize_*` function in `preprocessing.py`) before
    it reaches `transactions.py`. Declared once here — as data, not string
    literals repeated in every preprocessor — so a rename is a one-line
    change instead of a hunt across every broker's preprocessing code.

    Field *values* are the column names themselves. `models.RawTrade` is the
    validator for a row in this exact shape; its field names must match
    these values (`tests/test_config.py` asserts this doesn't drift).
    """

    model_config = ConfigDict(frozen=True)

    trade_date: str = "trade_date"
    symbol: str = "symbol"
    shares: str = "shares"
    usd_spent: str = "usd_spent"


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


class ReturnsConfig(BaseModel):
    """The return/annualization math and the cash benchmark to compare against."""

    model_config = ConfigDict(frozen=True)

    annualization_days: int = Field(default=365, gt=0)
    hysa_annual_rate: float = Field(default=0.04, ge=0)
    trend_fit_kind: Literal["linear", "mean"] = "linear"


class IbkrFlexCredentials(BaseSettings):
    """The IBKR Flex Web Service token and query ID, read from `.env` (or the
    real environment) — never hardcoded, and required: constructing this
    with either variable unset raises immediately instead of silently
    proceeding with an empty credential.
    """

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
    generating) and 1018 (too many requests) — see docs/ibkr_flex_api.md.
    """

    model_config = ConfigDict(frozen=True)

    cache_dir: Path = _REPO_ROOT / "data" / "brokers" / "ibkr"
    send_request_url: str = (
        "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
    )
    fallback_statement_url: str = (
        "https://gdcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"
    )
    request_headers: dict[str, str] = Field(default_factory=lambda: {"User-Agent": "Java"})
    request_timeout_seconds: float = Field(default=15.0, gt=0)
    max_poll_attempts: int = Field(default=10, gt=0)
    server_busy_retry_seconds: float = Field(default=5.0, gt=0)
    throttled_retry_seconds: float = Field(default=10.0, gt=0)
