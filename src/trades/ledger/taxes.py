"""U.S. investment-tax accounting: what was realized, how it's characterized, and what to watch for.

Three related but distinct questions live here. First, once a lot is
closed, how much of its gain is taxed at the lower long-term rate versus
the higher short-term rate, and how are dividends split between the
lower-taxed "qualified" bucket and ordinary income — answered per
calendar year, since that's the period gains and losses are netted over,
and split again at any date a person's residency status changed, since a
nonresident alien and a resident alien are taxed under different rules
even within the same year. Second, does a loss risk being disallowed
because a "substantially identical" position was bought back too close to
the sale — a mechanical proximity check, not a tax ruling. Third, what
would happen if an open position were sold right now — the same
questions a closed lot answers, asked hypothetically about one that
hasn't closed yet.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

from trades.utils.frames import collect_if_lazy

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from trades.config import AppConfig, TaxRegime


def _earlier_regime(current_regime: TaxRegime) -> TaxRegime:
    return "NRA" if current_regime == "RESIDENT" else "RESIDENT"


def _regime_as_of(day: date, current_regime: TaxRegime, status_change_date: date | None) -> TaxRegime:
    """Resolve which regime was in effect on a given date.

    A person has at most one residency-status change in this model:
    `current_regime` names whatever applies from `status_change_date`
    onward (or always, if `status_change_date` is None); before that
    date, the other regime applied.

    Returns
    -------
    TaxRegime
        The regime in effect on `day`.
    """
    if status_change_date is None or day >= status_change_date:
        return current_regime
    return _earlier_regime(current_regime)


def _regime_expr(date_column: str, current_regime: TaxRegime, status_change_date: date | None) -> pl.Expr:
    """Build the column expression equivalent of `_regime_as_of`, for tagging a whole frame at once.

    Returns
    -------
    polars.Expr
        An expression evaluating to the regime in effect on each row's `date_column` value.
    """
    if status_change_date is None:
        return pl.lit(current_regime)
    return (
        pl
        .when(pl.col(date_column) < status_change_date)
        .then(pl.lit(_earlier_regime(current_regime)))
        .otherwise(pl.lit(current_regime))
    )


_ANNUAL_REPORT_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "year": pl.Int32,
    "regime": pl.Utf8,
    "long_term_gain_usd": pl.Float64,
    "short_term_gain_usd": pl.Float64,
    "qualified_dividends_usd": pl.Float64,
    "ordinary_dividends_usd": pl.Float64,
    "ordinary_interest_usd": pl.Float64,
    "withholding_tax_usd": pl.Float64,
}


def annual_tax_report(
    closed_lots: pl.DataFrame | pl.LazyFrame,
    ledger: pl.DataFrame | pl.LazyFrame,
    config: AppConfig,
    current_regime: TaxRegime,
    status_change_date: date | None,
) -> pl.DataFrame:
    """Summarize realized gains and dividend income by calendar year and regime.

    Every closed lot and every `DIVIDEND`/`WITHHOLDING` ledger row is
    tagged with the regime in effect on its own date (see
    `_regime_as_of`), so a year a residency-status change fell inside
    produces two rows — one per regime — rather than blending two
    different tax treatments into one number. Within each (year, regime)
    group, gains split by `term` (`LONG` vs `SHORT`, already computed
    when the lot closed) and dividends split by tax character (looked up
    per symbol from `config.tax.tax_character`). Gains and losses are
    summed together, not reported separately, since that net figure — not
    the gross gain or the gross loss alone — is what a given year's tax
    liability is actually based on.

    Parameters
    ----------
    closed_lots
        Closed lots, with `closed_at`, `term`, `realized_gain` columns
        (see `lots.ClosedLot`).
    ledger
        The full ledger, in chronological order.
    config
        Application configuration; `config.tax.tax_character` is read.
    current_regime
        The regime in effect today (and from `status_change_date` onward,
        if given).
    status_change_date
        The date residency status changed, or None if it never has.

    Returns
    -------
    polars.DataFrame
        One row per (year, regime) combination that had any activity,
        columns `year`, `regime`, `long_term_gain_usd`,
        `short_term_gain_usd`, `qualified_dividends_usd`,
        `ordinary_dividends_usd`, `ordinary_interest_usd`,
        `withholding_tax_usd`. Sorted by year then regime.
    """
    lots = collect_if_lazy(closed_lots)
    rows = collect_if_lazy(ledger)

    gains = _gains_by_year_and_regime(lots, current_regime, status_change_date)
    dividends = _dividends_by_year_and_regime(rows, config, current_regime, status_change_date)
    withholding = _withholding_by_year_and_regime(rows, current_regime, status_change_date)

    combined = gains.join(dividends, on=["year", "regime"], how="full", coalesce=True).join(
        withholding, on=["year", "regime"], how="full", coalesce=True
    )
    if combined.is_empty():
        return pl.DataFrame(schema=_ANNUAL_REPORT_SCHEMA)
    money_columns = [name for name in _ANNUAL_REPORT_SCHEMA if name not in {"year", "regime"}]
    return (
        combined
        # `fill_null` unifies every targeted column to one common dtype, so
        # `year` (an int) is cast back explicitly rather than joining it
        # into the same fill as the float money columns.
        .with_columns(pl.col(money_columns).fill_null(0.0), year=pl.col("year").cast(pl.Int32))
        .select(*_ANNUAL_REPORT_SCHEMA.keys())
        .sort("year", "regime")
    )


def _gains_by_year_and_regime(
    closed_lots: pl.DataFrame, current_regime: TaxRegime, status_change_date: date | None
) -> pl.DataFrame:
    schema = {"year": pl.Int32, "regime": pl.Utf8, "long_term_gain_usd": pl.Float64, "short_term_gain_usd": pl.Float64}
    if closed_lots.is_empty():
        return pl.DataFrame(schema=schema)
    return (
        closed_lots
        .with_columns(year=pl.col("closed_at").dt.year(), closed_date=pl.col("closed_at").dt.date())
        .with_columns(regime=_regime_expr("closed_date", current_regime, status_change_date))
        .group_by("year", "regime")
        .agg(
            long_term_gain_usd=pl.col("realized_gain").filter(pl.col("term") == "LONG").sum(),
            short_term_gain_usd=pl.col("realized_gain").filter(pl.col("term") == "SHORT").sum(),
        )
    )


def _dividends_by_year_and_regime(
    ledger: pl.DataFrame, config: AppConfig, current_regime: TaxRegime, status_change_date: date | None
) -> pl.DataFrame:
    schema = {
        "year": pl.Int32,
        "regime": pl.Utf8,
        "qualified_dividends_usd": pl.Float64,
        "ordinary_dividends_usd": pl.Float64,
        "ordinary_interest_usd": pl.Float64,
    }
    dividend_rows = ledger.filter(pl.col("event_type") == "DIVIDEND")
    if dividend_rows.is_empty():
        return pl.DataFrame(schema=schema)
    return (
        dividend_rows
        .with_columns(
            year=pl.col("event_datetime").dt.year(),
            event_date=pl.col("event_datetime").dt.date(),
            character=pl.col("symbol").replace_strict(config.tax.tax_character, default="ordinary_dividend"),
        )
        .with_columns(regime=_regime_expr("event_date", current_regime, status_change_date))
        .group_by("year", "regime")
        .agg(
            qualified_dividends_usd=pl.col("amount").filter(pl.col("character") == "qualified_dividend").sum(),
            ordinary_dividends_usd=pl.col("amount").filter(pl.col("character") == "ordinary_dividend").sum(),
            ordinary_interest_usd=pl.col("amount").filter(pl.col("character") == "ordinary_interest").sum(),
        )
    )


def _withholding_by_year_and_regime(
    ledger: pl.DataFrame, current_regime: TaxRegime, status_change_date: date | None
) -> pl.DataFrame:
    schema = {"year": pl.Int32, "regime": pl.Utf8, "withholding_tax_usd": pl.Float64}
    withholding_rows = ledger.filter(pl.col("event_type") == "WITHHOLDING")
    if withholding_rows.is_empty():
        return pl.DataFrame(schema=schema)
    return (
        withholding_rows
        .with_columns(year=pl.col("event_datetime").dt.year(), event_date=pl.col("event_datetime").dt.date())
        .with_columns(regime=_regime_expr("event_date", current_regime, status_change_date))
        .group_by("year", "regime")
        .agg(withholding_tax_usd=pl.col("amount").sum())
    )


def _related_symbols(symbols: list[str], similar: dict[str, list[str]]) -> pl.DataFrame:
    """Build every (symbol, related_symbol) pair the wash-sale check should treat as one security.

    Every symbol is related to itself; `similar` adds extra pairs and is
    treated as symmetric — declaring `{"VOO": ["IVV"]}` also makes a sale
    of IVV followed by a repurchase of VOO count, without needing the
    reverse entry declared too.

    Returns
    -------
    polars.DataFrame
        Columns `symbol`, `related_symbol` — one row per pair, including each symbol paired with itself.
    """
    related = pl.DataFrame({"symbol": symbols, "related_symbol": symbols})
    similar_with_entries = {symbol: others for symbol, others in similar.items() if others}
    if not similar_with_entries:
        return related
    forward = pl.DataFrame(
        {"symbol": list(similar_with_entries.keys()), "related_symbol": list(similar_with_entries.values())},
        schema={"symbol": pl.Utf8, "related_symbol": pl.List(pl.Utf8)},
    ).explode("related_symbol", empty_as_null=True)
    backward = forward.rename({"symbol": "related_symbol", "related_symbol": "symbol"}).select(
        "symbol", "related_symbol"
    )
    return pl.concat([related, forward, backward]).unique()


def _wash_sale_flagged_lot_ids(candidates: pl.DataFrame, ledger: pl.DataFrame, config: AppConfig) -> list[str]:
    """Find which of a set of candidate loss sales has a nearby repurchase of a related symbol.

    `candidates` needs `lot_id`, `symbol`, `sale_date` columns (`lot_id`
    doubles as the id of the `BUY` that originally opened it, per
    `lots.Lot`, so that opening purchase — not a repurchase — is excluded
    from the search by construction).

    Returns
    -------
    list[str]
        The `lot_id` of every candidate with a matching nearby repurchase.
    """
    if candidates.is_empty():
        return []

    buys = ledger.filter(pl.col("event_type") == "BUY").select(
        "event_id", "symbol", buy_date=pl.col("event_datetime").dt.date()
    )
    related = _related_symbols(candidates["symbol"].unique().to_list(), config.tax.wash_sale_similar_symbols)
    window = timedelta(days=config.tax.wash_sale_window_days)

    matches = (
        candidates
        .join(related, on="symbol", how="inner")
        .join(buys, left_on="related_symbol", right_on="symbol", how="inner")
        .filter(
            (pl.col("buy_date") >= pl.col("sale_date") - window)
            & (pl.col("buy_date") <= pl.col("sale_date") + window)
            & (pl.col("event_id") != pl.col("lot_id"))
        )
    )
    return matches["lot_id"].unique().to_list()


def flag_wash_sales(
    closed_lots: pl.DataFrame | pl.LazyFrame, ledger: pl.DataFrame | pl.LazyFrame, config: AppConfig
) -> pl.DataFrame | pl.LazyFrame:
    """Flag closed lots whose loss might be disallowed by a nearby repurchase.

    Only a lot sold at a loss can trigger this; a lot sold at a gain is
    never flagged, since the wash-sale rule only disallows losses. A flag
    here means a `BUY` of the same symbol, or a symbol on the wash-sale
    similarity list, happened within the configured window before or
    after the sale — it is a mechanical proximity check, not a
    determination that the securities involved are legally "substantially
    identical."

    Parameters
    ----------
    closed_lots
        Closed lots, with `lot_id`, `symbol`, `closed_at`, `realized_gain` columns.
    ledger
        The full ledger, in chronological order.
    config
        Application configuration; `config.tax.wash_sale_window_days` and
        `config.tax.wash_sale_similar_symbols` are read.

    Returns
    -------
    polars.DataFrame or polars.LazyFrame
        `closed_lots` with a `wash_sale_flag` boolean column added. Same
        type as `closed_lots`.
    """
    lots = collect_if_lazy(closed_lots)
    was_eager = isinstance(closed_lots, pl.DataFrame)
    if lots.is_empty():
        empty = lots.with_columns(wash_sale_flag=pl.lit(value=False, dtype=pl.Boolean))
        return empty if was_eager else empty.lazy()

    rows = collect_if_lazy(ledger)
    candidates = lots.filter(pl.col("realized_gain") < 0).select(
        "lot_id", "symbol", sale_date=pl.col("closed_at").dt.date()
    )
    flagged = _wash_sale_flagged_lot_ids(candidates, rows, config)

    result = lots.with_columns(wash_sale_flag=pl.col("lot_id").is_in(flagged))
    return result if was_eager else result.lazy()


_PREVIEW_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "lot_id": pl.Utf8,
    "symbol": pl.Utf8,
    "shares": pl.Float64,
    "days_held": pl.Int64,
    "term": pl.Utf8,
    "unrealized_gain_usd": pl.Float64,
    "would_wash_sale": pl.Boolean,
}


def preview_sale(
    open_lots: pl.DataFrame | pl.LazyFrame,
    ledger: pl.DataFrame | pl.LazyFrame,
    price_lookup: Callable[[str, date], float | None],
    as_of: date,
    config: AppConfig,
) -> pl.DataFrame:
    """Preview what selling every open lot today, without actually selling it, would look like.

    Answers the same three questions a real sale answers — how long was
    it held, what term would that give it, what gain or loss would it
    realize — plus whether selling it today would trip the wash-sale
    check in `flag_wash_sales`, using the exact same proximity rule. None
    of this changes any stored state; it only reads `open_lots` and
    reports what a sale dated `as_of` would compute to.

    Parameters
    ----------
    open_lots
        Open lots, with `lot_id`, `symbol`, `opened_at`, `shares`, `cost_per_share` columns.
    ledger
        The full ledger, in chronological order.
    price_lookup
        Looks up a symbol's price as of a given date; returns None if unavailable.
    as_of
        The hypothetical sale date.
    config
        Application configuration; `config.ledger.long_term_holding_days`
        and the wash-sale settings are read.

    Returns
    -------
    polars.DataFrame
        Columns `lot_id`, `symbol`, `shares`, `days_held`, `term`,
        `unrealized_gain_usd`, `would_wash_sale`.

    Raises
    ------
    ValueError
        If `price_lookup` returns None for any symbol still held.
    """
    lots = collect_if_lazy(open_lots)
    if lots.is_empty():
        return pl.DataFrame(schema=_PREVIEW_SCHEMA)

    prices: dict[str, float] = {}
    for symbol in lots["symbol"].unique().to_list():
        price = price_lookup(symbol, as_of)
        if price is None:
            message = f"No price available for {symbol} on or before {as_of}."
            raise ValueError(message)
        prices[symbol] = price

    with_preview = lots.with_columns(
        days_held=(pl.lit(as_of) - pl.col("opened_at").dt.date()).dt.total_days(),
        current_price=pl.col("symbol").replace_strict(prices, return_dtype=pl.Float64),
    ).with_columns(
        term=pl
        .when(pl.col("days_held") >= config.ledger.long_term_holding_days)
        .then(pl.lit("LONG"))
        .otherwise(pl.lit("SHORT")),
        unrealized_gain_usd=(pl.col("current_price") - pl.col("cost_per_share")) * pl.col("shares"),
    )

    rows = collect_if_lazy(ledger)
    candidates = with_preview.filter(pl.col("unrealized_gain_usd") < 0).select(
        "lot_id", "symbol", sale_date=pl.lit(as_of)
    )
    flagged = _wash_sale_flagged_lot_ids(candidates, rows, config)

    return with_preview.with_columns(would_wash_sale=pl.col("lot_id").is_in(flagged)).select(*_PREVIEW_SCHEMA.keys())


def after_tax_rate_lookup(
    rate_lookup: Callable[[date], float],
    marginal_ordinary_rate: float,
    current_regime: TaxRegime,
    status_change_date: date | None,
) -> Callable[[date], float]:
    """Wrap an annual-rate lookup so it reflects what actually lands in your pocket after tax.

    Bank interest paid to a nonresident alien is generally exempt from
    U.S. tax, so the rate is passed through unchanged for any date the
    nonresident-alien regime applies. Once resident-alien status applies,
    interest is ordinary income taxed at the marginal rate, so the rate is
    cut accordingly: `after_tax_rate = rate * (1 - marginal_ordinary_rate)`.

    Parameters
    ----------
    rate_lookup
        The pre-tax annual rate as of a given date (e.g. a HYSA rate lookup).
    marginal_ordinary_rate
        The tax rate applied to ordinary income once resident-alien status applies.
    current_regime
        The regime in effect today (and from `status_change_date` onward, if given).
    status_change_date
        The date residency status changed, or None if it never has.

    Returns
    -------
    Callable[[datetime.date], float]
        A rate lookup with the same signature as `rate_lookup`, reduced by
        `marginal_ordinary_rate` on any date resident-alien status applies.
    """

    def wrapped(day: date) -> float:
        rate = rate_lookup(day)
        if _regime_as_of(day, current_regime, status_change_date) == "RESIDENT":
            return rate * (1 - marginal_ordinary_rate)
        return rate

    return wrapped
