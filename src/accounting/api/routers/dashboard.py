"""Read-only view endpoints — mirrors `accounting.dashboard.*`: income statement, interest, net worth, simulator.

The two budget reads that used to live here are in `routers.budgets`
instead. They have to be registered before `GET /budgets/{budget_id}`,
which matches `/budgets/comparison` on a GET just as well as a real budget
id does, and an ordering constraint spanning two router modules is only
visible to whoever happens to read `api.py`'s include order.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, cast

import polars as pl
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    CategoryTotalRow,
    InterestAccountRow,
    MonthlyIncomeExpenseRow,
    NetWorthAccountRow,
    NetWorthHistoryByAccountPoint,
    NetWorthHistoryPoint,
    NetWorthOtherAssetRow,
    NetWorthSummary,
    ProjectionPoint,
    SpendCurvePoint,
)
from accounting.api.dependencies import (
    _currencies_in_use,
    _display_currency,
    _flow_display_currency,
    _resolved_postings,
    _resolved_postings_for_aggregation,
)
from accounting.dashboard import income_statement, interest, simulator
from accounting.dashboard.net_worth import net_worth_summary
from accounting.ledger.currency import convert
from accounting.ledger.replay import account_balances_over_time
from accounting.models import VIRTUAL_ACCOUNT_KINDS, CurrencyCode
from accounting.repositories.accounts import load_opening_balances
from accounting.repositories.taxonomy import load_categories, load_other_assets
from accounting.taxonomy import seeded_accounts
from accounting.utils.io_utils import collect_if_lazy
from db.current_user import get_current_user_id
from db.money import to_analytics_float
from db.session import get_db

router = APIRouter()


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


def _external_investment_values(dates: list[date], session: Session, user_id: uuid.UUID) -> dict[date, float] | None:
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
    user_id
        Whose trades ledger to read.

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
    ledger = trades_main.load_ledger(session, user_id)
    if ledger.is_empty():
        return None
    first_event_date = cast("date", ledger["event_datetime"].dt.date().min())
    start = min(first_event_date, *dates)
    end = max(dates)
    price_lookup = make_price_lookup(trades_config)
    daily = cast("pl.DataFrame", daily_portfolio_values(ledger, price_lookup, start, end, trades_config))
    return dict(zip(daily["date"].to_list(), daily["value"].to_list(), strict=True))


def _benchmark_apy_pct(as_of: date, session: Session, user_id: uuid.UUID) -> float | None:
    """Look up `trades`'s published HYSA rate as of a date, as a percent, to compare vault/savings APYs against.

    Imported lazily, reading the *running* `trades.api` app's own
    `app.state.config` — the same reasoning as `_external_investment_values`.
    `session` is the same session this request's own route already holds —
    `accounting.*` and `trades.*` are separate Postgres schemas in one
    database, so one session can query both.

    Returns
    -------
    float or None
        The benchmark rate as a percent (e.g. `4.2`), or `None` if `trades` has no rate configured.
    """
    from trades import api as trades_api  # noqa: PLC0415
    from trades.dashboard.settings import hysa_rate_lookup  # noqa: PLC0415
    from trades.dashboard.settings import load_settings as load_trades_dashboard_settings  # noqa: PLC0415

    trades_config = trades_api.app.state.config
    try:
        trades_settings = load_trades_dashboard_settings(session, user_id)
        rate = hysa_rate_lookup(trades_config, trades_settings)(as_of)
    except Exception:  # noqa: BLE001 - a missing/misconfigured HYSA rate shouldn't block the rest of the view
        return None
    return rate * 100


@router.get("/interest-summary")
def get_interest_summary(
    *,
    as_of: date | None = None,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[InterestAccountRow]:
    """Every savings/vault account's year-to-date interest, current APY, balance, and a one-year projection.

    Returns
    -------
    list[InterestAccountRow]
    """
    postings = _resolved_postings(session, user_id)
    resolved_as_of = as_of or datetime.now(tz=UTC).date()
    rows = interest.interest_summary(
        postings,
        seeded_accounts(session, user_id),
        resolved_as_of,
        _benchmark_apy_pct(resolved_as_of, session, user_id),
    )
    return [InterestAccountRow(**vars(row)) for row in rows]


@router.get("/net-worth")
def get_net_worth(
    *,
    as_of: date | None = None,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> NetWorthSummary:
    """Return the full net-worth view: every account's balance, grouped, plus manually-added assets.

    Returns
    -------
    NetWorthSummary
    """
    postings = _resolved_postings(session, user_id)
    accounts = seeded_accounts(session, user_id)
    has_external_investment = any(account.broker_connection_id is not None for account in accounts.values())
    resolved_as_of = as_of or datetime.now(tz=UTC).date()
    external_values = (
        _external_investment_values([resolved_as_of], session, user_id) if has_external_investment else None
    )
    summary = net_worth_summary(
        postings,
        accounts,
        load_other_assets(session, user_id),
        resolved_as_of,
        _display_currency(display_currency, _currencies_in_use(session, user_id), resolved_as_of),
        external_investment_value=(external_values or {}).get(resolved_as_of) if external_values else None,
        opening_balances=load_opening_balances(session, user_id),
    )
    return NetWorthSummary(
        as_of=summary.as_of,
        display_currency=summary.display_currency,
        assets=summary.assets,
        liabilities=summary.liabilities,
        other_assets_total=summary.other_assets_total,
        net_worth=summary.net_worth,
        accounts=[NetWorthAccountRow(**vars(row)) for row in summary.accounts],
        other_assets=[
            NetWorthOtherAssetRow(
                asset_id=asset.asset_id,
                name=asset.name,
                # The one Decimal -> float crossing in this response, made
                # explicit rather than left to Pydantic's coercion: see
                # `NetWorthOtherAssetRow` for why this row is analytics.
                value=to_analytics_float(asset.value),
                currency=asset.currency,
                note=asset.note,
            )
            for asset in summary.other_assets
        ],
    )


@router.get("/net-worth/history")
def get_net_worth_history(
    start: date,
    end: date,
    *,
    interval_days: int = 1,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
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
    postings = _resolved_postings(session, user_id)
    accounts = seeded_accounts(session, user_id)
    other_assets = load_other_assets(session, user_id)
    opening_balances = load_opening_balances(session, user_id)
    currencies = _currencies_in_use(session, user_id)
    has_external_investment = any(account.broker_connection_id is not None for account in accounts.values())
    dates = pl.date_range(start, end, interval=f"{interval_days}d", eager=True).to_list()
    external_values = _external_investment_values(dates, session, user_id) if has_external_investment else None
    return [
        NetWorthHistoryPoint(
            date=day,
            net_worth=net_worth_summary(
                postings,
                accounts,
                other_assets,
                day,
                _display_currency(display_currency, currencies, day),
                external_investment_value=(external_values or {}).get(day, 0.0) if external_values else None,
                opening_balances=opening_balances,
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
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
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
    postings = _resolved_postings(session, user_id)
    opening_balances = load_opening_balances(session, user_id)
    currencies = _currencies_in_use(session, user_id)
    dates = pl.date_range(start, end, interval=f"{interval_days}d", eager=True).to_list()
    real_accounts = {
        account_id: account
        for account_id, account in seeded_accounts(session, user_id).items()
        if account.kind not in VIRTUAL_ACCOUNT_KINDS
    }
    has_external_investment = any(account.broker_connection_id is not None for account in real_accounts.values())
    external_values = _external_investment_values(dates, session, user_id) if has_external_investment else None

    balances = cast("pl.DataFrame", account_balances_over_time(postings, dates))
    balance_lookup = {(row["account_id"], row["date"]): row["balance"] for row in balances.to_dicts()}

    rows: list[NetWorthHistoryByAccountPoint] = []
    for day in dates:
        display = _display_currency(display_currency, currencies, day)
        for account_id, account in real_accounts.items():
            if account.broker_connection_id is not None:
                native = (external_values or {}).get(day, 0.0)
            else:
                native = balance_lookup.get((account_id, day), 0.0)
                opening = opening_balances.get(account_id)
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
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[CategoryTotalRow]:
    """Sum real income/expense postings by classification, category, and subcategory.

    Returns
    -------
    list[CategoryTotalRow]
    """
    postings = _resolved_postings_for_aggregation(session, user_id, since=start, until=end)
    parsed_account_ids = account_ids.split(",") if account_ids else None
    totals = collect_if_lazy(
        income_statement.category_totals(
            postings,
            seeded_accounts(session, user_id),
            load_categories(session, user_id),
            start,
            end,
            income_statement.Scope(parsed_account_ids, tag_id),
            _flow_display_currency(display_currency, _currencies_in_use(session, user_id)),
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
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[MonthlyIncomeExpenseRow]:
    """Sum real income and real expense per calendar month.

    Returns
    -------
    list[MonthlyIncomeExpenseRow]
    """
    postings = _resolved_postings_for_aggregation(session, user_id, since=start, until=end)
    rows = collect_if_lazy(
        income_statement.monthly_income_expense(
            postings,
            seeded_accounts(session, user_id),
            start,
            end,
            _flow_display_currency(display_currency, _currencies_in_use(session, user_id)),
        )
    )
    return [MonthlyIncomeExpenseRow(**row) for row in rows.to_dicts()]


@router.get("/income-statement/spend-curve")
def get_spend_curve(
    month: date,
    *,
    lookback_months: Annotated[int, Query(ge=1)] = 3,
    display_currency: CurrencyCode = "USD",
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> list[SpendCurvePoint]:
    """Cumulative daily spend through one month, next to the average of the prior months.

    Returns
    -------
    list[SpendCurvePoint]
    """
    since, until = income_statement.spend_curve_window(month, lookback_months)
    postings = _resolved_postings_for_aggregation(session, user_id, since=since, until=until)
    rows = collect_if_lazy(
        income_statement.spend_curve_vs_average(
            postings,
            seeded_accounts(session, user_id),
            month,
            lookback_months,
            _flow_display_currency(display_currency, _currencies_in_use(session, user_id)),
        )
    )
    return [SpendCurvePoint(**row) for row in rows.to_dicts()]
