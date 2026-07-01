"""Map each broker's native trade-history shape onto the canonical schema
declared in `config.TradeSchema` (validated via `models.RawTrade`), so
`transactions.py` and everything downstream never has to know which broker
a trade came from.

One `standardize_{broker}_trades` function per broker. See AGENTS.md /
CLAUDE.md for the convention this file exists to enforce: a new data source
gets a function here, not ad hoc column names leaking into the rest of the
app.
"""

from __future__ import annotations

import pandas as pd

from trades.config import TradeSchema
from trades.models import RawTrade

_SCHEMA = TradeSchema()
_CANONICAL_COLUMNS = [_SCHEMA.trade_date, _SCHEMA.symbol, _SCHEMA.shares, _SCHEMA.usd_spent]


def standardize_ibkr_trades(ibkr_trades: pd.DataFrame) -> pd.DataFrame:
    """Map `ibkr.load_trade_history`'s native columns onto the canonical
    trade schema.

    Only genuine `BUY` fills are kept: `SELL` and the `BUY (Ca.)`/`SELL
    (Ca.)` correction rows fall outside what "USD invested" means to
    `transactions.py`'s pipeline. `usd_spent` comes from `net_cash` (the
    trade's total cash effect, commission included), not `trade_money`.
    """
    buys = ibkr_trades[ibkr_trades["buy_sell"] == "BUY"]
    if buys.empty:
        return pd.DataFrame(columns=_CANONICAL_COLUMNS).astype(
            {
                _SCHEMA.trade_date: "datetime64[ns]",
                _SCHEMA.shares: "float64",
                _SCHEMA.usd_spent: "float64",
            }
        )

    standardized = pd.DataFrame(
        {
            _SCHEMA.trade_date: buys["trade_date"].dt.date,
            _SCHEMA.symbol: buys["symbol"],
            _SCHEMA.shares: buys["quantity"].abs(),
            _SCHEMA.usd_spent: buys["net_cash"].abs(),
        }
    )
    validated = [RawTrade.model_validate(row.to_dict()) for _, row in standardized.iterrows()]
    df = pd.DataFrame([t.model_dump() for t in validated])
    df[_SCHEMA.trade_date] = pd.to_datetime(df[_SCHEMA.trade_date])
    return df.sort_values([_SCHEMA.trade_date, _SCHEMA.symbol]).reset_index(drop=True)
