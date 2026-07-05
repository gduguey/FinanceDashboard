"""Compound-interest projector: the Finary-style calculator's five inputs, projected forward month by month.

Pure math, no ledger data required — `initial_capital` is only ever
*defaulted* from a real account's current balance by the caller (see
`api.get_net_worth`), never read here, so this module works identically
for a real account or a hypothetical "what if I saved $500/mo" scenario.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CompoundingFrequency = Literal["annually", "monthly", "daily"]

_PERIODS_PER_YEAR: dict[CompoundingFrequency, int] = {"annually": 1, "monthly": 12, "daily": 365}


@dataclass(frozen=True)
class ProjectionPoint:
    """One month's projected balance, and how much of it is contributions rather than growth."""

    month: int
    balance: float
    contributions_to_date: float


def _effective_monthly_rate(annual_rate_pct: float, compounding_frequency: CompoundingFrequency) -> float:
    """Convert an annual rate compounding at some frequency into the equivalent effective monthly rate.

    Every projection step is monthly regardless of `compounding_frequency`
    — that only changes how often the *annual* rate itself compounds
    before being converted to a monthly-equivalent, the same distinction a
    savings account's "APY" (already compounded) makes against its
    "nominal rate" (not yet).

    Returns
    -------
    float
        The effective monthly rate, as a fraction (not a percent).
    """
    periods_per_year = _PERIODS_PER_YEAR[compounding_frequency]
    annual_rate = annual_rate_pct / 100
    return (1 + annual_rate / periods_per_year) ** (periods_per_year / 12) - 1


def project(
    initial_capital: float,
    monthly_contribution: float,
    horizon_years: float,
    annual_rate_pct: float,
    compounding_frequency: CompoundingFrequency = "monthly",
) -> list[ProjectionPoint]:
    """Month-by-month compound growth of a starting balance plus a level monthly contribution.

    Parameters
    ----------
    initial_capital
        The starting balance.
    monthly_contribution
        Added at the end of every month, after that month's growth.
    horizon_years
        How many years to project forward; fractional years are rounded to the nearest month.
    annual_rate_pct
        The annual growth rate, as a percent (e.g. `5.0` for 5%).
    compounding_frequency
        How often the annual rate itself compounds; see `_effective_monthly_rate`.

    Returns
    -------
    list[ProjectionPoint]
        One point per month from 0 (today) through the full horizon, inclusive.
    """
    monthly_rate = _effective_monthly_rate(annual_rate_pct, compounding_frequency)
    months = round(horizon_years * 12)
    balance = initial_capital
    contributed = initial_capital
    points = [ProjectionPoint(month=0, balance=balance, contributions_to_date=contributed)]
    for month in range(1, months + 1):
        balance = balance * (1 + monthly_rate) + monthly_contribution
        contributed += monthly_contribution
        points.append(ProjectionPoint(month=month, balance=balance, contributions_to_date=contributed))
    return points
