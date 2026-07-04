"""Ad-hoc ticker symbol search against Yahoo Finance's public search endpoint.

Unlike `prices.py`/`cpi.py`/`hysa_rates.py`, nothing here is cached to
disk: a symbol search is a live, on-demand lookup for the frontend's
benchmark picker, not a data source the rest of the app replays against.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from trades.config import AppConfig


def search_symbols(query: str, config: AppConfig, session: requests.Session | None = None) -> list[dict[str, str]]:
    """Search Yahoo Finance for ticker symbols matching a free-text query.

    Parameters
    ----------
    query
        Free-text search, e.g. a company name or partial ticker.
    config
        Application configuration; `config.symbol_search` is read.
    session
        HTTP session to use instead of the top-level `requests` module.

    Returns
    -------
    list[dict[str, str]]
        One entry per match, in Yahoo's relevance order: `symbol`,
        `name` (falls back to the symbol itself if Yahoo gives no name),
        `exchange`.
    """
    http = session or requests
    params: dict[str, int | str] = {"q": query, "quotesCount": config.symbol_search.max_results, "newsCount": 0}
    response = http.get(
        config.symbol_search.search_url,
        params=params,
        headers=config.symbol_search.request_headers,
        timeout=config.symbol_search.request_timeout_seconds,
    )
    response.raise_for_status()
    quotes = response.json().get("quotes", [])
    return [
        {
            "symbol": quote["symbol"],
            "name": quote.get("longname") or quote.get("shortname") or quote["symbol"],
            "exchange": quote.get("exchDisp", ""),
        }
        for quote in quotes
        if "symbol" in quote
    ]
