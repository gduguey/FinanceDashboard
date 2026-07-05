"""Net worth: every real account's balance, grouped into assets and liabilities, plus manually-added assets.

Mirrors Maybe's `BalanceSheet` (see `ACCOUNTING_PLAN.md` Part 1) without
copying its code: accounts are grouped by classification rather than kept
as one flat list, and the one figure this module cannot compute itself —
what the tracked investment portfolio is worth — is passed in by the
caller rather than fetched here, keeping the only coupling between the two
packages one-directional and explicit (see `api.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from accounting.ledger.replay import account_balances

if TYPE_CHECKING:
    from datetime import date

    import polars as pl

    from accounting.models import Account, AccountKind, OtherAsset

_VIRTUAL_KINDS = {"income_source", "expense_payee"}
_LIABILITY_KINDS = {"credit_card", "loan"}


@dataclass(frozen=True)
class AccountBalanceRow:
    """One real account's current balance, ready to display."""

    account_id: str
    name: str
    kind: AccountKind
    parent_account_id: str | None
    balance_usd: float


@dataclass(frozen=True)
class NetWorthSummary:
    """Assets, liabilities, and net worth as of one date, with every account and manually-added asset behind it."""

    as_of: date
    assets_usd: float
    liabilities_usd: float
    other_assets_usd: float
    net_worth_usd: float
    accounts: list[AccountBalanceRow]
    other_assets: list[OtherAsset]


def net_worth_summary(
    postings: pl.DataFrame,
    accounts: dict[str, Account],
    other_assets: list[OtherAsset],
    as_of: date,
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
    no way to compute it and no business trying to.

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
            balance_usd=(
                external_investment_value_usd or 0.0
                if account.kind == "external_investment"
                else balance_by_account.get(account.account_id, 0.0)
            ),
        )
        for account in accounts.values()
        if account.kind not in _VIRTUAL_KINDS
    ]

    assets_usd = sum(row.balance_usd for row in rows if row.kind not in _LIABILITY_KINDS)
    liabilities_usd = sum(-row.balance_usd for row in rows if row.kind in _LIABILITY_KINDS)
    other_assets_usd = sum(asset.value_usd for asset in other_assets)

    return NetWorthSummary(
        as_of=as_of,
        assets_usd=assets_usd,
        liabilities_usd=liabilities_usd,
        other_assets_usd=other_assets_usd,
        net_worth_usd=assets_usd - liabilities_usd + other_assets_usd,
        accounts=sorted(rows, key=lambda row: row.name),
        other_assets=list(other_assets),
    )
