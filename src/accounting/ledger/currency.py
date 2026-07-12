"""Convert an amount between any two supported currencies, via one shared base-currency rate table.

Adding a new `CurrencyCode` (see `accounting.models`) never touches this
file — `convert` only ever needs a `rates_to_base` entry for whichever
codes it's asked to convert between, so nothing here hardcodes which
currencies exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from accounting.models import BASE_CURRENCY

if TYPE_CHECKING:
    from accounting.models import CurrencyCode


@dataclass(frozen=True)
class DisplayCurrency:
    """A currency to aggregate into, plus the rate table needed to get there.

    Bundles the two values every aggregation function
    (`dashboard.net_worth`, `dashboard.income_statement`) always needs
    together — a display currency is meaningless to convert into without
    rates for the currencies actually involved, so callers never pass one
    without the other. `rates_to_base` maps every relevant `CurrencyCode`
    to how many `BASE_CURRENCY` units one unit of it is worth
    (`BASE_CURRENCY` itself always maps to `1.0`) — see
    `market_data.exchange_rates.current_rates_to_base`.
    """

    code: CurrencyCode = BASE_CURRENCY
    rates_to_base: dict[CurrencyCode, float] = field(default_factory=lambda: {BASE_CURRENCY: 1.0})


def convert(
    amount: float, from_currency: CurrencyCode, to_currency: CurrencyCode, rates_to_base: dict[CurrencyCode, float]
) -> float:
    """Convert a signed amount from one supported currency to another, through the shared base currency.

    Parameters
    ----------
    amount
        The amount, in `from_currency`.
    from_currency
        The currency `amount` is denominated in.
    to_currency
        The currency to convert into.
    rates_to_base
        Every relevant currency's rate into `accounting.models.BASE_CURRENCY`
        (which itself must map to `1.0`) — see `DisplayCurrency`. A missing
        entry for `from_currency`/`to_currency` raises `KeyError`.

    Returns
    -------
    float
        `amount`, converted into `to_currency`.
    """
    if from_currency == to_currency:
        return amount
    amount_in_base = amount * rates_to_base[from_currency]
    return amount_in_base / rates_to_base[to_currency]
