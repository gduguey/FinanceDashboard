"""Dashboard aggregation layer — the API's only source of computed data.

Combines `ledger.*` (replay, lots, metrics, counterfactuals, nav) and
`market_data.*` into the exact shapes `api.py` serves over HTTP; `api.py`
itself does no aggregation, matching `docs/trades/architecture.md`'s split
between the layer that computes something and the layer that serializes
it.
"""

from trades.dashboard.cash_sitting import (
    CashLot,
    CashSittingSummary,
    cash_received_counterfactual,
    cash_sitting_summary,
    daily_cash_balances,
    open_cash_lots,
)
from trades.dashboard.charts import (
    dollar_chart_series,
    growth_of_100_chart,
    monthly_pnl,
    monthly_pnl_by_symbol,
    reallocation_markers,
    risk_stat,
)
from trades.dashboard.holdings import LotsTable, allocation_view, data_quality, lots_table
from trades.dashboard.overview import OverviewCards, overview_cards
from trades.dashboard.settings import (
    DashboardSettings,
    hysa_rate_lookup,
    load_settings,
    resolved_benchmark_symbol,
    resolved_marginal_ordinary_rate,
    resolved_nra_dividend_tax_rate,
    resolved_qualified_ltcg_rate,
    resolved_tax_regime,
    save_settings,
)
from trades.dashboard.tax import LiquidationEstimate, TaxSummary, tax_summary
from trades.dashboard.valuation import daily_portfolio_values, make_price_lookup

__all__ = [
    "CashLot",
    "CashSittingSummary",
    "DashboardSettings",
    "LiquidationEstimate",
    "LotsTable",
    "OverviewCards",
    "TaxSummary",
    "allocation_view",
    "cash_received_counterfactual",
    "cash_sitting_summary",
    "daily_cash_balances",
    "daily_portfolio_values",
    "data_quality",
    "dollar_chart_series",
    "growth_of_100_chart",
    "hysa_rate_lookup",
    "load_settings",
    "lots_table",
    "make_price_lookup",
    "monthly_pnl",
    "monthly_pnl_by_symbol",
    "open_cash_lots",
    "overview_cards",
    "reallocation_markers",
    "resolved_benchmark_symbol",
    "resolved_marginal_ordinary_rate",
    "resolved_nra_dividend_tax_rate",
    "resolved_qualified_ltcg_rate",
    "resolved_tax_regime",
    "risk_stat",
    "save_settings",
    "tax_summary",
]
