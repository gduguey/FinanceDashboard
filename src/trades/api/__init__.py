"""Re-exports `app` (the one FastAPI instance) and module-under-test targets tests monkeypatch directly."""

from trades.api.api import app
from trades.api.dependencies import main
from trades.api.routers.market_data import hysa_rates_module, prices, symbol_search_module
from trades.market_data import cpi as cpi_module

__all__ = ["app", "cpi_module", "hysa_rates_module", "main", "prices", "symbol_search_module"]
