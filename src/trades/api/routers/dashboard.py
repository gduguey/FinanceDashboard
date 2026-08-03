"""Portfolio-performance read endpoints — mirrors `trades.dashboard`: overview, charts, lots, risk, tax report."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.current_user import get_current_user_id
from db.session import get_db
from http_api.pagination import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX
from trades import dashboard
from trades.api.api_models import (
    AllocationRow,
    AnnualTaxRow,
    CashHistoryPoint,
    CashSitting,
    ClosedLotPage,
    ClosedLotRow,
    DataQualityRow,
    DollarChart,
    DollarChartPoint,
    GrowthOf100Point,
    LedgerEventPage,
    MonthlyPnlBySymbolRow,
    MonthlyPnlRow,
    OpenLotPage,
    OpenLotRow,
    Overview,
    ReallocationMarker,
    RiskStat,
    SalePreviewRow,
    SymbolRollupPage,
    SymbolRollupRow,
    TaxOwedRow,
    TaxReport,
    WashSaleRow,
)
from trades.api.dependencies import _config, _first_event_date, _last_synced_iso, _load_ledger
from trades.api.entities import LedgerEvent
from trades.brokers.ibkr import main

if TYPE_CHECKING:
    import polars as pl

router = APIRouter()


def _chart_range(ledger: pl.DataFrame, start: date | None, end: date | None) -> tuple[date, date]:
    """Default a chart's range to the ledger's full history.

    Deliberately never defaults to "just today": a single-day snapshot
    invites watching daily noise, whereas since-inception or a long window
    shows the trend that actually matters.

    Returns
    -------
    tuple[datetime.date, datetime.date]
        `(start, end)`, each defaulted if not given.
    """
    return start or _first_event_date(ledger), end or datetime.now(tz=UTC).date()


@router.get("/overview")
def get_overview(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    as_of: date | None = None,
) -> Overview:
    """Return the overview card row: value, gain split, XIRR, excess value over a HYSA, TWR.

    Returns
    -------
    Overview
        `OverviewCards` fields, plus `last_synced_at`.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger(session, user_id)
    config = _config()
    settings = dashboard.load_settings(session, user_id)
    try:
        cards = dashboard.overview_cards(ledger, config, settings, as_of or datetime.now(tz=UTC).date())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    local_zone = dashboard.resolved_local_zone(config, settings)
    return Overview(**asdict(cards), last_synced_at=_last_synced_iso(user_id, local_zone))


@router.get("/chart/dollar")
def get_dollar_chart(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    start: date | None = None,
    end: date | None = None,
) -> DollarChart:
    """Return the three/four-line dollar chart plus reallocation markers.

    Returns
    -------
    DollarChart
        `series` (one entry per day) and `reallocation_markers`.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger(session, user_id)
    settings = dashboard.load_settings(session, user_id)
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        series = dashboard.dollar_chart_series(ledger, _config(), settings, range_start, range_end)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    markers = dashboard.reallocation_markers(ledger)
    return DollarChart(
        series=[DollarChartPoint(**row) for row in series.to_dicts()],
        reallocation_markers=[ReallocationMarker(**row) for row in markers.to_dicts()],
    )


@router.get("/chart/growth-of-100")
def get_growth_of_100_chart(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    start: date | None = None,
    end: date | None = None,
) -> list[GrowthOf100Point]:
    """Return the growth-of-$100 chart: NAV plus every benchmark, indexed to 100.

    Returns
    -------
    list[GrowthOf100Point]
        One entry per day.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger(session, user_id)
    settings = dashboard.load_settings(session, user_id)
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        series = dashboard.growth_of_100_chart(ledger, _config(), settings, range_start, range_end)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return [GrowthOf100Point(**row) for row in series.to_dicts()]


