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

from accounting.ledger.currency import DisplayCurrency, convert
from accounting.ledger.replay import account_balances

if TYPE_CHECKING:
    from datetime import date

    import polars as pl

    from accounting.models import Account, AccountKind, CurrencyCode, OpeningBalance, OtherAsset

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
    display: DisplayCurrency = DisplayCurrency(),  # noqa: B008
    external_investment_value_usd: float | None = None,
    opening_balances: dict[str, OpeningBalance] | None = None,
) -> NetWorthSummary:
    """Assemble the full net-worth view: every real account's balance, grouped, plus manually-added assets.

    Virtual counterparty accounts (`income_source`/`expense_payee`,
    including the two uncategorized placeholders) are excluded entirely —
    their "balance" is just how much has passed through categorization,
    never money that is anywhere. An `external_investment` account whose
    `external_ref` is `"trades"` never gets its balance from `postings` at
    all; it comes from `external_investment_value_usd`, sourced by the
    caller from `trades.dashboard.overview_cards` (see `api.py`) since this
    module has no way to compute it and no business trying to — always
    treated as USD, since `trades` has no multi-currency concept of its
    own. An `external_investment` account with no `external_ref` is a
    manually-tracked one instead, and is valued the same way as any other
    account — from its postings plus its opening balance.

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
    display
        The currency (and rate table) the four aggregate totals are converted into.
    external_investment_value_usd
        The tracked investment portfolio's current value, or `None` if it
        isn't available (e.g. `trades` has never been synced) — treated as
        zero rather than raised on, since a missing investment value
        shouldn't block seeing the rest of net worth.
    opening_balances
        Manually-entered starting balances for accounts that already held
        money before their first posting (see `models.OpeningBalance`),
        keyed by `account_id`. Added on top of the posting-derived balance,
        but only once `as_of` reaches that balance's own `as_of_date`.

    Returns
    -------
    NetWorthSummary
        The full net-worth view, ready to serialize.
    """
    balances = cast("pl.DataFrame", account_balances(postings, as_of))
    balance_by_account = dict(zip(balances["account_id"].to_list(), balances["balance"].to_list(), strict=True))
    opening_balances = opening_balances or {}

    def base_balance(account: Account) -> float:
        if account.kind == "external_investment" and account.external_ref == "trades":
            return external_investment_value_usd or 0.0
        balance = balance_by_account.get(account.account_id, 0.0)
        opening = opening_balances.get(account.account_id)
        if opening is not None and as_of >= opening.as_of_date.date():
            balance += opening.amount
        return balance

    rows = [
        AccountBalanceRow(
            account_id=account.account_id,
            name=account.name,
            kind=account.kind,
            parent_account_id=account.parent_account_id,
            balance=base_balance(account),
            currency=account.currency,
        )
        for account in accounts.values()
        if account.kind not in _VIRTUAL_KINDS
    ]

    def to_display(amount: float, currency: CurrencyCode) -> float:
        return convert(amount, currency, display.code, display.rates_to_base)

    assets = sum(to_display(row.balance, row.currency) for row in rows if row.kind not in _LIABILITY_KINDS)
    liabilities = sum(-to_display(row.balance, row.currency) for row in rows if row.kind in _LIABILITY_KINDS)
    other_assets_total = sum(to_display(asset.value, asset.currency) for asset in other_assets)

    return NetWorthSummary(
        as_of=as_of,
        display_currency=display.code,
        assets=assets,
        liabilities=liabilities,
        other_assets_total=other_assets_total,
        net_worth=assets - liabilities + other_assets_total,
        accounts=sorted(rows, key=lambda row: row.name),
        other_assets=list(other_assets),
    )
