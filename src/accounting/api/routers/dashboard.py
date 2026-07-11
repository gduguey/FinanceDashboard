"""Read-only view endpoints — mirrors `accounting.dashboard.*`: budgets, income statement, interest, net worth."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Annotated, cast

import polars as pl
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    BudgetComparisonRow,
    CategoryTotalRow,
    InterestAccountRow,
    MonthlyIncomeExpenseRow,
    NetWorthAccountRow,
    NetWorthHistoryByAccountPoint,
    NetWorthHistoryPoint,
    NetWorthSummary,
    ProjectionPoint,
    SpendCurvePoint,
    SuggestedBudgetAmount,
)
from accounting.api.dependencies import (
    _display_currency,
    _resolved_postings_and_store,
    _resolved_postings_for_aggregation,
    state,
)
from accounting.dashboard import budgets, income_statement, interest, simulator
from accounting.dashboard.net_worth import net_worth_summary
from accounting.ledger.currency import convert
from accounting.ledger.replay import account_balances_over_time
from accounting.models import CurrencyCode
from accounting.utils.io_utils import collect_if_lazy
from db.session import get_db

router = APIRouter()

_VIRTUAL_ACCOUNT_KINDS = {"income_source", "expense_payee"}


@router.get("/simulator/project")
def get_simulator_projection(
    initial_capital: float,
    monthly_contribution: float,
    horizon_years: float,
    annual_rate_pct: float,
    compounding_frequency: simulator.CompoundingFrequency = "monthly",
) -> list[ProjectionPoint]:
    """Project a compound-interest scenario forward, month by month.

    Returns
    -------
    list[ProjectionPoint]
    """
    points = simulator.project(
        initial_capital, monthly_contribution, horizon_years, annual_rate_pct, compounding_frequency
    )
    return [ProjectionPoint(**vars(point)) for point in points]


def _external_investment_values_usd(dates: list[date], session: Session) -> dict[date, float] | None:
    """Look up the tracked investment portfolio's value as of each requested date, from `trades`'s own ledger.

    Imported lazily, and reads the *running* `trades.api` app's own
    `app.state.config` (the same one its own endpoints use, so this
    reflects whatever cache directory that server is actually configured
    for) rather than a disconnected default — this is the one place
    accounting code reaches into trades at all. `session` is the same
    session this request's own route already holds — `accounting.*` and
    `trades.*` are separate Postgres schemas in one database, so one
    session can query both.

    Uses `trades.dashboard.valuation.daily_portfolio_values` (one batched,
    O(unique event dates) computation) rather than calling `overview_cards`
    once per date — that would replay the whole ledger per point, and net
    worth history can ask for a year of daily points. The valuation range
    is widened back to the ledger's own first event so a request whose
    window starts after investing began still sees that day's real value,
    not 0 (`daily_portfolio_values` only considers events inside the range
    it's given).

    Parameters
    ----------
    dates
        Every date a value is needed for.
    session
        An open database session.

    Returns
    -------
    dict[date, float] or None
        Portfolio value for each requested date (0.0 for dates before the
        first investment event), or `None` if trades has never been synced.
    """
    from trades import api as trades_api  # noqa: PLC0415
    from trades.brokers.ibkr import main as trades_main  # noqa: PLC0415
    from trades.dashboard.valuation import daily_portfolio_values, make_price_lookup  # noqa: PLC0415

    trades_config = trades_api.app.state.config
    ledger = trades_main.load_ledger(session)
    if ledger.is_empty():
        return None
    first_event_date = cast("date", ledger["event_datetime"].dt.date().min())
    start = min(first_event_date, *dates)
    end = max(dates)
    price_lookup = make_price_lookup(trades_config)
    daily = cast("pl.DataFrame", daily_portfolio_values(ledger, price_lookup, start, end, trades_config))
    return dict(zip(daily["date"].to_list(), daily["value"].to_list(), strict=True))


def _benchmark_apy_pct(as_of: date) -> float | None:
    """Look up `trades`'s published HYSA rate as of a date, as a percent, to compare vault/savings APYs against.

    Imported lazily, reading the *running* `trades.api` app's own
    `app.state.config` — the same reasoning as `_external_investment_values_usd`.

    Returns
    -------
    float or None
        The benchmark rate as a percent (e.g. `4.2`), or `None` if `trades` has no rate configured.
    """
    from trades import api as trades_api  # noqa: PLC0415
    from trades.dashboard.settings import hysa_rate_lookup  # noqa: PLC0415

    trades_config = trades_api.app.state.config
    try:
        rate = hysa_rate_lookup(trades_config)(as_of)
    except Exception:  # noqa: BLE001 - a missing/misconfigured HYSA rate shouldn't block the rest of the view
        return None
    return rate * 100


@router.get("/interest-summary")
def get_interest_summary(
    *, as_of: date | None = None, session: Annotated[Session, Depends(get_db)]
) -> list[InterestAccountRow]:
    """Every savings/vault account's year-to-date interest, current APY, balance, and a one-year projection.

    Returns
    -------
    list[InterestAccountRow]
    """
    postings, store = _resolved_postings_and_store(state.config, session)
    resolved_as_of = as_of or datetime.now(tz=UTC).date()
    rows = interest.interest_summary(postings, store.accounts, resolved_as_of, _benchmark_apy_pct(resolved_as_of))
    return [InterestAccountRow(**vars(row)) for row in rows]


@router.get("/net-worth")
def get_net_worth(
    *, as_of: date | None = None, display_currency: CurrencyCode = "USD", session: Annotated[Session, Depends(get_db)]
) -> NetWorthSummary:
    """Return the full net-worth view: every account's balance, grouped, plus manually-added assets.

    Returns
    -------
    NetWorthSummary
    """
    postings, store = _resolved_postings_and_store(state.config, session)
    has_external_investment = any(
        account.kind == "external_investment" and account.external_ref == "trades"
        for account in store.accounts.values()
    )
    resolved_as_of = as_of or datetime.now(tz=UTC).date()
    external_values = _external_investment_values_usd([resolved_as_of], session) if has_external_investment else None
    summary = net_worth_summary(
        postings,
        store.accounts,
        store.other_assets,
        resolved_as_of,
        _display_currency(display_currency, store, resolved_as_of),
        external_investment_value_usd=(external_values or {}).get(resolved_as_of) if external_values else None,
        opening_balances=store.opening_balances,
    )
    return NetWorthSummary(
        as_of=summary.as_of,
        display_currency=summary.display_currency,
        assets=summary.assets,
        liabilities=summary.liabilities,
        other_assets_total=summary.other_assets_total,
        net_worth=summary.net_worth,
        accounts=[NetWorthAccountRow(**vars(row)) for row in summary.accounts],
        other_assets=summary.other_assets,
    )


@router.get("/net-worth/history")
def get_net_worth_history(
    start: date,
    end: date,
    *,
    interval_days: int = 1,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
) -> list[NetWorthHistoryPoint]:
    """Return net worth as of a regularly-spaced series of dates, for a history chart.

    Each point uses that date's own smoothed exchange rate (see
    `market_data.exchange_rates`), not today's — a EUR account's value ten
    months ago is converted at what the rate actually was ten months ago,
    not backdated with today's rate.

    Returns
    -------
    list[NetWorthHistoryPoint]
        Oldest first.
    """
    postings, store = _resolved_postings_and_store(state.config, session)
    has_external_investment = any(
        account.kind == "external_investment" and account.external_ref == "trades"
        for account in store.accounts.values()
    )
    dates = pl.date_range(start, end, interval=f"{interval_days}d", eager=True).to_list()
    external_values = _external_investment_values_usd(dates, session) if has_external_investment else None
    return [
        NetWorthHistoryPoint(
            date=day,
            net_worth=net_worth_summary(
                postings,
                store.accounts,
                store.other_assets,
                day,
                _display_currency(display_currency, store, day),
                external_investment_value_usd=(external_values or {}).get(day, 0.0) if external_values else None,
                opening_balances=store.opening_balances,
            ).net_worth,
        )
        for day in dates
    ]


@router.get("/net-worth/history/by-account")
def get_net_worth_history_by_account(
    start: date,
    end: date,
    *,
    interval_days: int = 1,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
) -> list[NetWorthHistoryByAccountPoint]:
    """Return every real account's own balance as of a regularly-spaced series of dates.

    The per-account counterpart to `get_net_worth_history` — same dates,
    same as-of-date exchange rate handling, but one row per (date,
    account) instead of one aggregate net-worth figure per date, for the
    net worth chart's "detailed" per-account view.

    Returns
    -------
    list[NetWorthHistoryByAccountPoint]
        `balance` already converted into `display_currency`.
    """
    postings, store = _resolved_postings_and_store(state.config, session)
    dates = pl.date_range(start, end, interval=f"{interval_days}d", eager=True).to_list()
    real_accounts = {
        account_id: account
        for account_id, account in store.accounts.items()
        if account.kind not in _VIRTUAL_ACCOUNT_KINDS
    }
    has_external_investment = any(
        account.kind == "external_investment" and account.external_ref == "trades" for account in real_accounts.values()
    )
    external_values = _external_investment_values_usd(dates, session) if has_external_investment else None

    balances = cast("pl.DataFrame", account_balances_over_time(postings, dates))
    balance_lookup = {(row["account_id"], row["date"]): row["balance"] for row in balances.to_dicts()}

    rows: list[NetWorthHistoryByAccountPoint] = []
    for day in dates:
        display = _display_currency(display_currency, store, day)
        for account_id, account in real_accounts.items():
            if account.kind == "external_investment" and account.external_ref == "trades":
                native = (external_values or {}).get(day, 0.0)
            else:
                native = balance_lookup.get((account_id, day), 0.0)
                opening = store.opening_balances.get(account_id)
                if opening is not None and day >= opening.as_of_date.date():
                    native += opening.amount
            rows.append(
                NetWorthHistoryByAccountPoint(
                    date=day,
                    account_id=account_id,
                    account_name=account.name,
                    balance=convert(native, account.currency, display.code, display.rates_to_base),
                )
            )
    return rows


@router.get("/income-statement/category-totals")
def get_category_totals(
    start: date,
    end: date,
    *,
    account_ids: str | None = None,
    tag_id: str | None = None,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
) -> list[CategoryTotalRow]:
    """Sum real income/expense postings by classification, category, and subcategory.

    Returns
    -------
    list[CategoryTotalRow]
    """
    postings, store = _resolved_postings_for_aggregation(state.config, session)
    parsed_account_ids = account_ids.split(",") if account_ids else None
    totals = collect_if_lazy(
        income_statement.category_totals(
            postings,
            store.accounts,
            store.categories,
            start,
            end,
            income_statement.Scope(parsed_account_ids, tag_id),
            _display_currency(display_currency, store),
        )
    )
    return [CategoryTotalRow(**row) for row in totals.to_dicts()]


@router.get("/income-statement/monthly")
def get_monthly_income_expense(
    start: date,
    end: date,
    *,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
) -> list[MonthlyIncomeExpenseRow]:
    """Sum real income and real expense per calendar month.

    Returns
    -------
    list[MonthlyIncomeExpenseRow]
    """
    postings, store = _resolved_postings_for_aggregation(state.config, session)
    rows = collect_if_lazy(
        income_statement.monthly_income_expense(
            postings, store.accounts, start, end, _display_currency(display_currency, store)
        )
    )
    return [MonthlyIncomeExpenseRow(**row) for row in rows.to_dicts()]


@router.get("/income-statement/spend-curve")
def get_spend_curve(
    month: date,
    *,
    lookback_months: int = 3,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
) -> list[SpendCurvePoint]:
    """Cumulative daily spend through one month, next to the average of the prior months.

    Returns
    -------
    list[SpendCurvePoint]
    """
    postings, store = _resolved_postings_for_aggregation(state.config, session)
    rows = collect_if_lazy(
        income_statement.spend_curve_vs_average(
            postings, store.accounts, month, lookback_months, _display_currency(display_currency, store)
        )
    )
    return [SpendCurvePoint(**row) for row in rows.to_dicts()]


@router.get("/budgets/comparison")
def get_budget_comparison(
    month: str,
    *,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
) -> list[BudgetComparisonRow]:
    """Every category budgeted for one month, actual spend next to the target.

    Returns
    -------
    list[BudgetComparisonRow]

    Raises
    ------
    HTTPException
        400 if `month` isn't `"YYYY-MM"`.
    """
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM form")
    postings, store = _resolved_postings_for_aggregation(state.config, session)
    rows = budgets.budget_comparison(
        postings, store.accounts, store.categories, store.budgets, month, _display_currency(display_currency, store)
    )
    return [BudgetComparisonRow(**vars(row)) for row in rows]


@router.get("/budgets/suggested-amount")
def get_suggested_budget_amount(
    category_id: str,
    month: str,
    *,
    lookback_months: int = 3,
    subcategory_id: str | None = None,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
) -> SuggestedBudgetAmount:
    """Suggest a budget for a category (or one subcategory of it) from its trailing months' actual spend.

    Returns
    -------
    SuggestedBudgetAmount

    Raises
    ------
    HTTPException
        400 if `month` isn't `"YYYY-MM"`.
    """
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM form")
    postings, store = _resolved_postings_for_aggregation(state.config, session)
    amount = budgets.suggested_budget_amount(
        postings,
        store.accounts,
        category_id,
        month,
        lookback_months,
        subcategory_id,
        _display_currency(display_currency, store),
    )
    return SuggestedBudgetAmount(suggested_amount=amount)