@router.get("/chart/cash-history")
def get_cash_history(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    start: date | None = None,
    end: date | None = None,
) -> list[CashHistoryPoint]:
    """Return the uninvested cash balance for every day in range, plus what it would be worth invested immediately.

    Returns
    -------
    list[CashHistoryPoint]
        One entry per day.
        `*_live_usd` is what currently-sitting cash would be worth had it
        been invested in the benchmark/HYSA since it arrived — bounded,
        tracks `cash`'s own shape. `*_realized_usd` is a running total,
        banked once per past sitting episode at the moment it ended, of
        the gain that episode's cash missed out on — frozen from then on
        (see `dashboard.cash_received_counterfactual`).

    Raises
    ------
    HTTPException
        Via `_load_ledger`, if no ledger is cached yet (404).
    """
    ledger = _load_ledger(session, user_id)
    config = _config()
    settings = dashboard.load_settings(session, user_id)
    range_start, range_end = _chart_range(ledger, start, end)
    daily_cash = cast("pl.DataFrame", dashboard.daily_cash_balances(ledger, config, range_start, range_end))
    adjusted_lookup = dashboard.make_price_lookup(config, adjusted=True)
    benchmark_symbol = dashboard.resolved_benchmark_symbol(config, settings)
    try:
        counterfactual = dashboard.cash_received_counterfactual(
            daily_cash,
            benchmark_price_lookup=lambda day: adjusted_lookup(benchmark_symbol, day),
            hysa_rate_lookup=dashboard.hysa_rate_lookup(config, settings),
            days_per_year=config.returns.days_per_year,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    combined = daily_cash.join(counterfactual, on="date", how="left")
    return [CashHistoryPoint(**row) for row in combined.to_dicts()]


@router.get("/cash-sitting")
def get_cash_sitting(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CashSitting:
    """Report how long the current uninvested cash balance has been sitting idle, and what it's missed out on.

    Returns
    -------
    CashSitting
        See `dashboard.cash_sitting.CashSittingSummary`.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger(session, user_id)
    config = _config()
    settings = dashboard.load_settings(session, user_id)
    today = datetime.now(tz=UTC).date()
    range_start = _first_event_date(ledger)
    try:
        daily_cash = cast("pl.DataFrame", dashboard.daily_cash_balances(ledger, config, range_start, today))
        growth_index = dashboard.growth_of_100_chart(ledger, config, settings, range_start, today)
        summary = dashboard.cash_sitting_summary(daily_cash, growth_index, today, config)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return CashSitting(**asdict(summary))


@router.get("/chart/monthly-pnl")
def get_monthly_pnl(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    start: date | None = None,
    end: date | None = None,
) -> list[MonthlyPnlRow]:
    """Return each month's value change split into contributions and market gain.

    Returns
    -------
    list[MonthlyPnlRow]
        One entry per calendar month.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger(session, user_id)
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        rows = dashboard.monthly_pnl(ledger, _config(), range_start, range_end)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return [MonthlyPnlRow(**row) for row in rows.to_dicts()]


@router.get("/chart/monthly-pnl/by-symbol")
def get_monthly_pnl_by_symbol(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    start: date | None = None,
    end: date | None = None,
) -> list[MonthlyPnlBySymbolRow]:
    """Return each month's value change split into contributions and market gain, per symbol.

    Returns
    -------
    list[MonthlyPnlBySymbolRow]
        One entry per (month, symbol) pair.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger(session, user_id)
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        rows = dashboard.monthly_pnl_by_symbol(ledger, _config(), range_start, range_end)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return [MonthlyPnlBySymbolRow(**row) for row in rows.to_dicts()]


@router.get("/allocation")
def get_allocation(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    as_of: date | None = None,
) -> list[AllocationRow]:
    """Return the current-value allocation by symbol (including cash), against the target.

    Returns
    -------
    list[AllocationRow]
        One entry per symbol (plus cash).

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger(session, user_id)
    settings = dashboard.load_settings(session, user_id)
    try:
        rows = dashboard.allocation_view(ledger, _config(), settings, as_of or datetime.now(tz=UTC).date())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return [AllocationRow(**row) for row in rows.to_dicts()]


@router.get("/tax/report")
def get_tax_report(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    as_of: date | None = None,
) -> TaxReport:
    """Return the full tax view: the annual report, estimated tax owed, flagged wash sales, and sale previews.

    Returns
    -------
    TaxReport
        `annual`, `tax_owed` (the annual report plus estimated
        `capital_gains_tax_usd`, `dividend_tax_usd`, `total_tax_usd`,
        `balance_due_usd` per year), `wash_sales`, `sale_previews`,
        `after_tax_excess_value_vs_hysa_usd`, and the liquidation estimate —
        `liquidation_pretax_value_usd`, `liquidation_long_term_gain_usd`,
        `liquidation_short_term_gain_usd`, `liquidation_capital_gains_tax_usd`,
        `liquidation_value_usd` — what a full sale of every open lot right
        now would leave you with, and the arithmetic behind that number.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger(session, user_id)
    settings = dashboard.load_settings(session, user_id)
    try:
        summary = dashboard.tax_summary(ledger, _config(), settings, as_of or datetime.now(tz=UTC).date())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return TaxReport(
        annual=[AnnualTaxRow(**row) for row in summary.annual.to_dicts()],
        tax_owed=[TaxOwedRow(**row) for row in summary.tax_owed.to_dicts()],
        wash_sales=[WashSaleRow(**row) for row in summary.wash_sales.to_dicts()],
        sale_previews=[SalePreviewRow(**row) for row in summary.sale_previews.to_dicts()],
        after_tax_excess_value_vs_hysa_usd=summary.after_tax_excess_value_vs_hysa_usd,
        liquidation_pretax_value_usd=summary.liquidation_pretax_value_usd,
        liquidation_long_term_gain_usd=summary.liquidation_long_term_gain_usd,
        liquidation_short_term_gain_usd=summary.liquidation_short_term_gain_usd,
        liquidation_capital_gains_tax_usd=summary.liquidation_capital_gains_tax_usd,
        liquidation_value_usd=summary.liquidation_value_usd,
    )


_LOT_LIMIT = Annotated[int, Query(ge=1, description="How many lots to return.")]
_SYMBOL_LIMIT = Annotated[int, Query(ge=1, description="How many symbols to return.")]
_LOT_OFFSET = Annotated[int, Query(ge=0, description="How many lots to skip.")]
_SYMBOL_OFFSET = Annotated[int, Query(ge=0, description="How many symbols to skip.")]


def _lots_table(session: Session, user_id: uuid.UUID, as_of: date | None) -> dashboard.LotsTable:
    """FIFO-match the whole ledger, for one of the three lot collections to be paged out of.

    Each of the three routes below calls this, so a client walking all three
    pays for one match per request rather than one for the screen. That is
    affordable and was not: the match is `ledger.replay.replay_ledger`,
    which cost 3.7 s over a 10,000-event ledger until item C4b made it
    linear, and being unaffordable is exactly why item C4a was scoped into
    PR D and then declined there — paging a read that recomputes everything
    per page multiplies the work by the page count. At 39 ms it does not.

    Returns
    -------
    LotsTable
        All three collections, as polars frames.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger(session, user_id)
    settings = dashboard.load_settings(session, user_id)
    try:
        return dashboard.lots_table(ledger, _config(), settings, as_of or datetime.now(tz=UTC).date())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _window(rows: pl.DataFrame, order_by: list[str], limit: int, offset: int) -> list[dict[str, Any]]:
    """One page's worth of rows, in a total order paging can rely on.

    Sorted rather than left in replay order, which is deterministic but
    undocumented: two requests for two different windows of one collection
    have to agree on what row 200 is, or a page walk both skips and repeats
    rows. The empty frame is returned as-is because `symbol_rollup` carries
    no columns when there are no symbols, and sorting it by name would
    raise.

    Returns
    -------
    list[dict[str, Any]]
    """
    if rows.is_empty():
        return []
    return rows.sort(order_by).slice(offset, limit).to_dicts()


@router.get("/lots/open")
def get_open_lots(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    as_of: date | None = None,
    limit: _LOT_LIMIT = PAGE_LIMIT_DEFAULT,
    offset: _LOT_OFFSET = 0,
) -> OpenLotPage:
    """Return one page of the open lots, oldest-opened first, with each lot's return since it opened.

    Parameters
    ----------
    as_of
        The date to price every open lot as of. Defaults to today.
    limit
        How many lots to return. Clamped to `PAGE_LIMIT_MAX`.
    offset
        How many lots to skip.

    Returns
    -------
    OpenLotPage
        The page's lots, plus the total a client needs to ask for the next one.

    The 404 for an uncached ledger and the 422 for a missing price both come
    out of `_lots_table`.
    """
    limit = min(limit, PAGE_LIMIT_MAX)
    rows = _lots_table(session, user_id, as_of).open_lots
    return OpenLotPage(
        items=[OpenLotRow(**row) for row in _window(rows, ["opened_at", "symbol", "lot_id"], limit, offset)],
        window_unit="lot",
        total=rows.height,
        limit=limit,
        offset=offset,
    )


@router.get("/lots/closed")
def get_closed_lots(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    as_of: date | None = None,
    limit: _LOT_LIMIT = PAGE_LIMIT_DEFAULT,
    offset: _LOT_OFFSET = 0,
) -> ClosedLotPage:
    """Return one page of the closed lots, oldest-closed first, with each lot's excess return over a HYSA.

    Parameters
    ----------
    as_of
        Unused by the figures on a closed lot, whose window is finished;
        accepted so all three lot routes take the same query.
    limit
        How many lots to return. Clamped to `PAGE_LIMIT_MAX`.
    offset
        How many lots to skip.

    Returns
    -------
    ClosedLotPage
        The page's lots, plus the total a client needs to ask for the next one.

    The 404 for an uncached ledger and the 422 for a missing price both come
    out of `_lots_table`.
    """
    limit = min(limit, PAGE_LIMIT_MAX)
    rows = _lots_table(session, user_id, as_of).closed_lots
    order = ["closed_at", "symbol", "lot_id", "closed_by_event_id"]
    return ClosedLotPage(
        items=[ClosedLotRow(**row) for row in _window(rows, order, limit, offset)],
        window_unit="lot",
        total=rows.height,
        limit=limit,
        offset=offset,
    )


@router.get("/lots/symbols")
def get_symbol_rollup(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    as_of: date | None = None,
    limit: _SYMBOL_LIMIT = PAGE_LIMIT_DEFAULT,
    offset: _SYMBOL_OFFSET = 0,
) -> SymbolRollupPage:
    """Return one page of the per-symbol rollup, by symbol: lifecycle stats and a money-weighted return.

    Parameters
    ----------
    as_of
        The date to value each symbol's remaining holding as of. Defaults to today.
    limit
        How many symbols to return. Clamped to `PAGE_LIMIT_MAX`.
    offset
        How many symbols to skip.

    Returns
    -------
    SymbolRollupPage
        The page's symbols, plus the total a client needs to ask for the next one.

    The 404 for an uncached ledger and the 422 for a missing price both come
    out of `_lots_table`.
    """
    limit = min(limit, PAGE_LIMIT_MAX)
    rows = _lots_table(session, user_id, as_of).symbol_rollup
    return SymbolRollupPage(
        items=[SymbolRollupRow(**row) for row in _window(rows, ["symbol"], limit, offset)],
        window_unit="symbol",
        total=rows.height,
        limit=limit,
        offset=offset,
    )


@router.get("/risk")
def get_risk(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    start: date | None = None,
    end: date | None = None,
) -> RiskStat:
    """Return the largest peak-to-trough NAV decline over a window.

    Returns
    -------
    RiskStat
        `max_drawdown_pct`.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger(session, user_id)
    range_start, range_end = _chart_range(ledger, start, end)
    try:
        return RiskStat(max_drawdown_pct=dashboard.risk_stat(ledger, _config(), range_start, range_end))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/data-quality")
def get_data_quality(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[DataQualityRow]:
    """Return the last cached price date per symbol ever held or benchmarked against.

    Returns
    -------
    list[DataQualityRow]
        One entry per symbol.
    """
    config = _config()
    settings = dashboard.load_settings(session, user_id)
    ledger = _load_ledger(session, user_id)
    held_and_benchmark = {*ledger["symbol"].unique().to_list(), dashboard.resolved_benchmark_symbol(config, settings)}
    symbols = sorted(held_and_benchmark - {config.ledger.cash_symbol})
    rows = dashboard.data_quality(symbols, config)
    return [DataQualityRow(**row) for row in rows.to_dicts()]


@router.get("/ledger/export")
def get_ledger_export(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    limit: Annotated[int, Query(ge=1, description="How many events to return, oldest first.")] = PAGE_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0, description="How many events to skip.")] = 0,
) -> LedgerEventPage:
    """Export one page of the event ledger, for the user's own backup.

    An export's caller wants the whole ledger by definition, so this is
    bounded rather than filtered: a page is capped at `PAGE_LIMIT_MAX` and
    the client walks `offset` until it has `total` events. That is
    deliberately not the same as returning a truncated file — a partial
    backup presented as a complete one is worse than several requests. This
    endpoint used to return every event in one unbounded response, which is
    the same shape its accounting counterpart was given a page for (C3).

    `limit` counts events, which for this ledger is also what `items` counts:
    unlike `GET /postings`, nothing here groups rows that have to stay
    together on one page.

    Parameters
    ----------
    limit
        How many events to return, oldest first. Clamped to `PAGE_LIMIT_MAX`.
    offset
        How many events to skip.

    Returns
    -------
    LedgerEventPage
        The page's events, plus the total a client needs in order to ask for
        the next one.
    """
    limit = min(limit, PAGE_LIMIT_MAX)
    # Not `_load_ledger`: that one raises 404 for an empty ledger, which is
    # the right answer for a dashboard with nothing to draw and the wrong one
    # for a paged collection, where "no rows" is an empty page with a total
    # of zero. The 404 stays on every other route in this module.
    page = main.load_ledger_page(session, user_id, limit=limit, offset=offset)
    return LedgerEventPage(
        items=[LedgerEvent(**row) for row in page.to_dicts()],
        window_unit="event",
        total=main.ledger_event_count(session, user_id),
        limit=limit,
        offset=offset,
    )
