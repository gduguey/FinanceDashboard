"""Tunable configuration for the whole application.

Every configurable value is a field on one of the frozen models below.
`AppConfig` composes all of them into a single object that gets passed to
every function that needs configuration. `IbkrFlexCredentials` is kept
separate because it's a per-user secret resolved from Postgres (see
`trades.brokers.ibkr.credentials`, over the broker-agnostic storage in
`trades.broker_credentials`), never a tunable with a sensible default and
never read from `.env`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

_REPO_ROOT = Path(__file__).resolve().parents[2]

LedgerEventType = Literal["DEPOSIT", "WITHDRAWAL", "BUY", "SELL", "DIVIDEND", "WITHHOLDING", "FEE", "SPLIT"]
"""The transaction kinds the ledger knows. `models.LedgerEvent.event_type`
is typed against this.
"""

TaxRegime = Literal["NRA", "RESIDENT"]
"""Which U.S. tax treatment applies to investment income: a nonresident
alien (NRA — someone present on a visa such as F-1 who hasn't met the
substantial-presence test) generally owes no U.S. tax on bank interest and
no U.S. capital-gains tax on securities at all; a resident alien (e.g. an
H-1B holder who has met that test) is taxed the same way a U.S. citizen
is, on both.
"""

TaxCharacter = Literal["qualified_dividend", "ordinary_dividend", "ordinary_interest"]
"""How a distribution is taxed: a qualified dividend gets the lower
long-term-capital-gains rate; an ordinary dividend or interest payment is
taxed at the regular income-tax rate. A symbol can be pinned to one of
these explicitly in `TaxConfig.tax_character`; otherwise it's derived from
the paying broker's own dividend label plus how long the position was
actually held around the payment date (see `ledger.taxes`).
"""


class TaxConfig(BaseModel):
    """Static tax facts and rate assumptions this dashboard needs but has no way to derive on its own.

    `tax_character` pins a symbol's distributions to a fixed classification,
    overriding the broker-label-plus-holding-period derivation in
    `ledger.taxes` entirely — useful for a fund whose distributions are
    known never to qualify regardless of how long it's held (e.g. most bond
    funds). `qualified_dividend_window_days` is the length of the window,
    centered on a dividend's ex-date, that the holding-period check looks
    within; `qualified_dividend_min_days_held` is how many of those days
    must actually be held for the dividend to qualify. `wash_sale_similar_symbols`
    lists, for a given symbol, other symbols a sale-and-repurchase pair
    between them might count as "substantially identical" for the wash-sale
    check — declaring it in one direction is enough, the reverse mapping is
    inferred automatically. `nra_statutory_dividend_withholding_rate` is the
    default U.S. withholding rate on a nonresident alien's dividends when no
    tax treaty lowers it — a resident of a treaty country (claimed on IRS
    Form W-8BEN) pays that treaty's negotiated rate instead.
    """

    model_config = ConfigDict(frozen=True)

    marginal_ordinary_rate: float = Field(
        default=0.24, ge=0, le=1, description="Top marginal rate applied to ordinary income, for after-tax estimates."
    )
    qualified_ltcg_rate: float = Field(
        default=0.15,
        ge=0,
        le=1,
        description="Rate applied to long-term capital gains and qualified dividends, for after-tax estimates.",
    )
    tax_character: dict[str, TaxCharacter] = Field(default_factory=dict)
    qualified_dividend_window_days: int = Field(
        default=60, gt=0, description="Half-width of the ex-date-centered window the holding-period test looks within."
    )
    qualified_dividend_min_days_held: int = Field(
        default=60, gt=0, description="Days that must actually be held within that window for a dividend to qualify."
    )
    nra_statutory_dividend_withholding_rate: float = Field(
        default=0.30,
        ge=0,
        le=1,
        description="Default U.S. withholding rate on a nonresident alien's dividends absent a tax treaty.",
    )
    wash_sale_window_days: int = Field(
        default=30, gt=0, description="How many days before or after a loss sale a repurchase can still taint it."
    )
    wash_sale_similar_symbols: dict[str, list[str]] = Field(default_factory=dict)


class LedgerConfig(BaseModel):
    """Ledger-replay tunables."""

    model_config = ConfigDict(frozen=True)

    cash_symbol: str = Field(default="CASH", min_length=1)
    long_term_holding_days: int = Field(default=365, gt=0)


class PriceApiConfig(BaseModel):
    """Where price history is cached and how the Yahoo Finance API is called."""

    model_config = ConfigDict(frozen=True)

    cache_dir: Path = _REPO_ROOT / "data" / "trades" / "prices"
    chart_url_template: str = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
    request_headers: dict[str, str] = Field(
        default_factory=lambda: {"User-Agent": "Mozilla/5.0 (compatible; trades/0.1)"}
    )
    request_timeout_seconds: float = Field(default=10.0, gt=0)


class CpiConfig(BaseModel):
    """Where the CPI index cache lives and how FRED's public CSV export is called."""

    model_config = ConfigDict(frozen=True)

    cache_dir: Path = _REPO_ROOT / "data" / "trades" / "cpi"
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

    cache_dir: Path = _REPO_ROOT / "data" / "trades" / "hysa_rates"
    source_url: str = "https://www.apyarchives.com"
    request_headers: dict[str, str] = Field(
        default_factory=lambda: {"User-Agent": "Mozilla/5.0 (compatible; trades/0.1)"}
    )
    request_timeout_seconds: float = Field(default=15.0, gt=0)
    default_bank_id: str = Field(default="ally-bank", min_length=1)


