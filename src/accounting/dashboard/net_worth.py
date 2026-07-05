"""Net worth: every real account's balance, grouped into assets and liabilities, plus manually-added assets.

Mirrors Maybe's `BalanceSheet` (see `ACCOUNTING_PLAN.md` Part 1) without
copying its code: accounts are grouped by classification rather than kept
as one flat list, and the one figure this module cannot compute itself —
what the tracked investment portfolio is worth — is passed in by the
caller rather than fetched here, keeping the only coupling between the two
packages one-directional and explicit (see `api.py`).

Every account and manually-added asset keeps its own native currency in
`AccountBalanceRow`/`OtherAsset` — only the aggregate totals convert into
one `display_currency`, since a checking account's balance is a fact
(always the same number of dollars) while "what's that worth in euros" is
a question with a today's-rate-dependent answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from accounting.ledger.currency import convert
from accounting.ledger.replay import account_balances

if TYPE_CHECKING:
    from datetime import date

    import polars as pl

    from accounting.models import Account, AccountKind, CurrencyCode, OtherAsset

_VIRTUAL_KINDS = {"income_source", "expense_payee"}
_LIABILITY_KINDS = {"credit_card", "loan"}


@dataclass(frozen=True)
class AccountBalanceRow:
    """One real account's current balance, in its own currency, ready to display."""

    account_id: str
    name: str
    kind: AccountKind
    parent_account_id: str | None
    balance: float
    currency: CurrencyCode


@dataclass(frozen=True)
class NetWorthSummary:
    """Assets, liabilities, and net worth as of one date, with every account and manually-added asset behind it.

    The four totals are converted into `display_currency`; every row in
    `accounts`/`other_assets` stays in its own native currency.
    """

    as_of: date
    display_currency: CurrencyCode
    assets: float
    liabilities: float
    other_assets_total: float
    net_worth: float
    accounts: list[AccountBalanceRow]
    other_assets: list[OtherAsset]


def net_worth_summary(
    postings: pl.DataFrame,
    accounts: dict[str, Account],
    other_assets: list[OtherAsset],
    as_of: date,
    display_currency: CurrencyCode = "USD",
    eur_usd_rate: float = 1.08,
    external_investment_value_usd: float | None = None,
) -> NetWorthSummary:
    """Assemble the full net-worth view: every real account's balance, grouped, plus manually-added assets.

    Virtual counterparty accounts (`income_source`/`expense_payee`,
    including the two uncategorized placeholders) are excluded entirely —
    their "balance" is just how much has passed through categorization,
    never money that is anywhere. `external_investment` accounts never get
    their balance from `postings` at all; it comes from
    `external_investment_value_usd`, sourced by the caller from
    `trades.dashboard.overview_cards` (see `api.py`) since this module has
    no way to compute it and no business trying to — always treated as
    USD, since `trades` has no multi-currency concept of its own.

    Parameters
    ----------
    postings
        The full, resolved posting ledger.
    accounts
        Every known account, keyed by `account_id`.
    other_assets
        Manually-entered net-worth lines with no transaction history.
    as_of
        The date to value every account as of.
    display_currency
        The currency the four aggregate totals are converted into.
    eur_usd_rate
        How many US dollars one euro buys, for converting any account or
        asset whose own currency differs from `display_currency`.
    external_investment_value_usd
        The tracked investment portfolio's current value, or `None` if it
        isn't available (e.g. `trades` has never been synced) — treated as
        zero rather than raised on, since a missing investment value
        shouldn't block seeing the rest of net worth.

    Returns
    -------
    NetWorthSummary
        The full net-worth view, ready to serialize.
    """
    balances = cast("pl.DataFrame", account_balances(postings, as_of))
    balance_by_account = dict(zip(balances["account_id"].to_list(), balances["balance"].to_list(), strict=True))

    rows = [
        AccountBalanceRow(
            account_id=account.account_id,
            name=account.name,
            kind=account.kind,
            parent_account_id=account.parent_account_id,
            balance=(
                external_investment_value_usd or 0.0
                if account.kind == "external_investment"
                else balance_by_account.get(account.account_id, 0.0)
            ),
            currency=account.currency,
        )
        for account in accounts.values()
        if account.kind not in _VIRTUAL_KINDS
    ]

    def to_display(amount: float, currency: CurrencyCode) -> float:
        return convert(amount, currency, display_currency, eur_usd_rate)

    assets = sum(to_display(row.balance, row.currency) for row in rows if row.kind not in _LIABILITY_KINDS)
    liabilities = sum(-to_display(row.balance, row.currency) for row in rows if row.kind in _LIABILITY_KINDS)
    other_assets_total = sum(to_display(asset.value, asset.currency) for asset in other_assets)

    return NetWorthSummary(
        as_of=as_of,
        display_currency=display_currency,
        assets=assets,
        liabilities=liabilities,
        other_assets_total=other_assets_total,
        net_worth=assets - liabilities + other_assets_total,
        accounts=sorted(rows, key=lambda row: row.name),
        other_assets=list(other_assets),
    )


def net_worth_series(
    postings: pl.DataFrame,
    accounts: dict[str, Account],
    other_assets: list[OtherAsset],
    dates: list[date],
    display_currency: CurrencyCode = "USD",
    eur_usd_rate: float = 1.08,
    external_investment_value_usd: float | None = None,
) -> list[tuple[date, float]]:
    """Compute net worth as of every date in `dates`, for a net-worth-over-time chart.

    `external_investment_value_usd` and every `other_assets` entry are
    held constant across the whole series — neither has a tracked
    history here, only a current value, so every historical point uses
    today's figure for them rather than pretending to know the past.

    Parameters
    ----------
    postings
        The full, resolved posting ledger.
    accounts
        Every known account, keyed by `account_id`.
    other_assets
        Manually-entered net-worth lines, applied at today's value throughout.
    dates
        The dates to compute net worth as of.
    display_currency
        The currency each point is converted into.
    eur_usd_rate
        How many US dollars one euro buys.
    external_investment_value_usd
        Today's tracked investment value, held constant across the series.

    Returns
    -------
    list[tuple[datetime.date, float]]
        One `(date, net_worth)` pair per entry in `dates`, in the same order.
    """
    return [
        (
            day,
            net_worth_summary(
                postings, accounts, other_assets, day, display_currency, eur_usd_rate, external_investment_value_usd
            ).net_worth,
        )
        for day in dates
    ]
