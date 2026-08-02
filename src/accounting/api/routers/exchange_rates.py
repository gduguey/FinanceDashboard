"""Exchange-rate endpoints — mirrors `accounting.market_data.exchange_rates`."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, cast

import polars as pl
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from accounting.api.api_models import CurrentExchangeRate, ExchangeRateHistoryPoint, RateCoverage
from accounting.api.dependencies import _currencies_in_use, _display_currency, state
from accounting.market_data import exchange_rates
from accounting.models import BASE_CURRENCY, CurrencyCode
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()


@router.get("/exchange-rates/current")
def get_current_exchange_rate(currency: CurrencyCode) -> CurrentExchangeRate:
    """Return the smoothed rate this app currently uses for one currency, and how it's computed.

    Returns
    -------
    CurrentExchangeRate
    """
    as_of = datetime.now(tz=UTC).date()
    display = _display_currency(currency, as_of=as_of)
    return CurrentExchangeRate(
        currency=currency,
        base_currency=BASE_CURRENCY,
        rate_to_base=display.rates_to_base[currency],
        as_of=as_of,
        window_days=exchange_rates.DEFAULT_SMOOTHING_WINDOW_DAYS,
    )


@router.get("/exchange-rates/coverage")
def get_exchange_rate_coverage(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    display_currency: CurrencyCode = BASE_CURRENCY,
) -> RateCoverage:
    """Report the span of rate history this user's dated flows are actually converted with.

    Item A5. The flow aggregations — the income statement, budgets, the
    spend curve, goals — convert each row at its own date's rate, and a
    row older than the cache is clamped to the oldest trailing mean on
    file. Nothing on screen said so. This is what a client needs to say
    it: a window starting before `earliest` holds at least one figure
    that is an approximation rather than the rate of its own day.

    Answers `None`/`None` when nothing is converted, which is exactly
    when `_rates_by_date` returns `None` — `display_currency` and every
    currency this user holds are all the base currency. The condition is
    computed the same way here rather than restated, so the note a client
    draws from this cannot come apart from whether a clamp can happen.

    The span is read off the cached history rather than off
    `smoothed_rate_series`, which would build the whole per-date table to
    have its min and max taken: that series spans exactly the history's
    own first and last day by construction (see its `pl.date_range`), and
    `rates_into_display` inner-joins the display currency, which has a
    row on every one of those days.

    Returns
    -------
    RateCoverage

    Raises
    ------
    HTTPException
        400 if a currency in use has no rate history at all — the same
        refusal every flow endpoint already makes, rather than reporting
        a coverage window for rates that cannot be built.
    """
    currencies = {display_currency, *_currencies_in_use(session, user_id)}
    if currencies == {BASE_CURRENCY}:
        return RateCoverage(earliest=None, latest=None)
    history = exchange_rates.load_rate_history(state.config)
    for code in sorted(currencies - {BASE_CURRENCY}):
        if history.filter(pl.col("currency") == code).is_empty():
            raise HTTPException(
                status_code=400, detail=f"No exchange-rate history for {code!r} — sync exchange rates first."
            )
    return RateCoverage(earliest=cast("date", history["date"].min()), latest=cast("date", history["date"].max()))


@router.get("/exchange-rates/history")
def get_exchange_rate_history(currency: CurrencyCode) -> list[ExchangeRateHistoryPoint]:
    """Return the cached daily rate history for one currency, alongside the smoothed value at each point.

    Returns
    -------
    list[ExchangeRateHistoryPoint]
        One point per cached day, oldest first.

    Raises
    ------
    HTTPException
        400 if exchange rates have never been synced.
    """
    history = exchange_rates.load_rate_history(state.config)
    series = history.filter(pl.col("currency") == currency).sort("date")
    if series.is_empty():
        raise HTTPException(
            status_code=400, detail=f"No exchange-rate history for {currency!r} — sync exchange rates first."
        )
    return [
        ExchangeRateHistoryPoint(
            date=row["date"],
            rate=row["rate_to_base"],
            # Never `None` here: `row["date"]` is itself a cached history day
            # for `currency`, so the trailing window ending on it always has
            # at least that one point to average.
            smoothed_rate=cast("float", exchange_rates.smoothed_rate_as_of(history, currency, row["date"])),
        )
        for row in series.iter_rows(named=True)
    ]
