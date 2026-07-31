"""Portfolio-performance read endpoints — mirrors `trades.dashboard`: overview, charts, lots, risk, tax report."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.current_user import get_current_user_id
from db.session import get_db
from trades import dashboard
from trades.api.api_models import (
    AllocationRow,
    AnnualTaxRow,
    CashHistoryPoint,
    CashSitting,
    ClosedLotRow,
    DataQualityRow,
    DollarChart,
    DollarChartPoint,
    GrowthOf100Point,
    LotsTable,
    MonthlyPnlBySymbolRow,
    MonthlyPnlRow,
    OpenLotRow,
    Overview,
    ReallocationMarker,
    RiskStat,
    SalePreviewRow,
    SymbolRollupRow,
    TaxOwedRow,
    TaxReport,
    WashSaleRow,
)
from trades.api.dependencies import _config, _first_event_date, _last_synced_iso, _load_ledger
from trades.api.entities import LedgerEvent

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


@router.get("/lots")
def get_lots(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    as_of: date | None = None,
) -> LotsTable:
    """Return the trade-level table: open lots, closed lots, per-symbol rollup.

    Returns
    -------
    LotsTable
        `open_lots`, `closed_lots`, `symbol_rollup`.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if a required price is missing.
    """
    ledger = _load_ledger(session, user_id)
    settings = dashboard.load_settings(session, user_id)
    try:
        table = dashboard.lots_table(ledger, _config(), settings, as_of or datetime.now(tz=UTC).date())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return LotsTable(
        open_lots=[OpenLotRow(**row) for row in table.open_lots.to_dicts()],
        closed_lots=[ClosedLotRow(**row) for row in table.closed_lots.to_dicts()],
        symbol_rollup=[SymbolRollupRow(**row) for row in table.symbol_rollup.to_dicts()],
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
    session: Annotated[Session, Depends(get_db)], user_id: Annotated[uuid.UUID, Depends(get_current_user_id)]
) -> list[LedgerEvent]:
    """Export the full ledger, for the user's own backup.

    Returns
    -------
    list[LedgerEvent]
        Every ledger row.
    """
    return [LedgerEvent(**row) for row in _load_ledger(session, user_id).to_dicts()]