def validate_iana_zone_name(value: str) -> str:
    """Reject a zone name that isn't a real IANA timezone.

    Shared by `TimezoneConfig.local_zone` (the code-level default) and
    `trades.api.api_models.TimezoneSettingUpdate.local_zone` (the
    browser-reported per-user override) — both need the exact same check,
    since either one ends up passed straight to `zoneinfo.ZoneInfo` in
    `trades.api.dependencies._to_display_zone`.

    Returns
    -------
    str

    Raises
    ------
    ValueError
        If `value` isn't a known IANA timezone name.
    """
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        message = f"{value!r} is not a known IANA timezone name."
        raise ValueError(message) from error
    return value


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
        """Reject a `local_zone` that isn't a real IANA timezone name.

        Returns
        -------
        str
        """
        return validate_iana_zone_name(value)


class ReturnsConfig(BaseModel):
    """The annualization convention and the counterfactual benchmarks to compare against.

    `hysa_annual_rate` is the floor under the real rate series, not a
    stand-in for one: `market_data.hysa_rates` scrapes and caches every
    tracked bank's published APY history, and
    `dashboard.settings.raw_hysa_rate_lookup` reads it for the bank the
    user selected, falling back to this constant only for days that bank
    published nothing (typically before its history starts). A user who
    wants a different number sets `hysa_bank_id` or `hysa_fixed_rate_pct`
    on their own dashboard settings rather than changing this.
    """

    model_config = ConfigDict(frozen=True)

    annualization_days: int = Field(
        default=365,
        gt=0,
        description="Minimum holding period before a raw return gets projected to a yearly rate.",
    )
    days_per_year: int = Field(
        default=365,
        gt=0,
        description="Day-count basis for converting an annual rate to a daily one (XIRR's exponent, HYSA compounding).",
    )
    hysa_annual_rate: float = Field(default=0.04, ge=0)
    benchmark_symbol: str = Field(default="VOO", min_length=1, description="The all-equity counterfactual symbol.")
    xirr_tolerance: float = Field(
        default=1e-6, gt=0, description="How close to zero XIRR's net-present-value search must land to accept a rate."
    )
    xirr_max_newton_iterations: int = Field(
        default=100, gt=0, description="Newton-Raphson attempts before XIRR falls back to bisection."
    )
    xirr_max_bisection_iterations: int = Field(
        default=200, gt=0, description="Bisection attempts before XIRR gives up and raises."
    )


class IbkrFlexCredentials(BaseModel):
    """The IBKR Flex Web Service token and query ID for one user, resolved from Postgres.

    Never `.env`-backed — see
    `trades.brokers.ibkr.credentials.resolve_ibkr_credentials`, the only
    place this gets constructed. Both fields are required because
    a partial credential (a query id with no token, or vice versa) can't
    call the Flex Web Service at all; `resolve_ibkr_credentials` is what
    turns "nothing saved yet" into a clear error before this model is ever built.
    """

    model_config = ConfigDict(frozen=True)

    token: SecretStr
    query_id: str = Field(min_length=1)


class IbkrFlexApiConfig(BaseModel):
    """Where IBKR data is cached locally and how the Flex Web Service is called.

    `server_busy_retry_seconds`/`throttled_retry_seconds` mirror IBKR's own
    documented guidance for error codes 1009/1019 (statement still
    generating) and 1018 (too many requests).
    """

    model_config = ConfigDict(frozen=True)

    cache_dir: Path = _REPO_ROOT / "data" / "trades" / "brokers" / "ibkr"
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
    def raw_statement_dir(self) -> Path:
        """Where every raw Flex statement is archived, under `cache_dir`."""
        return self.cache_dir / "raw_statements"


class CashSittingConfig(BaseModel):
    """Tunables for the uninvested-cash warning shown on the card.

    See `dashboard.cash_sitting.open_cash_lots` — every dollar is tracked
    as its own FIFO lot from the day it arrives, so there's no threshold
    to tune for "was this decrease big enough to count as deployment";
    any decrease consumes the oldest lot(s) exactly. Only how long the
    oldest lot has to sit before it's worth flagging is a tunable.
    """

    model_config = ConfigDict(frozen=True)

    light_warning_days: int = Field(default=7, gt=0, description="Days sitting before the light warning shows.")
    heavy_warning_days: int = Field(default=14, gt=0, description="Days sitting before the heavy warning shows.")


class AppConfig(BaseModel):
    """Every sub-config for the application, composed into one object."""

    model_config = ConfigDict(frozen=True)

    ledger: LedgerConfig = Field(default_factory=LedgerConfig)
    prices: PriceApiConfig = Field(default_factory=PriceApiConfig)
    cpi: CpiConfig = Field(default_factory=CpiConfig)
    hysa_rates: HysaRatesConfig = Field(default_factory=HysaRatesConfig)
    symbol_search: SymbolSearchConfig = Field(default_factory=SymbolSearchConfig)
    returns: ReturnsConfig = Field(default_factory=ReturnsConfig)
    tax: TaxConfig = Field(default_factory=TaxConfig)
    ibkr: IbkrFlexApiConfig = Field(default_factory=IbkrFlexApiConfig)
    timezone: TimezoneConfig = Field(default_factory=TimezoneConfig)
    cash_sitting: CashSittingConfig = Field(default_factory=CashSittingConfig)
