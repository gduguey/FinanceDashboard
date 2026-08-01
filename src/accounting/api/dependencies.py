"""`state` and private helpers shared by 2+ of `accounting.api`'s routers.

A helper only ever called from within a single router's own file stays
defined there instead — see that router module for those. `state` lives
here (rather than in `accounting.api.api`) so every router can import it
without a circular import back through the module that imports them.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import polars as pl
from fastapi import HTTPException
from sqlalchemy.orm import Session

from accounting.config import AccountingConfig
from accounting.importers.ingest import load_ledger
from accounting.ledger.currency import DisplayCurrency, rates_into_display
from accounting.market_data import exchange_rates
from accounting.models import BASE_CURRENCY, CurrencyCode
from accounting.repositories.planning import load_goal_contributions
from accounting.repositories.taxonomy import load_other_assets
from accounting.taxonomy import seeded_accounts

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterable


class _State:
    """Everything a running server needs, held off the shared `app` object so tests can swap it per-test."""

    def __init__(self) -> None:
        """Load the default `AccountingConfig`."""
        self.config = AccountingConfig()


state = _State()


def _account_has_postings(account_id: str, session: Session, user_id: uuid.UUID) -> bool:
    """Check whether any imported posting has ever been assigned to this account.

    Used to enforce the accounts-CRUD rule: an account's institution,
    kind, and currency (and the account itself) may only be edited or
    deleted before any real transaction has landed on it — afterward,
    only its display name may change.

    Returns
    -------
    bool
        `True` if at least one posting in the raw ledger references this account.
    """
    ledger = load_ledger(session, user_id)
    if ledger.is_empty():
        return False
    return bool(ledger.filter(pl.col("account_id") == account_id).height > 0)


def _currencies_in_use(session: Session, user_id: uuid.UUID) -> set[CurrencyCode]:
    """Find every currency this user actually holds money in, across all three places one can be named.

    Read once per request and passed to `_display_currency`, rather than
    re-read per date by an endpoint building a history series.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose currencies to collect.

    Returns
    -------
    set[CurrencyCode]
        Every account's, other asset's, and goal contribution's own currency.
    """
    return (
        {account.currency for account in seeded_accounts(session, user_id).values()}
        | {asset.currency for asset in load_other_assets(session, user_id)}
        | {contribution.currency for contribution in load_goal_contributions(session, user_id).values()}
    )


def _display_currency(
    code: CurrencyCode, currencies: Iterable[CurrencyCode] = (), as_of: date | None = None
) -> DisplayCurrency:
    """Build a `DisplayCurrency` from the cached exchange-rate history's smoothed rate as of a date.

    Only ever requires history for the currencies actually in play —
    `code` itself, plus whatever `currencies` names — never every
    `CurrencyCode` this app could theoretically support, so a user with
    no EUR accounts yet isn't blocked from a USD-only net worth just
    because EUR was never synced.

    Parameters
    ----------
    code
        The currency to display aggregates in.
    currencies
        Every other currency the caller's own figures are held in (see
        `_currencies_in_use`); empty (e.g. a standalone rate lookup) only
        requires `code` itself.
    as_of
        The date to compute the smoothed rate as of; defaults to today.

    Returns
    -------
    DisplayCurrency

    Raises
    ------
    HTTPException
        400 if exchange rates have never been synced (or lack history for
        a needed currency) — sync first, rather than silently guessing a rate.
    """
    needed = {code, *currencies}
    history = exchange_rates.load_rate_history(state.config)
    try:
        rates = exchange_rates.current_rates_to_base(history, as_of or datetime.now(tz=UTC).date(), needed)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return DisplayCurrency(code, rates)


def _rates_by_date(code: CurrencyCode, currencies: Iterable[CurrencyCode] = ()) -> pl.DataFrame | None:
    """Build the per-date rate table `ledger.currency.with_converted_amount` converts flows with.

    `None` when the base currency is the only one in play, because then
    there is nothing to convert: every rate into it is `1.0` on every day,
    and the scalar path already says so. That also keeps the table off the
    request entirely for the common single-currency case.

    Split out from `_flow_display_currency` for the one caller that needs
    the table and the scalar rates on *different* dates:
    `routers.goals` values dated contributions with this and undated
    opening balances at an `as_of`, and answers on two dates per request.

    Parameters
    ----------
    code
        The currency every rate is divided into.
    currencies
        Every other currency the caller's own figures are held in (see `_currencies_in_use`).

    Returns
    -------
    polars.DataFrame or None
        Columns `currency`, `rate_date`, `rate_into_display`.

    Raises
    ------
    HTTPException
        400 if exchange rates have never been synced (or lack history for
        a needed currency) — the same refusal `_display_currency` makes.
    """
    needed = {code, *currencies}
    if needed == {BASE_CURRENCY}:
        return None
    try:
        series = exchange_rates.smoothed_rate_series(exchange_rates.load_rate_history(state.config), needed)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return rates_into_display(series, code)


def _flow_display_currency(code: CurrencyCode, currencies: Iterable[CurrencyCode] = ()) -> DisplayCurrency:
    """Build a `DisplayCurrency` that converts each dated row at its own date's smoothed rate.

    The counterpart to `_display_currency` for an aggregation over flows
    rather than a balance on one date: the income statement, budgets, the
    spend curve, and goals. Those used to pass no `as_of` at all, so every
    row in them — including one from two years ago — was converted at
    today's rate, and last March's total moved whenever the currency
    market did.

    Parameters
    ----------
    code
        The currency to display aggregates in.
    currencies
        Every other currency the caller's own figures are held in (see `_currencies_in_use`).

    Returns
    -------
    DisplayCurrency
    """
    return replace(_display_currency(code, currencies), rates_by_date=_rates_by_date(code, currencies))
