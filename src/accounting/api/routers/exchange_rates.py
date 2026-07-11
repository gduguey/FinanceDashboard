"""Exchange-rate endpoints — mirrors `accounting.market_data.exchange_rates`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import polars as pl
from fastapi import APIRouter, HTTPException

from accounting.api.api_models import CurrentExchangeRate, ExchangeRateHistoryPoint
from accounting.api.dependencies import _display_currency, state
from accounting.market_data import exchange_rates
from accounting.models import BASE_CURRENCY, CurrencyCode

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
