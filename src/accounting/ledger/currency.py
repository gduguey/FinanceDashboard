"""Convert an amount between the two supported currencies, using one stored EUR/USD rate.

Two currencies is a closed set — `convert` is a plain three-way branch, not
a general exchange-rate graph, and stays that way until a third currency
is ever actually needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from accounting.models import CurrencyCode


@dataclass(frozen=True)
class DisplayCurrency:
    """A currency to aggregate into, plus the one rate needed to get there.

    Bundles the two values every aggregation function
    (`dashboard.net_worth`, `dashboard.income_statement`) always needs
    together — a display currency is meaningless to convert into without
    the rate, so callers never pass one without the other.
    """

    code: CurrencyCode = "USD"
    eur_usd_rate: float = 1.08


def convert(amount: float, from_currency: CurrencyCode, to_currency: CurrencyCode, eur_usd_rate: float) -> float:
    """Convert a signed amount from one supported currency to the other.

    Parameters
    ----------
    amount
        The amount, in `from_currency`.
    from_currency
        The currency `amount` is denominated in.
    to_currency
        The currency to convert into.
    eur_usd_rate
        How many US dollars one euro buys — the single stored rate every
        conversion in this app uses, in either direction.

    Returns
    -------
    float
        `amount`, converted into `to_currency`.
    """
    if from_currency == to_currency:
        return amount
    if from_currency == "EUR" and to_currency == "USD":
        return amount * eur_usd_rate
    return amount / eur_usd_rate
