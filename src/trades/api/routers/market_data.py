"""Market-data endpoints — mirrors `trades.market_data`: HYSA rates, symbol search, on-demand price refresh."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.current_user import get_current_user_id
from db.session import get_db
from trades.api.api_models import HysaBank, HysaRatePoint, HysaRates, SymbolPriceStatus, SymbolSearchResult
from trades.api.dependencies import _config, _first_event_date, _load_ledger
from trades.market_data import hysa_rates as hysa_rates_module
from trades.market_data import prices
from trades.market_data import symbol_search as symbol_search_module
from trades.utils.frames import collect_if_lazy

router = APIRouter()


@router.get("/api/hysa-rates")
def get_hysa_rates() -> HysaRates:
    """Return every bank's known rate history, for the bank picker and APY comparison chart.

    Returns
    -------
    HysaRates
        `banks` (id/name pairs), `history` (every rate-change row), `default_bank_id`.
    """
    config = _config()
    history = hysa_rates_module.load_hysa_rates_cache(config)
    banks = collect_if_lazy(hysa_rates_module.list_banks(history))
    return HysaRates(
        banks=[HysaBank(**row) for row in banks.to_dicts()],
        history=[HysaRatePoint(**row) for row in history.to_dicts()],
        default_bank_id=config.hysa_rates.default_bank_id,
    )


@router.get("/api/symbols/search")
def get_symbol_search(q: str) -> list[SymbolSearchResult]:
    """Search Yahoo Finance for a ticker symbol, for the benchmark picker.

    Unlike every other GET endpoint, this touches the network — a live
    search box needs a live answer, and there's nothing here to cache.

    Returns
    -------
    list[SymbolSearchResult]
        One entry per match: `symbol`, `name`, `exchange`.
    """
    return [SymbolSearchResult(**row) for row in symbol_search_module.search_symbols(q, _config())]


@router.post("/api/symbols/{symbol}/ensure-priced")
def ensure_symbol_priced(
    symbol: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> SymbolPriceStatus:
    """Refresh one symbol's price cache if it isn't already current, without a full sync.

    Lets picking a new benchmark symbol take effect immediately —
    `prices.update_price_cache` only fetches whatever date range is
    actually missing, so re-running this on an already-current symbol is
    cheap and safe to call on every selection.

    Returns
    -------
    SymbolPriceStatus
        `symbol`, `was_stale` (whether a fetch was actually needed), `last_price_date`.

    Raises
    ------
    HTTPException
        404 if no ledger is cached yet; 422 if Yahoo Finance has no data for `symbol`.
    """
    config = _config()
    ledger = _load_ledger(session, user_id)
    first_event = _first_event_date(ledger)
    today = datetime.now(tz=UTC).date()

    existing = prices.load_price_cache(symbol, config)
    was_stale = existing.is_empty() or cast("date", existing["price_date"].max()) < today
    try:
        prices.update_price_cache(symbol, since=first_event, as_of=today, config=config)
        updated = prices.update_price_cache(symbol, since=first_event, as_of=today, config=config, adjusted=True)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    last_price_date = updated["price_date"].max() if not updated.is_empty() else None
    return SymbolPriceStatus(
        symbol=symbol,
        was_stale=was_stale,
        last_price_date=str(last_price_date) if last_price_date else None,
    )
